#!/usr/bin/env python3
"""Mixed API + media load against an already running stage-4 stack."""
import argparse
import json
import os
import random
import struct
import subprocess
import threading
import time
import uuid
import zlib
from pathlib import Path
import check_stage4 as stack

ROOT = stack.ROOT


def png():
    def chunk(kind, data):
        return struct.pack("!I", len(data)) + kind + data + struct.pack("!I", zlib.crc32(kind + data))
    rows = b"".join(b"\x00" + random.Random(row).randbytes(200 * 3) for row in range(200))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack("!2I5B", 200, 200, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b"")


def upload_fixture():
    image = png()
    boundary = uuid.uuid4().hex
    body = f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="load.png"\r\nContent-Type: image/png\r\n\r\n'.encode() + image + f"\r\n--{boundary}--\r\n".encode()
    result = stack.ok("/api/admin/products/99990/images", "admin", "POST", body, headers={"Content-Type": "multipart/form-data; boundary=" + boundary})
    return result["objectKey"], len(image)


def sample(stop, values):
    while not stop.is_set():
        try:
            ids = stack.command("ps", "-q").split()
            raw = subprocess.check_output(["docker", "stats", "--no-stream", "--format", "{{json .}}", *ids], text=True, timeout=20)
            rss = {}
            for service in ("commerce-1", "commerce-2", "worker-1", "worker-2", "search", "provider"):
                status = stack.command("exec", "-T", service, "cat", "/proc/1/status")
                rss[service] = int(next(line.split()[1] for line in status.splitlines() if line.startswith("VmRSS:"))) * 1024
            values.append({"containers": [json.loads(line) for line in raw.splitlines()], "rss_bytes": rss})
        except (RuntimeError, OSError, subprocess.SubprocessError) as e:
            values.append({"error": str(e)[:500]})
        stop.wait(5)


def profile(name, rate, seconds, image_key, image_bytes, output):
    name = name + "-" + uuid.uuid4().hex[:8]
    samples = []
    stop = threading.Event()
    thread = threading.Thread(target=sample, args=(stop, samples), daemon=True)
    before = int(stack.sql("SELECT deadlocks FROM pg_stat_database WHERE datname=current_database()"))
    callbacks_before = int(stack.sql("SELECT count(*) FROM payment_webhooks"))
    thread.start()
    try:
        result = subprocess.run(stack.COMPOSE + ["run", "--rm", "--no-deps", "-T", "-e", f"RATE={rate}", "-e", f"SECONDS={seconds}", "-e", f"RUN={name}", "-e", f"IMAGE_KEY={image_key}", "k6", "run", "/scripts/load.js"], text=True, capture_output=True, timeout=seconds+120)
        (output / f"{name}.log").write_text(result.stdout + result.stderr)
        if result.returncode not in (0, 99):
            raise RuntimeError((result.stdout + result.stderr)[-4000:])
    finally:
        stop.set()
        thread.join(timeout=30)
    data = json.loads((output / f"{name}-k6.json").read_text())
    metric = lambda key: data["metrics"].get(key, {}).get("values", {})
    failures = [key for key, value in data["metrics"].items() if any(not threshold["ok"] for threshold in value.get("thresholds", {}).values())]
    invariant = int(stack.sql("""
        WITH active AS (SELECT ri.product_id,sum(ri.quantity) AS qty FROM reservation_items ri JOIN reservations r ON r.id=ri.reservation_id WHERE r.state='ACTIVE' GROUP BY ri.product_id)
        SELECT (SELECT count(*) FROM inventory i LEFT JOIN active a ON a.product_id=i.product_id WHERE i.reserved<0 OR i.reserved>i.on_hand OR i.reserved<>coalesce(a.qty,0)) +
          (SELECT count(*) FROM orders o JOIN reservations r ON r.order_id=o.id WHERE (o.status='PAID')<>(r.state='CONFIRMED'));
        """))
    assert invariant == 0
    stack.eventually("load outbox drains", lambda: stack.sql("SELECT count(*) FROM outbox_events WHERE published_at IS NULL") == "0")
    restarts = json.loads(subprocess.check_output(["docker", "inspect", *stack.command("ps", "-q").split()], text=True))
    summary = {**data["profile"], "k6_exit": result.returncode, "operations": metric("operation_requests").get("count", 0), "http_requests": metric("http_reqs").get("count", 0),
        "technical_errors": metric("technical_errors"), "operation_errors": metric("operation_errors"), "support_errors": metric("support_errors"), "dropped": metric("dropped_iterations").get("count", 0),
        "latency_ms": metric("operation_latency"), "by_operation_ms": {key.removeprefix("latency_"): value["values"] for key, value in data["metrics"].items() if key.startswith("latency_")},
        "media_latency_ms": metric("media_latency"), "media_errors": metric("media_errors"), "media_object_bytes": image_bytes,
        "callbacks_applied": int(stack.sql("SELECT count(*) FROM payment_webhooks"))-callbacks_before,
        "deadlocks": int(stack.sql("SELECT deadlocks FROM pg_stat_database WHERE datname=current_database()"))-before,
        "invariant_violations": invariant, "failed_thresholds": failures,
        "max_rss_bytes": {name: max(s.get("rss_bytes", {}).get(name, 0) for s in samples) for name in ("commerce-1", "commerce-2", "worker-1", "worker-2", "search", "provider")},
        "oom_killed": [row["Name"] for row in restarts if row["State"]["OOMKilled"]], "samples": len(samples),
        "resource_sample_errors": sum("error" in s for s in samples)}
    (output / f"{name}-resources.json").write_text(json.dumps(samples, indent=2) + "\n")
    print(json.dumps(summary, indent=2), flush=True)
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", default="hdls-stage4")
    parser.add_argument("--seconds", type=int, default=300)
    parser.add_argument("--cold-cache", action="store_true", help="Clear only the selected stack Redis before the arrival-rate warmup")
    args = parser.parse_args()
    stack.PROJECT = args.project
    stack.COMPOSE = ["docker", "compose", "-p", args.project, "-f", str(ROOT / "infra/stage4/compose.yaml")]
    output = ROOT / "artifacts/stage4/load"
    output.mkdir(parents=True, exist_ok=True)
    os.environ["LOAD_RESULTS_DIR"] = str(output)
    stack.eventually("APIs ready", lambda: all(stack.request("/readyz", port=p)[0] == 200 for p in (18080, 18081, 18082, 18085)))
    if stack.sql("SELECT count(*) FROM orders WHERE idempotency_key='history-1'") == "0":
        stack.sql((ROOT / "infra/stage2/history_fixture.sql").read_text())
    key, size = upload_fixture()
    if args.cold_cache:
        stack.command("exec", "-T", "redis", "redis-cli", "FLUSHDB")
    warmup = profile("warmup-640", 640, 60, key, size, output)
    result = profile("mixed-640", 640, args.seconds, key, size, output)
    (ROOT / "docs/stage4_load_results.json").write_text(json.dumps({"cold_cache_before_warmup": args.cold_cache, "warmup": warmup, "mixed": result}, indent=2, ensure_ascii=False) + "\n")
    if result["failed_thresholds"] or result["oom_killed"]:
        raise SystemExit("Stage 4 load thresholds failed; see docs/stage4_load_results.json")
