package ru.mipt.ecom.commerce;

import org.springframework.context.annotation.Profile;

import jakarta.validation.Valid;
import jakarta.validation.constraints.*;
import java.time.OffsetDateTime;
import java.util.Map;
import java.util.UUID;
import org.springframework.http.ResponseEntity;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.web.bind.annotation.*;

@RestController
@Profile("!worker")
class CommerceController {
    record Quantity(@Min(0) @Max(100) int quantity) {}
    record Checkout(@Min(0) long expectedCartVersion,@Min(0) long expectedTotalMinor) {}
    record ProductUpdate(@NotBlank @Size(max=200) String name,@Min(1) @Max(100000000000L) long priceMinor,boolean active) {}
    record Stock(@Min(0) @Max(100000000) int onHand) {}
    private final CommerceService service;
    private final ProductCache cache;
    CommerceController(CommerceService service,ProductCache cache) { this.service=service; this.cache=cache; }
    @GetMapping("/api/products") Object products(@RequestParam(defaultValue="1") long categoryId,@RequestParam(defaultValue="0") long afterId,@RequestParam(defaultValue="20") int limit) { return service.category(categoryId,afterId,limit); }
    @GetMapping("/api/products/{id}") Object product(@PathVariable long id) { return service.card(id); }
    @GetMapping("/api/cart") Object cart(@AuthenticationPrincipal SecurityConfig.StoreUser user) { return service.cart(user.id); }
    @PutMapping("/api/cart/items/{id}") Object cartItem(@AuthenticationPrincipal SecurityConfig.StoreUser user,@PathVariable long id,@Valid @RequestBody Quantity body) { return service.setCart(user.id,id,body.quantity()); }
    @PostMapping("/api/orders") ResponseEntity<?> checkout(@AuthenticationPrincipal SecurityConfig.StoreUser user,@RequestHeader("Idempotency-Key") String key,@Valid @RequestBody Checkout body) {
        var result=service.checkout(user.id,key,body.expectedCartVersion(),body.expectedTotalMinor());
        return ResponseEntity.status(result.status()).body(result.body());
    }
    @GetMapping("/api/orders") Object orders(@AuthenticationPrincipal SecurityConfig.StoreUser user,@RequestParam(required=false) OffsetDateTime beforeCreatedAt,@RequestParam(required=false) UUID beforeId) { return service.orders(user.id,beforeCreatedAt,beforeId); }
    @GetMapping("/api/orders/{id}") Object order(@AuthenticationPrincipal SecurityConfig.StoreUser user,@PathVariable UUID id) { return service.order(user.id,id); }
    @PutMapping("/api/admin/products/{id}") Object update(@PathVariable long id,@Valid @RequestBody ProductUpdate body) { return service.updateProduct(id,body.name(),body.priceMinor(),body.active()); }
    @PutMapping("/api/admin/inventory/{id}") Object stock(@PathVariable long id,@Valid @RequestBody Stock body) { return service.updateStock(id,body.onHand()); }
    @PostMapping("/api/admin/reservations/expire") Object expire() { return service.expire(); }
    @GetMapping("/api/admin/cache-stats") Object stats() { return cache.stats(); }
}

@RestControllerAdvice
class ErrorHandler {
    @ExceptionHandler(org.springframework.dao.DataAccessResourceFailureException.class)
    ResponseEntity<?> unavailable(Exception error) { return ResponseEntity.status(503).body(Map.of("code","DATABASE_UNAVAILABLE")); }
    @ExceptionHandler(CommerceService.ApiFailure.class)
    ResponseEntity<?> business(CommerceService.ApiFailure error) { return ResponseEntity.status(error.status).body(Map.of("code",error.code)); }
    @ExceptionHandler(software.amazon.awssdk.core.exception.SdkException.class)
    ResponseEntity<?> media(Exception error) { return ResponseEntity.status(503).body(Map.of("code","MEDIA_UNAVAILABLE")); }
    @ExceptionHandler(org.springframework.dao.TransientDataAccessException.class)
    ResponseEntity<?> busy(Exception error) { return ResponseEntity.status(503).body(Map.of("code","DATABASE_BUSY")); }
}
