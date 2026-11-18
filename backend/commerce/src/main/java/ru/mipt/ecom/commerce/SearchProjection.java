package ru.mipt.ecom.commerce;

import com.fasterxml.jackson.databind.*;
import java.net.URI;
import java.net.http.*;
import java.time.Duration;
import java.util.*;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Component;
import org.springframework.transaction.support.TransactionTemplate;

@Component
class SearchProjection {
    final JdbcTemplate db;
    final TransactionTemplate tx;
    final DomainEvents events;
    final ObjectMapper json;
    final String url,mediaBase;
    final HttpClient http=HttpClient.newBuilder().connectTimeout(Duration.ofMillis(500)).build();
    SearchProjection(JdbcTemplate db,TransactionTemplate tx,DomainEvents events,ObjectMapper json,@Value("${search.url:http://localhost:19200}") String url,@Value("${media.public-base:http://localhost:18086}") String mediaBase) {
        this.db=db; this.tx=tx; this.events=events; this.json=json; this.url=url; this.mediaBase=mediaBase;
    }
    JsonNode call(String method,String path,String body) {
        try {
            var req=HttpRequest.newBuilder(URI.create(url+path)).timeout(Duration.ofSeconds(5)).header("Content-Type",path.startsWith("/_bulk")?"application/x-ndjson":"application/json")
                .method(method,body==null?HttpRequest.BodyPublishers.noBody():HttpRequest.BodyPublishers.ofString(body)).build();
            var response=http.send(req,HttpResponse.BodyHandlers.ofString());
            if(response.statusCode()/100!=2) throw new IllegalStateException("OpenSearch HTTP "+response.statusCode()+" "+response.body().substring(0,Math.min(200,response.body().length())));
            return json.readTree(response.body());
        } catch(InterruptedException e) { Thread.currentThread().interrupt(); throw new IllegalStateException(e); }
        catch(Exception e) { throw new IllegalStateException(e); }
    }
    List<Map<String,Object>> documents(long after,int limit,Long exact) {
        return db.queryForList("""
            SELECT p.id,p.version,p.active,p.name,p.description,p.category_id AS "categoryId",
              pr.price_minor AS "priceMinor",i.on_hand-i.reserved AS available,
              ARRAY(SELECT ? || '/' || object_key FROM product_images WHERE product_id=p.id ORDER BY object_key) AS images
            FROM products p JOIN product_prices pr ON pr.product_id=p.id JOIN inventory i ON i.product_id=p.id
            WHERE p.id>? AND (?::bigint IS NULL OR p.id=?::bigint) ORDER BY p.id LIMIT ?
            """,mediaBase,after,exact,exact,limit).stream().map(row -> {
                try { row.put("images",((java.sql.Array)row.get("images")).getArray()); return row; }
                catch(Exception e) { throw new IllegalStateException(e); }
            }).toList();
    }
    void bulk(String index,List<Map<String,Object>> docs) {
        if(docs.isEmpty()) return;
        StringBuilder body=new StringBuilder();
        for(var doc:docs) {
            body.append(events.encode(Map.of("index",Map.of("_index",index,"_id",doc.get("id").toString(),"version",doc.get("version"),"version_type","external_gte")))).append('\n');
            body.append(events.encode(doc)).append('\n');
        }
        var response=call("POST","/_bulk",body.toString());
        for(var item:response.path("items")) {
            int status=item.path("index").path("status").asInt();
            if(status/100!=2 && status!=409) throw new IllegalStateException("Search bulk item failed: "+item);
        }
    }
    void project(long id) {
        var targets=db.queryForList("SELECT name FROM search_indexes WHERE state IN ('BUILDING','LIVE')",String.class);
        if(targets.isEmpty()) throw new IllegalStateException("Search bootstrap pending");
        var docs=documents(0,1,id);
        for(String index:targets) bulk(index,docs);
    }
    Map<String,Object> requestRebuild() {
        return tx.execute(status -> {
            UUID id=UUID.randomUUID(); String index="products-"+id;
            db.update("INSERT INTO search_rebuilds(id,index_name) VALUES(?,?) ON CONFLICT DO NOTHING",id,index);
            return db.queryForMap("SELECT id,state,cursor_id AS \"cursorId\",index_name AS \"indexName\" FROM search_rebuilds WHERE state<>'DONE'");
        });
    }
    void rebuildStep() {
        if(db.queryForObject("SELECT count(*) FROM search_rebuilds",Integer.class)==0) requestRebuild();
        var jobs=tx.execute(status -> db.queryForList("""
            WITH picked AS (SELECT id FROM search_rebuilds WHERE state<>'DONE' AND (lease_until IS NULL OR lease_until<clock_timestamp()) ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED)
            UPDATE search_rebuilds j SET state='RUNNING',lease_until=clock_timestamp()+interval '30 seconds' FROM picked WHERE j.id=picked.id RETURNING j.*
            """));
        if(jobs.isEmpty()) return;
        var job=jobs.getFirst(); String index=job.get("index_name").toString();
        try {
            if(db.queryForObject("SELECT count(*) FROM search_indexes WHERE name=?",Integer.class,index)==0) {
                // Idempotent create, including restart after create-before-register.
                try { call("PUT","/"+index,"""
                    {"settings":{"number_of_shards":1,"number_of_replicas":0,"refresh_interval":"1s"},
                     "mappings":{"dynamic":"strict","properties":{
                       "id":{"type":"long"},"version":{"type":"long"},"active":{"type":"boolean"},
                       "name":{"type":"text","analyzer":"russian"},"description":{"type":"text","analyzer":"russian"},
                       "categoryId":{"type":"long"},"priceMinor":{"type":"long"},"available":{"type":"integer"},"images":{"type":"keyword","index":false}}}}
                    """); }
                catch(IllegalStateException e) { call("GET","/"+index,null); }
                db.update("INSERT INTO search_indexes(name,state) VALUES(?,'BUILDING') ON CONFLICT DO NOTHING",index);
            }
            var docs=documents(((Number)job.get("cursor_id")).longValue(),500,null);
            bulk(index,docs);
            if(!docs.isEmpty()) db.update("UPDATE search_rebuilds SET cursor_id=?,lease_until=NULL WHERE id=?",docs.getLast().get("id"),job.get("id"));
            else {
                call("POST","/"+index+"/_refresh","{}");
                call("POST","/_aliases",events.encode(Map.of("actions",List.of(Map.of("remove",Map.of("index","products-*","alias","products","must_exist",false)),Map.of("add",Map.of("index",index,"alias","products"))))));
                tx.executeWithoutResult(status -> {
                    db.update("UPDATE search_indexes SET state='RETIRED' WHERE state='LIVE' AND name<>?",index);
                    db.update("UPDATE search_indexes SET state='LIVE' WHERE name=?",index);
                    db.update("UPDATE search_rebuilds SET state='DONE',lease_until=NULL WHERE id=?",job.get("id"));
                });
            }
        } catch(RuntimeException e) {
            db.update("UPDATE search_rebuilds SET lease_until=clock_timestamp()+interval '5 seconds' WHERE id=?",job.get("id"));
            throw e;
        }
    }
}
