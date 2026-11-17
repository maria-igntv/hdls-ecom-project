package ru.mipt.ecom.commerce;

import com.fasterxml.jackson.databind.ObjectMapper;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URI;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.ApplicationArguments;
import org.springframework.boot.ApplicationRunner;
import org.springframework.boot.web.servlet.context.ServletWebServerApplicationContext;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Component;

@Component
class StartupWarmup implements ApplicationRunner {
    private static final Logger LOG = LoggerFactory.getLogger(StartupWarmup.class);
    private final JdbcTemplate db;
    private final CommerceService service;
    private final ObjectMapper json;
    private final ServletWebServerApplicationContext context;

    StartupWarmup(JdbcTemplate db, CommerceService service, ObjectMapper json, ServletWebServerApplicationContext context) {
        this.db = db;
        this.service = service;
        this.json = json;
        this.context = context;
    }

    @Override
    public void run(ApplicationArguments args) throws Exception {
        long started = System.nanoTime();
        var products = db.queryForList("SELECT id FROM products WHERE active ORDER BY id LIMIT 1000", Long.class);
        int warmed = 0;
        for (long id : products) {
            try {
                json.writeValueAsBytes(service.card(id));
                warmed++;
            } catch (CommerceService.ApiFailure failure) {
                // A product can be unpublished while this instance starts.
                if (failure.status != 404) throw failure;
            }
        }
        for (long category : db.queryForList("SELECT id FROM categories ORDER BY id LIMIT 20", Long.class)) {
            json.writeValueAsBytes(service.category(category, 0, 20));
        }
        if (!products.isEmpty()) {
            String base = "http://127.0.0.1:" + context.getWebServer().getPort();
            for (int request = 0; request < 1000; request++) {
                String path = "/api/products/" + products.get(request % products.size());
                if (request % 2 == 0) path = "/api/products?categoryId=" + (1 + request % 20) + "&limit=20";
                var connection = (HttpURLConnection) URI.create(base + path).toURL().openConnection();
                connection.setConnectTimeout(2000);
                connection.setReadTimeout(5000);
                try (var body = connection.getInputStream()) {
                    body.transferTo(OutputStream.nullOutputStream());
                }
            }
        }
        LOG.info("Startup warmup loaded {} cards in {} ms", warmed, (System.nanoTime() - started) / 1_000_000);
    }
}
