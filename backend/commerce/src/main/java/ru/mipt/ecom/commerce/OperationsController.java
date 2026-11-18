package ru.mipt.ecom.commerce;

import java.util.*;
import org.springframework.context.annotation.Profile;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.multipart.MultipartFile;
import org.springframework.jdbc.core.JdbcTemplate;

@RestController
@Profile("!worker")
class OperationsController {
    final MediaService media; final SearchProjection search; final BrokerGateway broker; final JdbcTemplate db;
    OperationsController(MediaService media,SearchProjection search,BrokerGateway broker,JdbcTemplate db) { this.media=media; this.search=search; this.broker=broker; this.db=db; }
    @PostMapping("/api/admin/products/{id}/images") Object upload(@PathVariable long id,@RequestParam MultipartFile file) throws Exception { return media.upload(id,file); }
    @PostMapping("/api/admin/search/rebuilds") Object rebuild() { return search.requestRebuild(); }
    @GetMapping("/api/admin/search/rebuilds") Object jobs() { return db.queryForList("SELECT id,index_name,state,cursor_id,created_at FROM search_rebuilds ORDER BY created_at DESC LIMIT 20"); }
    @PostMapping("/api/admin/queues/{queue}/replay") Object replay(@PathVariable String queue,@RequestParam(defaultValue="100") int limit) throws Exception { return Map.of("replayed",broker.replay(queue,Math.clamp(limit,1,1000))); }
    @GetMapping("/api/admin/background-stats") Object stats() {
        return db.queryForMap("""
            SELECT (SELECT count(*) FROM outbox_events WHERE published_at IS NULL) AS outbox_pending,
              (SELECT extract(epoch FROM clock_timestamp()-min(created_at)) FROM outbox_events WHERE published_at IS NULL) AS oldest_event_seconds,
              (SELECT count(*) FROM payments WHERE state='REFUND_PENDING') AS refund_pending,
              (SELECT count(*) FROM payments WHERE state='PENDING') AS payments_pending,
              (SELECT count(*) FROM media_uploads WHERE state='PENDING') AS media_pending
            """);
    }
}
