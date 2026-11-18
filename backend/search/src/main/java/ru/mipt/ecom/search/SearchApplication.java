package ru.mipt.ecom.search;

import com.fasterxml.jackson.databind.*;
import java.net.URI;
import java.net.http.*;
import java.time.Duration;
import java.util.*;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

@SpringBootApplication
@RestController
public class SearchApplication {
    final String url;
    final ObjectMapper json;
    final HttpClient http=HttpClient.newBuilder().connectTimeout(Duration.ofMillis(300)).build();
    public SearchApplication(@Value("${SEARCH_URL:http://localhost:19200}") String url,ObjectMapper json) { this.url=url; this.json=json; }
    public static void main(String[] args) { SpringApplication.run(SearchApplication.class,args); }
    @GetMapping("/livez") Object live() { return Map.of("status","UP"); }
    @GetMapping("/readyz") ResponseEntity<?> ready() {
        try { call("GET","/products/_count",null); return ResponseEntity.ok(Map.of("status","UP")); }
        catch(Exception e) { return ResponseEntity.status(503).body(Map.of("status","OUT_OF_SERVICE")); }
    }
    @GetMapping("/api/search") Object search(@RequestParam(defaultValue="") String q,@RequestParam(required=false) Long categoryId,
        @RequestParam(required=false) Long minPriceMinor,@RequestParam(required=false) Long maxPriceMinor,
        @RequestParam(defaultValue="false") boolean inStock,@RequestParam(defaultValue="0") int offset,@RequestParam(defaultValue="20") int limit) throws Exception {
        if(q.length()>200 || offset<0 || offset>1000 || limit<1 || limit>100 || minPriceMinor!=null && minPriceMinor<0 || maxPriceMinor!=null && maxPriceMinor<0 || minPriceMinor!=null && maxPriceMinor!=null && minPriceMinor>maxPriceMinor) throw new BadQuery();
        List<Object> filters=new ArrayList<>(); filters.add(Map.of("term",Map.of("active",true)));
        if(categoryId!=null) filters.add(Map.of("term",Map.of("categoryId",categoryId)));
        if(inStock) filters.add(Map.of("range",Map.of("available",Map.of("gt",0))));
        Map<String,Long> price=new HashMap<>(); if(minPriceMinor!=null) price.put("gte",minPriceMinor); if(maxPriceMinor!=null) price.put("lte",maxPriceMinor);
        if(!price.isEmpty()) filters.add(Map.of("range",Map.of("priceMinor",price)));
        Object match=q.isBlank()?Map.of("match_all",Map.of()):Map.of("multi_match",Map.of("query",q,"fields",List.of("name^3","description")));
        var result=call("POST","/products/_search",json.writeValueAsString(Map.of("from",offset,"size",limit,"track_total_hits",true,"query",Map.of("bool",Map.of("must",match,"filter",filters)),"sort",List.of("_score",Map.of("id","asc")))));
        List<JsonNode> items=new ArrayList<>(); result.path("hits").path("hits").forEach(hit->items.add(hit.path("_source")));
        return Map.of("total",result.path("hits").path("total").path("value").asLong(),"items",items,"offset",offset,"limit",limit);
    }
    JsonNode call(String method,String path,String body) throws Exception {
        var req=HttpRequest.newBuilder(URI.create(url+path)).timeout(Duration.ofSeconds(2)).header("Content-Type","application/json")
            .method(method,body==null?HttpRequest.BodyPublishers.noBody():HttpRequest.BodyPublishers.ofString(body)).build();
        var response=http.send(req,HttpResponse.BodyHandlers.ofString());
        if(response.statusCode()/100!=2) throw new IllegalStateException("Search unavailable");
        return json.readTree(response.body());
    }
    static class BadQuery extends RuntimeException {}
    @ExceptionHandler(BadQuery.class) ResponseEntity<?> badQuery() { return ResponseEntity.badRequest().body(Map.of("code","INVALID_SEARCH_QUERY")); }
    @ExceptionHandler({java.io.IOException.class,InterruptedException.class,IllegalStateException.class}) ResponseEntity<?> unavailable(Exception e) {
        if(e instanceof InterruptedException) Thread.currentThread().interrupt();
        return ResponseEntity.status(503).body(Map.of("code","SEARCH_UNAVAILABLE"));
    }
}
