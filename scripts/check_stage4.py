#!/usr/bin/env python3
"""Real HTTP/PostgreSQL/RabbitMQ/OpenSearch/Garage fault checks in an owned stack."""
import argparse
import base64
import concurrent.futures
import hashlib
import hmac
import json
import os
import subprocess
import time
import urllib.error
import urllib.request
import urllib.parse
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROJECT = os.environ.get("STAGE4_PROJECT", "hdls-stage4-check-" + uuid.uuid4().hex[:8])
COMPOSE = ["docker", "compose", "-p", PROJECT, "-f", str(ROOT / "infra/stage4/compose.yaml")]
RESULT = {"project": PROJECT, "checks": {}}


def command(*args, data=None):
    p = subprocess.run(COMPOSE + list(args), input=data, capture_output=True, text=True)
    if p.returncode:
        raise RuntimeError((p.stdout + p.stderr)[-5000:])
    return p.stdout


def sql(query):
    return command("exec", "-T", "postgres", "psql", "-X", "-qAt", "-v", "ON_ERROR_STOP=1", "-U", "commerce", "-d", "commerce", data=query).strip()


def request(path, user=None, method="GET", body=None, port=18080, headers=None):
    hdr = {"Content-Type": "application/json"}
    if user:
        credentials = "admin:demo-admin" if user == "admin" else f"buyer-{user}:demo-buyer-{user}"
        hdr["Authorization"] = "Basic " + base64.b64encode(credentials.encode()).decode()
    hdr.update(headers or {})
    data = body if isinstance(body, bytes) else None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, headers=hdr, method=method)
    try:
        response = urllib.request.urlopen(req, timeout=15)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        raw = response.read()
        try:
            payload = json.loads(raw)
        except ValueError:
            payload = raw
        return response.status, payload


def ok(*args, **kwargs):
    status, body = request(*args, **kwargs)
    assert 200 <= status < 300, (args, status, body)
    return body


def provider(path, method="GET", body=None):
    return ok(path, method=method, body=body, port=18087, headers={"Authorization": "Bearer local-provider-token"})


def eventually(label, predicate, timeout=120):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            last = predicate()
            if last:
                return last
        except (OSError, ValueError, AssertionError) as error:
            last = str(error)
        time.sleep(0.5)
    raise AssertionError(f"Timed out: {label}, last={last}")


def check(name, value=True):
    RESULT["checks"][name] = value
    print(name, json.dumps(value), flush=True)


def order(user, product=100):
    cart = ok(f"/api/cart/items/{product}", user, "PUT", {"quantity": 1})
    return ok("/api/orders", user, "POST", {"expectedCartVersion": cart["version"], "expectedTotalMinor": cart["totalMinor"]}, headers={"Idempotency-Key": uuid.uuid4().hex})["orderId"]


def pay(user, oid, trace=None):
    return ok(f"/api/orders/{oid}/payments", user, "POST", headers={"X-Request-ID": trace} if trace else None)


def state(oid):
    return sql(f"SELECT o.status||':'||p.state FROM orders o JOIN payments p ON p.order_id=o.id WHERE o.id='{oid}';")


def callback(payment, event=None):
    event = event or str(uuid.uuid4())
    body = json.dumps({"eventId": event, "paymentId": payment}).encode()
    timestamp = str(int(time.time()))
    signature = hmac.new(b"local-webhook-secret", timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()
    return request("/api/payments/webhook", method="POST", body=body, headers={"X-Payment-Timestamp": timestamp, "X-Payment-Signature": signature})


def queue_count(queue):
    queues = ok("/api/queues/%2F", port=15672, headers={"Authorization": "Basic " + base64.b64encode(b"commerce:local-rabbit-password").decode()})
    return next((q.get("messages", 0) for q in queues if q["name"] == queue), 0)


def run():
    eventually("all APIs ready", lambda: all(request("/readyz", port=p)[0] == 200 for p in (18080, 18081, 18082, 18085)), 240)
    eventually("initial index", lambda: sql("SELECT count(*) FROM search_rebuilds WHERE state='DONE'") == "1", 240)
    eventually("search admitted by HAProxy", lambda: request("/api/search")[0] == 200)
    assert ok("/api/search")["total"] == 100000
    check("initial_search_products", 100000)
    oid = order(1)
    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
        attempts = list(pool.map(lambda _: pay(1, oid), range(32)))
    payment = attempts[0]["paymentId"]
    assert len({p["paymentId"] for p in attempts}) == 1
    assert sql(f"SELECT count(*) FROM provider.payments WHERE id='{payment}'") == "1"
    assert request(f"/api/orders/{oid}/payments", 2, "POST")[0] == 404
    provider(f"/demo/payments/{payment}/state", "POST", {"state": "SUCCEEDED"})
    eventually("paid", lambda: state(oid) == "PAID:SUCCEEDED")
    stock = sql("SELECT on_hand||':'||reserved FROM inventory WHERE product_id=100")
    event = str(uuid.uuid4())
    for _ in range(10):
        assert callback(payment, event)[0] == 200
    assert sql("SELECT on_hand||':'||reserved FROM inventory WHERE product_id=100") == stock
    assert request("/api/payments/webhook", method="POST", body=b"{}", headers={"X-Payment-Timestamp": str(int(time.time())), "X-Payment-Signature": "00"})[0] == 401
    check("payment_retry_webhook_ownership", {"parallel_initiations": 32, "provider_payments": 1, "duplicate_webhooks": 10})

    # Persist at the provider before a response that exceeds Commerce's 3-second deadline.
    command("stop", "worker-1", "worker-2")
    oid = order(3, 101)
    provider("/demo/faults", "PUT", {"createDelayMs": 5000, "pauseCallbacks": True})
    trace = str(uuid.uuid4())
    pending = pay(3, oid, trace)
    assert pending["state"] == "PENDING"
    assert sql(f"SELECT last_error IS NOT NULL FROM payments WHERE order_id='{oid}'") == "t"
    provider(f"/demo/payments/{pending['paymentId']}/state", "POST", {"state": "SUCCEEDED"})
    command("start", "worker-1", "worker-2")
    eventually("reconcile lost response without callback", lambda: state(oid) == "PAID:SUCCEEDED")
    provider("/demo/faults", "PUT", {"createDelayMs": 0, "pauseCallbacks": False})
    check("ambiguous_create_reconciled_without_webhook")
    assert sql(f"SELECT trace_id FROM outbox_events WHERE aggregate_id='{oid}' AND event_type='PaymentSucceeded'") == trace
    check("reconciliation_preserves_trace_id")

    oid = order(4, 102)
    payment = pay(4, oid)["paymentId"]
    sql(f"UPDATE orders SET expires_at=now()-interval '1 second' WHERE id='{oid}';")
    eventually("automatic expiry", lambda: sql(f"SELECT status FROM orders WHERE id='{oid}'") == "EXPIRED")
    provider(f"/demo/payments/{payment}/state", "POST", {"state": "SUCCEEDED"})
    eventually("late compensation", lambda: state(oid) == "EXPIRED:REFUNDED")
    assert sql("SELECT on_hand||':'||reserved FROM inventory WHERE product_id=102") == "1000:0"
    check("late_payment_refunded_stock_released_once")

    oid = order(5, 103)
    payment = pay(5, oid)["paymentId"]
    provider(f"/demo/payments/{payment}/state", "POST", {"state": "FAILED"})
    eventually("failed", lambda: state(oid) == "PENDING_PAYMENT:FAILED")
    sql(f"UPDATE orders SET expires_at=now()-interval '1 second' WHERE id='{oid}';")
    eventually("failed payment expiry", lambda: state(oid) == "EXPIRED:FAILED")
    check("failed_payment_expires")

    oid = order(6, 104)
    payment = pay(6, oid)["paymentId"]
    sql(f"UPDATE provider.payments SET created_at=now()-interval '31 minutes' WHERE id='{payment}'; UPDATE orders SET expires_at=now()-interval '1 second' WHERE id='{oid}';")
    eventually("abandoned provider session closes", lambda: state(oid) == "EXPIRED:FAILED")
    assert provider(f"/payments/{payment}")["state"] == "FAILED"
    check("abandoned_payment_session_becomes_terminal")

    def race(user):
        oid = order(user, 200 + user)
        payment = pay(user, oid)["paymentId"]
        sql(f"UPDATE orders SET expires_at=clock_timestamp()+interval '50 milliseconds' WHERE id='{oid}';")
        provider(f"/demo/payments/{payment}/state", "POST", {"state": "SUCCEEDED"})
        callback(payment)
        eventually("payment expiry race", lambda: state(oid) in ("PAID:SUCCEEDED", "EXPIRED:REFUNDED"))
        return state(oid)
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(race, range(10, 34)))
    check("payment_expiry_races", {value: outcomes.count(value) for value in set(outcomes)})

    command("stop", "rabbitmq")
    oid = order(40, 140)
    assert int(sql("SELECT count(*) FROM outbox_events WHERE published_at IS NULL")) > 0
    command("start", "rabbitmq")
    eventually("outbox drains after broker recovery", lambda: sql("SELECT count(*) FROM outbox_events WHERE published_at IS NULL") == "0")
    check("broker_outage_preserves_checkout_and_outbox")

    command("stop", "opensearch")
    assert request("/api/search", port=18085)[0] == 503
    oid = order(41, 141)
    eventually("failed search delivery reaches DLQ", lambda: queue_count("search.dlq") > 0)
    command("start", "opensearch")
    eventually("search ready after restart", lambda: request("/readyz", port=18085)[0] == 200)
    replay = ok("/api/admin/queues/search/replay", "admin", "POST")
    assert replay["replayed"] > 0
    eventually("DLQ drained", lambda: queue_count("search.dlq") == 0)
    check("search_outage_checkout_and_dlq_replay", replay)

    # Force a publisher crash at the acknowledged-but-not-marked boundary.
    command("stop", "worker-2")
    before = int(subprocess.check_output(["docker", "inspect", "--format", "{{.RestartCount}}", f"{PROJECT}-worker-1-1"], text=True))
    sql("INSERT INTO demo_faults VALUES('publisher_after_confirm');")
    ok("/api/admin/products/150", "admin", "PUT", {"name": "Чайник для кухни", "priceMinor": 25000, "active": True})
    eventually("publisher process crash", lambda: int(subprocess.check_output(["docker", "inspect", "--format", "{{.RestartCount}}", f"{PROJECT}-worker-1-1"], text=True)) > before)
    eventually("publisher lease recovery", lambda: sql("SELECT count(*) FROM outbox_events WHERE published_at IS NULL") == "0")
    check("publisher_crash_after_confirm_recovers")

    command("stop", "worker-1")
    sql("INSERT INTO demo_faults VALUES('consumer_after_effect_notifications');")
    oid = order(42, 142)
    event = sql(f"SELECT event_id FROM outbox_events WHERE aggregate_id='{oid}' AND event_type='OrderCreated'")
    before = int(subprocess.check_output(["docker", "inspect", "--format", "{{.RestartCount}}", f"{PROJECT}-worker-1-1"], text=True))
    command("start", "worker-1")
    eventually("consumer process crash", lambda: int(subprocess.check_output(["docker", "inspect", "--format", "{{.RestartCount}}", f"{PROJECT}-worker-1-1"], text=True)) >= 1)
    eventually("consumer reprocessed", lambda: sql(f"SELECT count(*) FROM consumer_inbox WHERE consumer='notifications' AND event_id='{event}'") == "1")
    assert sql(f"SELECT count(*) FROM provider.notifications WHERE event_id='{event}'") == "1"
    command("start", "worker-2")
    check("consumer_crash_after_external_effect_one_notification")

    job = ok("/api/admin/search/rebuilds", "admin", "POST")
    eventually("rebuild registered before scan", lambda: sql(f"SELECT count(*) FROM search_indexes WHERE name='{job['indexName']}'") == "1")
    updated = ok("/api/admin/products/150", "admin", "PUT", {"name": "Чайники кухонные новые", "priceMinor": 12345, "active": True})
    eventually("rebuild done", lambda: sql(f"SELECT state FROM search_rebuilds WHERE id='{job['id']}'") == "DONE", 240)
    eventually("Russian stemming and concurrent rebuild", lambda: any(p["id"] == 150 and p["priceMinor"] == 12345 for p in ok("/api/search?q=" + urllib.parse.quote("чайник"))["items"]))
    code, _ = request(f"/products/_doc/150?version=1&version_type=external_gte", method="PUT", body={"id": 150, "version": 1, "active": False}, port=19200)
    assert code == 409
    ok("/api/admin/products/150", "admin", "PUT", {"name": "Чайник скрытый", "priceMinor": 12345, "active": False})
    eventually("inactive tombstone filtered", lambda: ok("/api/search?q=" + urllib.parse.quote("чайник"))["total"] == 0)
    check("search_rebuild_concurrent_updates_stale_version_and_tombstone")
    fresh_started = time.monotonic()
    fresh = ok("/api/admin/products/151", "admin", "PUT", {"name": "stage4fresh", "priceMinor": 12346, "active": True})
    eventually("search freshness", lambda: any(p["id"] == 151 and p["version"] == fresh["version"] for p in ok("/api/search?q=stage4fresh")["items"]), 10)
    check("search_update_visible_seconds", round(time.monotonic() - fresh_started, 3))
    ok("/api/admin/products/150", "admin", "PUT", {"name": "Product 150", "priceMinor": 25000, "active": True})
    eventually("restore catalog fixture", lambda: ok("/api/search")["total"] == 100000)

    image = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/Zz8AAAAASUVORK5CYII=")
    boundary = "boundary" + uuid.uuid4().hex
    body = f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="image.png"\r\nContent-Type: image/png\r\n\r\n'.encode() + image + f"\r\n--{boundary}--\r\n".encode()
    uploaded = ok("/api/admin/products/160/images", "admin", "POST", body, headers={"Content-Type": "multipart/form-data; boundary=" + boundary})
    key = uploaded["objectKey"]
    assert ok("/" + key, port=18086) == image
    assert request("/" + key, method="HEAD", port=18086)[0] == 200
    assert request("/" + key, method="PUT", body=b"bad", port=18086)[0] == 403
    assert uploaded["url"] in ok("/api/products/160")["images"]
    # Model crash after PUT, before metadata commit; bypass the media cache when checking deletion.
    sql(f"DELETE FROM product_images WHERE object_key='{key}'; UPDATE media_uploads SET state='PENDING',created_at=now()-interval '2 hours' WHERE object_key='{key}';")
    eventually("orphan marked for cleanup", lambda: sql(f"SELECT state FROM media_uploads WHERE object_key='{key}'") == "CLEANUP", 90)
    # A cache MISS query parameter reaches Garage, whose deleted object must be absent.
    eventually("orphan object removed", lambda: request("/" + key + "?bypass=" + uuid.uuid4().hex, port=18086)[0] == 404)
    check("garage_put_get_head_readonly_media_and_orphan_cleanup")

    poison = str(uuid.uuid4())
    sql(f"INSERT INTO outbox_events(event_id,aggregate_type,aggregate_id,aggregate_version,event_type,payload,schema_version) VALUES('{poison}','Product','160',99999,'ProductUpdated','{{}}',999);")
    eventually("poison quarantined", lambda: queue_count("search.dlq") > 0)
    check("unsupported_schema_bounded_retries_dlq")

    violations = int(sql("""
        SELECT (SELECT count(*) FROM inventory WHERE reserved<0 OR reserved>on_hand) +
          (SELECT count(*) FROM inventory i WHERE reserved <> coalesce((SELECT sum(ri.quantity) FROM reservation_items ri JOIN reservations r ON r.id=ri.reservation_id WHERE ri.product_id=i.product_id AND r.state='ACTIVE'),0)) +
          (SELECT count(*) FROM orders o JOIN reservations r ON r.order_id=o.id WHERE (o.status='PAID')<>(r.state='CONFIRMED'));
        """))
    assert violations == 0
    check("inventory_order_reservation_violations", violations)
    RESULT["passed"] = True


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--existing", action="store_true", help="Use STAGE4_PROJECT; does not create/delete its volumes")
    parser.add_argument("--keep", action="store_true")
    args = parser.parse_args()
    try:
        if not args.existing:
            command("up", "-d", "--build")
            os.environ["STAGE4_PROJECT"] = PROJECT
            import init_stage4
            init_stage4.COMPOSE = COMPOSE
            eventually("Garage init", lambda: (init_stage4.initialize() or True), 60)
        run()
    finally:
        output = ROOT / "artifacts/stage4"
        output.mkdir(parents=True, exist_ok=True)
        (output / "integration.json").write_text(json.dumps(RESULT, ensure_ascii=False, indent=2) + "\n")
        (output / "services.log").write_text(command("logs", "--no-color", "--tail", "100"))
        if not args.existing and not args.keep:
            command("down", "--volumes")
    (ROOT / "docs/stage4_checks.json").write_text(json.dumps(RESULT, ensure_ascii=False, indent=2) + "\n")
