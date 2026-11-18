package ru.mipt.ecom.commerce;

import java.net.URI;
import java.time.Duration;
import java.util.*;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.support.TransactionTemplate;
import org.springframework.web.multipart.MultipartFile;
import software.amazon.awssdk.auth.credentials.*;
import software.amazon.awssdk.core.checksums.*;
import software.amazon.awssdk.core.sync.RequestBody;
import software.amazon.awssdk.http.urlconnection.UrlConnectionHttpClient;
import software.amazon.awssdk.regions.Region;
import software.amazon.awssdk.services.s3.*;
import software.amazon.awssdk.services.s3.model.*;

@Service
class MediaService {
    final java.util.concurrent.Semaphore uploads=new java.util.concurrent.Semaphore(2);
    final JdbcTemplate db; final TransactionTemplate tx; final DomainEvents events;
    final S3Client s3; final String bucket,publicBase;
    MediaService(JdbcTemplate db,TransactionTemplate tx,DomainEvents events,
        @Value("${media.endpoint:http://localhost:13900}") String endpoint,@Value("${media.access-key:GK000000000000000000000000}") String access,
        @Value("${media.secret-key:local-garage-secret}") String secret,@Value("${media.bucket:product-images}") String bucket,
        @Value("${media.public-base:http://localhost:18086}") String publicBase) {
        this.db=db; this.tx=tx; this.events=events; this.bucket=bucket; this.publicBase=publicBase;
        s3=S3Client.builder().endpointOverride(URI.create(endpoint)).region(Region.of("garage"))
            .credentialsProvider(StaticCredentialsProvider.create(AwsBasicCredentials.create(access,secret)))
            .httpClientBuilder(UrlConnectionHttpClient.builder().connectionTimeout(Duration.ofMillis(500)).socketTimeout(Duration.ofSeconds(3)))
            .overrideConfiguration(c -> c.apiCallTimeout(Duration.ofSeconds(4)).apiCallAttemptTimeout(Duration.ofSeconds(3)).retryStrategy(software.amazon.awssdk.retries.StandardRetryStrategy.builder().maxAttempts(1).build()))
            .requestChecksumCalculation(RequestChecksumCalculation.WHEN_REQUIRED).responseChecksumValidation(ResponseChecksumValidation.WHEN_REQUIRED)
            .serviceConfiguration(S3Configuration.builder().pathStyleAccessEnabled(true).chunkedEncodingEnabled(false).build()).build();
    }
    static String extension(byte[] bytes,String type) {
        boolean png=bytes.length>=8 && Arrays.equals(Arrays.copyOf(bytes,8),new byte[]{(byte)137,80,78,71,13,10,26,10});
        boolean jpeg=bytes.length>=3 && bytes[0]==(byte)255 && bytes[1]==(byte)216 && bytes[2]==(byte)255;
        boolean webp=bytes.length>=12 && new String(bytes,0,4,java.nio.charset.StandardCharsets.US_ASCII).equals("RIFF") && new String(bytes,8,4,java.nio.charset.StandardCharsets.US_ASCII).equals("WEBP");
        if(png && "image/png".equals(type)) return "png";
        if(jpeg && "image/jpeg".equals(type)) return "jpg";
        if(webp && "image/webp".equals(type)) return "webp";
        throw new CommerceService.ApiFailure(400,"INVALID_IMAGE");
    }
    Map<String,Object> upload(long product,MultipartFile file) throws java.io.IOException {
        if(file.getSize()==0 || file.getSize()>2*1024*1024) throw new CommerceService.ApiFailure(400,"IMAGE_SIZE_LIMIT");
        if(!uploads.tryAcquire()) throw new CommerceService.ApiFailure(503,"MEDIA_BUSY");
        try { return uploadBytes(product,file.getBytes(),file.getContentType()); }
        finally { uploads.release(); }
    }
    private Map<String,Object> uploadBytes(long product,byte[] bytes,String type) {
        if(bytes.length==0 || bytes.length>2*1024*1024) throw new CommerceService.ApiFailure(400,"IMAGE_SIZE_LIMIT");
        String key="products/"+product+"/"+UUID.randomUUID()+"."+extension(bytes,type);
        tx.executeWithoutResult(status -> {
            if(db.queryForObject("SELECT count(*) FROM products WHERE id=?",Integer.class,product)==0) throw new CommerceService.ApiFailure(404,"PRODUCT_NOT_FOUND");
            db.update("INSERT INTO media_uploads(object_key,product_id,state) VALUES(?,?,'PENDING')",key,product);
        });
        s3.putObject(PutObjectRequest.builder().bucket(bucket).key(key).contentType(type).cacheControl("public,max-age=31536000,immutable").build(),RequestBody.fromBytes(bytes));
        tx.executeWithoutResult(status -> {
            String state=db.queryForObject("SELECT state FROM media_uploads WHERE object_key=? FOR UPDATE",String.class,key);
            if(!state.equals("PENDING")) throw new CommerceService.ApiFailure(409,"UPLOAD_EXPIRED");
            db.queryForObject("SELECT id FROM products WHERE id=? FOR UPDATE",Long.class,product);
            db.update("INSERT INTO product_images VALUES(?,?,?,?)",product,key,type,bytes.length);
            db.update("UPDATE media_uploads SET state='READY' WHERE object_key=?",key);
            events.product(product,"ProductUpdated");
        });
        return Map.of("objectKey",key,"url",publicBase+"/"+key,"sizeBytes",bytes.length);
    }
    void cleanup() {
        var keys=tx.execute(status -> db.queryForList("""
            WITH picked AS (SELECT object_key FROM media_uploads WHERE state<>'READY' AND created_at<now()-interval '1 hour' AND next_cleanup<=now() ORDER BY next_cleanup,object_key LIMIT 100 FOR UPDATE SKIP LOCKED)
            UPDATE media_uploads m SET state='CLEANUP',next_cleanup=now()+interval '5 minutes' FROM picked WHERE m.object_key=picked.object_key RETURNING m.object_key
            """,String.class));
        // Keep cleanup intents: a PUT that completed after a previous DELETE is removed on the next sweep.
        for(String key:keys) s3.deleteObject(DeleteObjectRequest.builder().bucket(bucket).key(key).build());
    }
}
