#!/usr/bin/env python3
"""Exercise only the dedicated stage 1 diagnostic Compose project."""
import argparse
import collections
import concurrent.futures
import csv
import io
import json
import subprocess
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ["docker", "compose", "-f", str(ROOT / "infra/lb-smoke/compose.yaml")]


def compose(*arguments):
    result = subprocess.run(COMPOSE + list(arguments), text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError(result.stdout[-4000:] + result.stderr[-4000:])
    return result.stdout


def request(path):
    req = urllib.request.Request("http://127.0.0.1:18080" + path, headers={"Connection": "close"})
    with urllib.request.urlopen(req, timeout=5) as response:
        assert response.status == 200
        return json.load(response)["instance"]


def status():
    with urllib.request.urlopen("http://127.0.0.1:18404/stats;csv", timeout=2) as response:
        text = response.read().decode()
    return {(r["pxname"], r["svname"]): r["status"] for r in csv.DictReader(io.StringIO(text.lstrip("# ")))}


def wait_for(predicate, seconds=30):
    until = time.monotonic() + seconds
    last = None
    while time.monotonic() < until:
        try:
            if predicate():
                return
        except (OSError, ValueError, KeyError) as error:
            last = error
        time.sleep(0.25)
    raise AssertionError(f"Condition not reached within {seconds}s; last error: {last}")


def run():
    wait_for(lambda: all(status().get((pool, node), "").startswith("UP") for pool, node in [
        ("commerce", "commerce_1"), ("commerce", "commerce_2"), ("search", "search_1")]), 60)
    counts = collections.Counter(request("/api/catalog") for _ in range(20))
    assert counts == {"commerce-1": 10, "commerce-2": 10}, counts
    assert request("/api/search") == request("/api/search/items") == "search-1"
    assert request("/api/search-other") in {"commerce-1", "commerce-2"}
    assert request("/api/admin/images") in {"commerce-1", "commerce-2"}
    compose("exec", "-T", "commerce-1", "touch", "/tmp/not-ready")
    started = time.monotonic()
    try:
        wait_for(lambda: status().get(("commerce", "commerce_1"), "").startswith("DOWN"))
        exclusion = time.monotonic() - started
        compose("exec", "-T", "commerce-1", "kill", "-0", "1")
        assert all(request("/api/catalog") == "commerce-2" for _ in range(10))
        assert request("/api/search") == "search-1"
    finally:
        compose("exec", "-T", "commerce-1", "rm", "-f", "/tmp/not-ready")
    wait_for(lambda: status().get(("commerce", "commerce_1"), "").startswith("UP"))
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        pending = [executor.submit(request, "/api/slow") for _ in range(2)]
        time.sleep(0.2)
        compose("stop", "-t", "15", "commerce-1")
        completed = [future.result() for future in pending]
    assert set(completed) == {"commerce-1", "commerce-2"}, completed
    wait_for(lambda: status().get(("commerce", "commerce_1"), "").startswith("DOWN"))
    assert all(request("/api/catalog") == "commerce-2" for _ in range(10))
    compose("start", "commerce-1")
    wait_for(lambda: status().get(("commerce", "commerce_1"), "").startswith("UP"))
    result = {
        "round_robin_20_requests": dict(counts),
        "search_routing": "passed",
        "upload_routing": "passed",
        "not_ready_but_alive": "passed",
        "excluded_after_seconds": round(exclusion, 2),
        "search_during_commerce_failure": "passed",
        "inflight_requests_during_shutdown": completed,
        "recovery": "passed",
        "scope": "functional diagnostic check; no throughput claim",
    }
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", action="store_true", help="Start the dedicated project and remove it afterwards")
    args = parser.parse_args()
    try:
        if args.start:
            print("Starting dedicated stage 1 Compose project", flush=True)
            compose("up", "-d")
        run()
    finally:
        if args.start:
            compose("down")


if __name__ == "__main__":
    main()
