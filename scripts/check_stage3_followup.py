#!/usr/bin/env python3
"""Cold admission, rolling replacement and two local 30-minute load profiles."""
import argparse
import hashlib
import json
import os
import threading
import time
import urllib.error
import urllib.request
import uuid

import check_stage3 as bench

OUTPUT = bench.ROOT / "artifacts/stage3-followup"


def probe(port, path):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=2) as response:
            return response.status
    except urllib.error.HTTPError as error:
        return error.code
    except OSError:
        return 0


def observe_startup(port):
    started = time.monotonic()
    samples = []
    while time.monotonic() - started < 150:
        sample = {"elapsed_seconds": round(time.monotonic() - started, 3),
                  "live": probe(port, "/livez"), "ready": probe(port, "/readyz")}
        samples.append(sample)
        if sample["ready"] == 200:
            return {"ready_seconds": sample["elapsed_seconds"], "samples": samples,
                    "observed_refusing_while_live": any(row["live"] == 200 and row["ready"] == 503 for row in samples)}
        time.sleep(0.25)
    raise RuntimeError(f"Startup timed out on port {port}")


def wait_balanced():
    deadline = time.monotonic() + 150
    while time.monotonic() < deadline:
        rows = bench.haproxy_stats()
        if all(rows[f"commerce/c{n}"]["status"] == "UP" and int(rows[f"commerce/c{n}"]["weight"]) == 100
               for n in (1, 2)):
            return
        time.sleep(1)
    raise RuntimeError("HAProxy did not finish slowstart")


def assert_clean(profile):
    assert not profile["failed_thresholds"], (profile["run"], profile["failed_thresholds"])
    assert profile["resource_sample_errors"] == 0, profile["run"]


def rolling_replacement():
    result = {}
    errors = []

    def load():
        try:
            result["profile"] = bench.load_profile("rolling-521", 521, 240, 1, transitioning=True)
        except Exception as error:
            errors.append(error)

    initial = int(bench.haproxy_stats()["api/FRONTEND"]["stot"])
    worker = threading.Thread(target=load, daemon=True)
    worker.start()
    deadline = time.monotonic() + 60
    while int(bench.haproxy_stats()["api/FRONTEND"]["stot"]) - initial < 100:
        if errors:
            raise errors[0]
        if time.monotonic() > deadline:
            raise RuntimeError("Rolling load did not start")
        time.sleep(0.5)
    started = time.monotonic()
    samples = []
    introduced = False
    drained = False
    try:
        while worker.is_alive():
            elapsed = time.monotonic() - started
            rows = bench.haproxy_stats()
            samples.append({"elapsed_seconds": round(elapsed, 3), "servers": rows})
            if elapsed >= 30 and not introduced:
                bench.compose("up", "-d", "--no-deps", "commerce-2")
                result["second_startup"] = observe_startup(18082)
                introduced = True
            if elapsed >= 160 and not drained:
                assert rows["commerce/c2"]["status"] == "UP", rows
                assert int(rows["commerce/c2"]["weight"]) == 100, rows
                bench.haproxy_command("set server commerce/c1 state drain")
                drain_started = time.monotonic()
                while int(bench.haproxy_stats()["commerce/c1"]["scur"]) > 0:
                    if time.monotonic() - drain_started > 20:
                        raise RuntimeError("Active requests did not drain")
                    time.sleep(0.1)
                result["drain_seconds"] = round(time.monotonic() - drain_started, 3)
                bench.compose("stop", "commerce-1")
                bench.compose("cp", "commerce-1:/tmp/commerce.jfr", str(OUTPUT / "cold-rolling-commerce-1.jfr"))
                result["first_stopped_at_seconds"] = round(time.monotonic() - started, 3)
                drained = True
            time.sleep(1)
    finally:
        worker.join(timeout=360)
    if errors:
        raise errors[0]
    assert introduced and drained
    assert_clean(result["profile"])
    assert result["profile"]["technical_errors"]["passes"] == 0
    assert result["profile"]["operation_errors"]["passes"] == 0
    weights = [int(row["servers"]["commerce/c2"]["weight"]) for row in samples]
    assert any(0 < weight < 100 for weight in weights), weights
    assert weights[-1] == 100, weights[-1]
    first = samples[-1]["servers"]["commerce/c1"]
    second = samples[-1]["servers"]["commerce/c2"]
    assert int(first["scur"]) == 0 and int(second["hrsp_2xx"]) > 0
    result["samples"] = samples
    bench.save("rolling.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--startup-only", action="store_true", help="Run cold and rolling checks without the soak profiles")
    args = parser.parse_args()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    project = "hdls-stage3-followup-" + uuid.uuid4().hex[:8]
    bench.configure(project, OUTPUT)
    os.environ["LOAD_UID"] = str(os.getuid())
    os.environ["LOAD_GID"] = str(os.getgid())
    result = {"project": project, "profiles": [], "soak_seconds_each": 1800}
    started = False
    try:
        print("Preparing isolated local followup " + project, flush=True)
        bench.compose("build", "commerce-1", "commerce-2")
        bench.compose("pull", "k6")
        config = json.loads(bench.compose("config", "--format", "json"))
        result["java_options"] = config["services"]["commerce-1"]["environment"]["JAVA_TOOL_OPTIONS"]
        result["profiling_options"] = config["services"]["commerce-1"]["environment"]["JDK_JAVA_OPTIONS"]
        base = json.loads(bench.command(["docker", "compose", "-f", str(bench.ROOT / "infra/stage2/compose.yaml"),
                                         "config", "--format", "json"]))
        assert result["java_options"] == base["services"]["commerce-1"]["environment"]["JAVA_TOOL_OPTIONS"]
        result["shared_runtime_identical"] = True
        result["haproxy_config_sha256"] = hashlib.sha256((bench.ROOT / "infra/stage2/haproxy.cfg").read_bytes()).hexdigest()
        started = True
        cold_started = time.monotonic()
        bench.compose("up", "-d", "--wait", "postgres", "redis")
        bench.compose("up", "-d", "--no-deps", "commerce-1", "haproxy")
        result["cold_startup"] = observe_startup(18081)
        bench.wait_ready((18080, 18081))
        result["cold_stack_ready_seconds"] = round(time.monotonic() - cold_started, 3)
        assert result["cold_startup"]["observed_refusing_while_live"]
        bench.save("results.json", result)
        result["environment"] = {
            "docker": json.loads(bench.command(["docker", "info", "--format", "{{json .}}"])),
            "images": json.loads(bench.compose("images", "--format", "json")),
            "java": bench.compose("exec", "-T", "-e", "JAVA_TOOL_OPTIONS=", "-e", "JDK_JAVA_OPTIONS=", "commerce-1", "java", "--version").strip(),
        }
        result["environment"]["docker"] = {key: result["environment"]["docker"][key]
                                             for key in ("ServerVersion", "Architecture", "NCPU", "MemTotal")}
        bench.sql("CREATE EXTENSION IF NOT EXISTS pg_stat_statements;")
        bench.sql((bench.ROOT / "infra/stage2/history_fixture.sql").read_text())
        bench.sql("UPDATE inventory SET on_hand=100000 WHERE product_id BETWEEN 60003 AND 60602; ANALYZE;")
        cold = bench.load_profile("cold-521", 521, 120, 1)
        result["profiles"].append(cold)
        bench.save("results.json", result)
        assert_clean(cold)
        assert cold["technical_errors"]["passes"] == 0
        bench.save("results.json", result)
        result["rolling"] = rolling_replacement()
        result["profiles"].append(result["rolling"].pop("profile"))
        bench.save("results.json", result)
        bench.compose("up", "-d", "--no-deps", "commerce-1")
        result["restart_startup"] = observe_startup(18081)
        bench.haproxy_command("set server commerce/c1 state ready")
        wait_balanced()
        if not args.startup_only:
            for rate in (521, 640):
                profile = bench.load_profile(f"soak-{rate}", rate, 1800, 2)
                result["profiles"].append(profile)
                bench.save("results.json", result)
                assert_clean(profile)
        result["inventory_mismatches"] = int(bench.sql("""
            SELECT count(*) FROM inventory i LEFT JOIN (
              SELECT ri.product_id,sum(ri.quantity) qty FROM reservation_items ri
              JOIN reservations r ON r.id=ri.reservation_id WHERE r.state='ACTIVE' GROUP BY ri.product_id
            ) a ON a.product_id=i.product_id
            WHERE i.reserved<>coalesce(a.qty,0) OR i.reserved<0 OR i.reserved>i.on_hand;
        """))
        assert result["inventory_mismatches"] == 0
        ids = bench.compose("ps", "-q").split()
        raw = bench.command(["docker", "inspect", "--format",
                             '{"name":{{json .Name}},"state":{{json .State}},"restarts":{{.RestartCount}}}', *ids])
        result["final_container_states"] = [json.loads(line) for line in raw.splitlines()]
        assert all(not row["state"]["OOMKilled"] and row["restarts"] == 0 for row in result["final_container_states"])
        bench.save("results.json", result)
    finally:
        if started:
            try:
                (OUTPUT / "stack.log").write_text(bench.compose("logs", "--no-color"))
                bench.compose("stop", "commerce-1", "commerce-2")
                for instance in (1, 2):
                    if bench.compose("ps", "-a", "-q", f"commerce-{instance}").strip():
                        bench.compose("cp", f"commerce-{instance}:/tmp/commerce.jfr", str(OUTPUT / f"commerce-{instance}.jfr"))
            finally:
                bench.compose("down", "-v")
    print("Followup completed. Measurements in " + str(OUTPUT), flush=True)


if __name__ == "__main__":
    main()
