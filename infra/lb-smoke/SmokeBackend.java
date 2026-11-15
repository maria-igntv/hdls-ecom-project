import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicBoolean;

// Diagnostic fixture only. Business endpoints are implemented in later stages.
public class SmokeBackend {
    public static void main(String[] args) throws Exception {
        String instance = System.getenv().getOrDefault("INSTANCE_ID", "local");
        AtomicBoolean ready = new AtomicBoolean(true);
        var executor = Executors.newVirtualThreadPerTaskExecutor();
        var server = HttpServer.create(new InetSocketAddress(8080), 128);
        server.setExecutor(executor);
        server.createContext("/", exchange -> {
            String path = exchange.getRequestURI().getPath();
            boolean isReady = ready.get() && !Files.exists(Path.of("/tmp/not-ready"));
            int status = path.equals("/readyz") && !isReady ? 503 : 200;
            if (path.equals("/api/slow")) {
                try {
                    Thread.sleep(1200);
                } catch (InterruptedException exception) {
                    Thread.currentThread().interrupt();
                    status = 503;
                }
            }
            respond(exchange, status, "{\"instance\":\"" + instance + "\"}");
        });
        Runtime.getRuntime().addShutdownHook(new Thread(() -> {
            ready.set(false);
            try {
                // Allow three readiness checks to remove this instance first.
                Thread.sleep(8000);
            } catch (InterruptedException exception) {
                Thread.currentThread().interrupt();
            }
            server.stop(2);
            executor.shutdown();
        }));
        server.start();
        System.out.println("Started " + instance);
    }

    private static void respond(HttpExchange exchange, int status, String body) throws java.io.IOException {
        byte[] bytes = body.getBytes(StandardCharsets.UTF_8);
        exchange.getResponseHeaders().set("Content-Type", "application/json");
        exchange.sendResponseHeaders(status, bytes.length);
        try (var output = exchange.getResponseBody()) {
            output.write(bytes);
        }
    }
}
