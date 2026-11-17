import java.nio.file.Path;
import java.util.HashMap;
import java.util.Map;
import jdk.jfr.consumer.RecordedEvent;
import jdk.jfr.consumer.RecordingFile;

/** Stream a recording without materializing its events or stack traces as JSON. */
class JfrSummary {
    public static void main(String[] args) throws Exception {
        Map<String, Long> counts = new HashMap<>();
        Map<String, Long> allocations = new HashMap<>();
        Map<String, Long> execution = new HashMap<>();
        Map<String, Long> causes = new HashMap<>();
        double longestPause = 0;
        double totalPause = 0;
        try (var recording = new RecordingFile(Path.of(args[0]))) {
            while (recording.hasMoreEvents()) {
                RecordedEvent event = recording.readEvent();
                String name = event.getEventType().getName();
                switch (name) {
                    case "jdk.GarbageCollection" -> {
                        counts.merge(name, 1L, Long::sum);
                        longestPause = Math.max(longestPause, event.getDuration("longestPause").toNanos() / 1_000_000.0);
                        totalPause += event.getDuration("sumOfPauses").toNanos() / 1_000_000.0;
                        causes.merge(event.getString("cause"), 1L, Long::sum);
                    }
                    case "jdk.ObjectAllocationSample" -> {
                        counts.merge(name, 1L, Long::sum);
                        allocations.merge(event.getClass("objectClass").getName(), event.getLong("weight"), Long::sum);
                    }
                    case "jdk.ExecutionSample" -> {
                        counts.merge(name, 1L, Long::sum);
                        var trace = event.getStackTrace();
                        if (trace != null && !trace.getFrames().isEmpty()) {
                            var method = trace.getFrames().getFirst().getMethod();
                            execution.merge(method.getType().getName() + "." + method.getName(), 1L, Long::sum);
                        }
                    }
                    default -> { }
                }
            }
        }
        System.out.println("metric\tgc_longest_pause_ms\t" + longestPause);
        System.out.println("metric\tgc_total_pause_ms\t" + totalPause);
        System.out.println("metric\tsampled_allocation_weight_bytes\t" + allocations.values().stream().mapToLong(Long::longValue).sum());
        print("count", counts, counts.size());
        print("cause", causes, causes.size());
        print("allocation", allocations, 8);
        print("execution", execution, 10);
    }

    private static void print(String kind, Map<String, Long> values, int limit) {
        values.entrySet().stream()
            .sorted(Map.Entry.<String, Long>comparingByValue().reversed())
            .limit(limit)
            .forEach(entry -> System.out.println(kind + "\t" + entry.getKey() + "\t" + entry.getValue()));
    }
}
