package ru.mipt.ecom.commerce;

import org.springframework.context.annotation.Profile;

import java.util.Map;
import org.springframework.boot.availability.ApplicationAvailability;
import org.springframework.boot.availability.ReadinessState;
import org.springframework.dao.DataAccessException;
import org.springframework.http.ResponseEntity;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@Profile("!worker")
class ProbeController {
    private final ApplicationAvailability availability;
    private final JdbcTemplate db;

    ProbeController(ApplicationAvailability availability, JdbcTemplate db) {
        this.availability = availability;
        this.db = db;
    }

    @GetMapping("/livez")
    Object live() {
        return Map.of("status", "UP");
    }

    @GetMapping("/readyz")
    ResponseEntity<?> ready() {
        if (availability.getReadinessState() != ReadinessState.ACCEPTING_TRAFFIC) {
            return ResponseEntity.status(503).body(Map.of("status", "OUT_OF_SERVICE"));
        }
        try {
            db.queryForObject("SELECT 1", Integer.class);
            return ResponseEntity.ok(Map.of("status", "UP"));
        } catch (DataAccessException failure) {
            return ResponseEntity.status(503).body(Map.of("status", "OUT_OF_SERVICE"));
        }
    }
}
