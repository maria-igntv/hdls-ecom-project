package ru.mipt.ecom.commerce;

import com.fasterxml.jackson.databind.ObjectMapper;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.time.Instant;
import java.util.*;
import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Profile;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

@RestController
@Profile("!worker")
class PaymentController {
    record Callback(UUID eventId,UUID paymentId) {}
    final PaymentService service;
    final ObjectMapper json;
    final String secret;
    PaymentController(PaymentService service,ObjectMapper json,@Value("${payment.webhook-secret:local-webhook-secret}") String secret) { this.service=service; this.json=json; this.secret=secret; }
    @PostMapping("/api/orders/{id}/payments") ResponseEntity<?> pay(@AuthenticationPrincipal SecurityConfig.StoreUser user,@PathVariable UUID id) { return ResponseEntity.accepted().body(service.initiate(user.id,id)); }
    @GetMapping("/api/orders/{id}/payments") Object get(@AuthenticationPrincipal SecurityConfig.StoreUser user,@PathVariable UUID id) { return service.view(user.id,id); }
    @PostMapping("/api/payments/webhook") Object webhook(@RequestHeader("X-Payment-Timestamp") long timestamp,@RequestHeader("X-Payment-Signature") String signature,@RequestBody byte[] body) throws Exception {
        if(body.length>2048 || Math.abs(Instant.now().getEpochSecond()-timestamp)>300 || !verify(secret,timestamp,body,signature)) throw new CommerceService.ApiFailure(401,"INVALID_WEBHOOK_SIGNATURE");
        var callback=json.readValue(body,Callback.class);
        if(callback.eventId()==null || callback.paymentId()==null) throw new CommerceService.ApiFailure(400,"INVALID_CALLBACK");
        service.webhook(callback.eventId(),callback.paymentId(),HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(body)));
        return Map.of("accepted",true);
    }
    static boolean verify(String secret,long timestamp,byte[] body,String signature) {
        try {
            var mac=Mac.getInstance("HmacSHA256"); mac.init(new SecretKeySpec(secret.getBytes(StandardCharsets.UTF_8),"HmacSHA256"));
            mac.update((timestamp+".").getBytes(StandardCharsets.UTF_8));
            return MessageDigest.isEqual(mac.doFinal(body),HexFormat.of().parseHex(signature));
        } catch(Exception e) { return false; }
    }
}
