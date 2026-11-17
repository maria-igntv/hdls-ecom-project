#!/usr/bin/env python3
"""Open-arrival k6 load, container/JVM/SQL measurements and JFR in an owned stack."""
import argparse
import base64
import csv
import hashlib
import io
import json
import os
import re
import socket
import statistics
import subprocess
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "artifacts/stage3"
PROJECT = "hdls-stage3-check-" + uuid.uuid4().hex[:8]
COMPOSE = ["docker", "compose", "-p", PROJECT,
           "-f", str(ROOT / "infra/stage2/compose.yaml"),
           "-f", str(ROOT / "infra/stage3/compose.yaml")]
PROFILES = {
    "warmup": (521, 90, 2),
    "baseline": (200, 60, 2),
    "target": (521, 120, 2),
    "headroom": (640, 90, 2),
    "stress": (960, 90, 2),
    "saturation": (1440, 60, 2),
    "overload": (2880, 60, 2),
    "single": (521, 90, 1),
    "single-stress": (960, 60, 1),
    "single-overload": (1440, 60, 1),
}
SERVICES = ("postgres", "redis", "commerce-1", "commerce-2", "haproxy")


def configure(project, output):
    global PROJECT, OUTPUT, COMPOSE
    PROJECT = project
    OUTPUT = output
    COMPOSE = ["docker", "compose", "-p", PROJECT,
               "-f", str(ROOT / "infra/stage2/compose.yaml"),
               "-f", str(ROOT / "infra/stage3/compose.yaml")]
    os.environ["LOAD_RESULTS_DIR"] = str(output)


def haproxy_command(query):
    with socket.create_connection(("127.0.0.1", 18084), timeout=3) as connection:
        connection.sendall((query + "\n").encode())
        connection.shutdown(socket.SHUT_WR)
        parts = []
        while data := connection.recv(65536):
            parts.append(data)
    return b"".join(parts).decode()


def haproxy_stats():
    raw = haproxy_command("show stat")
    fields = ("status", "weight", "scur", "smax", "qcur", "qmax", "stot", "req_tot",
              "hrsp_2xx", "hrsp_4xx", "hrsp_5xx", "econ", "eresp", "check_status")
    return {row["pxname"] + "/" + row["svname"]: {key: row.get(key, "") for key in fields}
            for row in csv.DictReader(io.StringIO(raw.lstrip("# ")))}


def command(args, data=None, allowed=(0,)):
    process = subprocess.run(args, input=data, capture_output=True, text=True, timeout=240)
    if process.returncode not in allowed:
        raise RuntimeError(f"Command failed ({process.returncode}): {args}\n{process.stdout[-3000:]}\n{process.stderr[-4000:]}")
    return process.stdout


def compose(*args, data=None):
    return command(COMPOSE + list(args), data)


def sql(query):
    return compose("exec", "-T", "postgres", "psql", "-X", "-qAt", "-v", "ON_ERROR_STOP=1",
                   "-U", "commerce", "-d", "commerce", data=query).strip()


def api(port, path):
    credentials = base64.b64encode(b"admin:demo-admin").decode()
    request = urllib.request.Request(f"http://127.0.0.1:{port}{path}",
                                     headers={"Authorization": "Basic " + credentials})
    with urllib.request.urlopen(request, timeout=3) as response:
        return json.load(response)


def wait_ready(ports=(18080, 18081, 18082)):
    deadline = time.monotonic() + 150
    ready = {}
    started = time.monotonic()
    while time.monotonic() < deadline:
        for port in ports:
            if port in ready:
                continue
            try:
                if api(port, "/readyz")["status"] == "UP":
                    ready[port] = round(time.monotonic() - started, 3)
            except (OSError, ValueError):
                pass
        if len(ready) == len(ports):
            return ready
        time.sleep(0.5)
    raise RuntimeError("API readiness timed out")


def save(name, value):
    (OUTPUT / name).write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")


def database_snapshot():
    return json.loads(sql("""
        SELECT json_build_object(
          'database', (SELECT row_to_json(d) FROM (
            SELECT xact_commit,xact_rollback,blks_read,blks_hit,deadlocks,temp_bytes,
                   blk_read_time,blk_write_time FROM pg_stat_database WHERE datname=current_database()
          ) d),
          'wal_lsn', pg_current_wal_lsn(),
          'size_bytes', pg_database_size(current_database()),
          'statements', (SELECT json_agg(s) FROM (
            SELECT query,calls,total_exec_time,mean_exec_time,rows,shared_blks_hit,shared_blks_read,
                   shared_blks_dirtied,shared_blks_written,temp_blks_written,wal_bytes
            FROM pg_stat_statements WHERE dbid=(SELECT oid FROM pg_database WHERE datname=current_database())
            ORDER BY total_exec_time DESC LIMIT 20
          ) s));
    """))


def cgroup_snapshot(instances, allow_stopped=False):
    values = {}
    for service in ("postgres", "redis", "haproxy", *(f"commerce-{n}" for n in range(1, instances + 1))):
        try:
            raw = compose("exec", "-T", service, "cat", "/sys/fs/cgroup/cpu.stat")
        except RuntimeError:
            if allow_stopped:
                continue
            raise
        values[service] = {key: int(value) for key, value in (line.split() for line in raw.splitlines())}
    return values


def sample_resources(instances):
    container_ids = command(["docker", "ps", "--filter", "label=com.docker.compose.project=" + PROJECT,
                             "--format", "{{.ID}}"]).split()
    stats = command(["docker", "stats", "--no-stream", "--format", "{{json .}}", *container_ids])
    result = {"containers": [json.loads(line) for line in stats.splitlines()], "jvm": {}}
    result["haproxy"] = haproxy_stats()
    metrics = {
        "heap_used": "jvm.memory.used?tag=area:heap",
        "gc_pause": "jvm.gc.pause",
        "pool_active": "hikaricp.connections.active",
        "pool_pending": "hikaricp.connections.pending",
    }
    for instance in range(1, instances + 1):
        values = {}
        for label, metric in metrics.items():
            try:
                values[label] = api(18080 + instance, "/actuator/metrics/" + metric)["measurements"]
            except (OSError, ValueError):
                values[label] = None
        try:
            values["cache"] = api(18080 + instance, "/api/admin/cache-stats")
            status = compose("exec", "-T", f"commerce-{instance}", "cat", "/proc/1/status")
            values["process_kib"] = {line.split(":")[0]: int(line.split()[1])
                                     for line in status.splitlines() if line.startswith(("VmRSS:", "VmHWM:"))}
        except (OSError, ValueError, RuntimeError) as error:
            values["sample_error"] = str(error)[:200]
        result["jvm"][f"commerce-{instance}"] = values
    return result


def sample_loop(stop, samples, instances, output_path):
    started = time.monotonic()
    while not stop.is_set():
        try:
            sample = sample_resources(instances)
            sample["elapsed_seconds"] = round(time.monotonic() - started, 3)
            samples.append(sample)
            with output_path.open("a") as stream:
                stream.write(json.dumps(sample) + "\n")
        except (OSError, RuntimeError) as error:
            samples.append({"sample_error": str(error)[:500]})
        stop.wait(5)


def memory_bytes(value):
    number, unit = re.fullmatch(r"([0-9.]+)\s*([a-zA-Z]+)", value.strip()).groups()
    return float(number) * {"B": 1, "kB": 1000, "MB": 1000000, "GB": 1000000000,
                          "KiB": 1024, "MiB": 1024**2, "GiB": 1024**3}[unit]


def resource_summary(samples):
    groups = {}
    for sample in samples:
        for row in sample.get("containers", []):
            name = row["Name"].removeprefix(PROJECT + "-").rsplit("-", 1)[0]
            if name.startswith("k6-run"):
                name = "k6"
            group = groups.setdefault(name, {"cpu": [], "working_set": [], "rss": [], "heap": [], "pending": []})
            group["cpu"].append(float(row["CPUPerc"].rstrip("%")))
            group["working_set"].append(memory_bytes(row["MemUsage"].split("/")[0]))
        for name, values in sample.get("jvm", {}).items():
            group = groups.setdefault(name, {"cpu": [], "working_set": [], "rss": [], "heap": [], "pending": []})
            if "process_kib" in values:
                group["rss"].append(values["process_kib"]["VmRSS"] * 1024)
            for metric, key in (("heap_used", "heap"), ("pool_pending", "pending")):
                if values.get(metric):
                    group[key].append(values[metric][0]["value"])
    return {name: {key: {"mean": round(statistics.mean(values), 2), "max": max(values)}
                   for key, values in group.items() if values} for name, group in groups.items()}


def load_profile(name, rate, seconds, instances, transitioning=False):
    print(f"Running {name}: {rate} primary RPS, {seconds}s, {instances} API instance(s)", flush=True)
    sql("SELECT pg_stat_statements_reset();")
    before = database_snapshot()
    cpu_before = cgroup_snapshot(instances)
    haproxy_before = haproxy_stats()
    samples = []
    live_path = OUTPUT / (name + "-live.jsonl")
    live_path.write_text("")
    stop = threading.Event()
    collector = threading.Thread(target=sample_loop, args=(stop, samples, instances, live_path), daemon=True)
    collector.start()
    try:
        process = subprocess.run(COMPOSE + ["run", "--rm", "--no-deps", "-T", "-e", f"RATE={rate}",
                                 "-e", f"SECONDS={seconds}", "-e", f"RUN={name}", "k6", "run", "/scripts/load.js"],
                                 capture_output=True, text=True, timeout=seconds + 120)
        (OUTPUT / f"{name}-k6.log").write_text(process.stdout + process.stderr)
        if process.returncode not in (0, 99):
            raise RuntimeError((process.stdout + process.stderr)[-6000:])
    finally:
        stop.set()
        collector.join(timeout=45)
    after = database_snapshot()
    cpu_after = cgroup_snapshot(instances, allow_stopped=transitioning)
    haproxy_after = haproxy_stats()
    save(name + "-resources.json", samples)
    save(name + "-sql.json", {"before": before, "after": after})
    data = json.loads((OUTPUT / f"{name}-k6.json").read_text())
    metrics = data["metrics"]
    get = lambda key: metrics.get(key, {}).get("values", {})
    failed = [key for key, metric in metrics.items()
              if any(not threshold["ok"] for threshold in metric.get("thresholds", {}).values())]
    database_delta = {key: after["database"][key] - value for key, value in before["database"].items()}
    result = {
        **data["profile"], "instances": instances, "k6_exit": process.returncode,
        "transitioning": transitioning,
        "operations": get("operation_requests").get("count", 0),
        "http_requests": get("http_reqs").get("count", 0),
        "dropped_iterations": get("dropped_iterations").get("count", 0),
        "technical_errors": get("technical_errors"), "operation_errors": get("operation_errors"),
        "support_errors": get("support_errors"), "latency_ms": get("operation_latency"),
        "by_operation_ms": {key.removeprefix("latency_"): value["values"]
                            for key, value in metrics.items() if key.startswith("latency_")},
        "failed_thresholds": failed, "resources": resource_summary(samples),
        "database_delta": database_delta,
        "wal_bytes": int(sql(f"SELECT pg_wal_lsn_diff('{after['wal_lsn']}','{before['wal_lsn']}');")),
        "data_received_bytes": get("data_received").get("count", 0),
        "data_sent_bytes": get("data_sent").get("count", 0),
        "vus_max": get("vus_max").get("max"),
        "minute_metrics": [{
            "minute": minute + 1,
            "latency_ms": get(f"minute_latency_{minute}"),
            "technical_errors": get(f"minute_technical_errors_{minute}"),
            "operations": get(f"minute_operations_{minute}").get("count", 0),
        } for minute in range((seconds + 59) // 60)],
        "resource_sample_errors": sum("sample_error" in sample for sample in samples),
        "haproxy_before": haproxy_before,
        "haproxy_after": haproxy_after,
        "cgroup_cpu_delta": {name: {key: value - cpu_before[name][key] for key, value in values.items()}
                             for name, values in cpu_after.items()},
    }
    result["achieved_primary_rps"] = round(result["operations"] / seconds, 3)
    save(name + "-summary.json", result)
    print(json.dumps({key: result[key] for key in ("run", "achieved_primary_rps", "dropped_iterations", "latency_ms", "failed_thresholds")}), flush=True)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profiles", nargs="+", choices=PROFILES, default=list(PROFILES))
    args = parser.parse_args()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    os.environ["LOAD_RESULTS_DIR"] = str(OUTPUT)
    os.environ["LOAD_UID"] = str(os.getuid())
    os.environ["LOAD_GID"] = str(os.getgid())
    results = {"project": PROJECT, "environment": {}, "profiles": []}
    started = False
    try:
        print("Building isolated load project " + PROJECT, flush=True)
        compose("build", "commerce-1", "commerce-2")
        compose("pull", "k6")
        config = json.loads(compose("config", "--format", "json"))
        results["environment"]["java_options"] = config["services"]["commerce-1"]["environment"]["JAVA_TOOL_OPTIONS"]
        results["environment"]["haproxy_config_sha256"] = hashlib.sha256((ROOT / "infra/stage2/haproxy.cfg").read_bytes()).hexdigest()
        results["environment"]["docker"] = json.loads(command(["docker", "info", "--format", "{{json .}} "]))
        # Keep only environment facts needed for reproduction.
        results["environment"]["docker"] = {key: results["environment"]["docker"][key]
                                             for key in ("ServerVersion", "Architecture", "NCPU", "MemTotal", "OperatingSystem")}
        started = True
        start = time.monotonic()
        compose("up", "-d")
        readiness = wait_ready()
        results["cold_stack_start_seconds"] = round(time.monotonic() - start, 3)
        results["readiness_after_compose_seconds"] = readiness
        sql("CREATE EXTENSION IF NOT EXISTS pg_stat_statements;")
        sql((ROOT / "infra/stage2/history_fixture.sql").read_text())
        sql("UPDATE inventory SET on_hand=100000 WHERE product_id BETWEEN 60003 AND 60602; ANALYZE;")
        results["environment"]["java"] = compose("exec", "-T", "-e", "JAVA_TOOL_OPTIONS=", "-e", "JDK_JAVA_OPTIONS=", "commerce-1", "java", "--version").strip()
        results["environment"]["images"] = json.loads(compose("images", "--format", "json"))
        instances = 2
        for name in args.profiles:
            rate, seconds, desired_instances = PROFILES[name]
            if desired_instances != instances:
                compose("stop" if desired_instances == 1 else "start", "commerce-2")
                instances = desired_instances
                time.sleep(8)  # HAProxy health-check transition, outside the measured window.
                wait_ready((18080, 18081) if instances == 1 else (18080, 18081, 18082))
            results["profiles"].append(load_profile(name, rate, seconds, instances))
            if name == "warmup":
                routes = {}
                for instance in range(1, instances + 1):
                    try:
                        routes[f"commerce-{instance}"] = api(18080 + instance, "/actuator/metrics/http.server.requests?tag=status:503")
                    except OSError as error:
                        routes[f"commerce-{instance}"] = {"sample_error": str(error)}
                results["cold_error_routes"] = routes
            save("results.json", results)
        results["inventory_mismatches"] = int(sql("""
            SELECT count(*) FROM inventory i LEFT JOIN (
              SELECT ri.product_id,sum(ri.quantity) qty FROM reservation_items ri
              JOIN reservations r ON r.id=ri.reservation_id WHERE r.state='ACTIVE' GROUP BY ri.product_id
            ) a ON a.product_id=i.product_id WHERE i.reserved<>coalesce(a.qty,0) OR i.reserved<0 OR i.reserved>i.on_hand;
        """))
        assert results["inventory_mismatches"] == 0
        save("results.json", results)
    finally:
        if started:
            try:
                (OUTPUT / "stack.log").write_text(compose("logs", "--no-color"))
                compose("stop", "commerce-1", "commerce-2")
                for instance in (1, 2):
                    compose("cp", f"commerce-{instance}:/tmp/commerce.jfr", str(OUTPUT / f"commerce-{instance}.jfr"))
            finally:
                # This project owns only disposable synthetic data.
                compose("down", "-v")
    print("Raw measurements and JFR recordings written to " + str(OUTPUT), flush=True)


if __name__ == "__main__":
    main()
