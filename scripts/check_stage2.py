#!/usr/bin/env python3
"""Real PostgreSQL/Redis/HTTP checks in a uniquely named disposable project."""
import base64
import collections
import concurrent.futures
import json
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROJECT = "hdls-stage2-check-" + uuid.uuid4().hex[:8]
COMPOSE = ["docker", "compose", "-p", PROJECT, "-f", str(ROOT / "infra/stage2/compose.yaml")]
OUTPUT = ROOT / "artifacts/stage2"


def command(*args, data=None, binary=False):
    p = subprocess.run(COMPOSE + list(args), input=data, capture_output=True, text=not binary)
    if p.returncode:
        output = p.stdout + p.stderr
        raise RuntimeError(output[-7000:].decode(errors="replace") if binary else output[-7000:])
    return p.stdout


def sql(query, database="commerce"):
    return command("exec", "-T", "postgres", "psql", "-X", "-qAt", "-v", "ON_ERROR_STOP=1", "-U", "commerce", "-d", database, data=query)


def request(path, user=None, method="GET", body=None, key=None, port=18080):
    headers = {"Content-Type": "application/json", "Connection": "close"}
    if user is not None:
        name, password = ("admin", "demo-admin") if user == "admin" else (f"buyer-{user}", f"demo-buyer-{user}")
        headers["Authorization"] = "Basic " + base64.b64encode(f"{name}:{password}".encode()).decode()
    if key: headers["Idempotency-Key"] = key
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{port}" + path, data=data, headers=headers, method=method)
    try:
        response = urllib.request.urlopen(req, timeout=40)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        content = response.read()
        try: payload = json.loads(content)
        except ValueError: payload = {"raw": content.decode(errors="replace")[:300]}
        return response.status, payload


def ok(path, user=None, method="GET", body=None, key=None, port=18080):
    status, payload = request(path, user, method, body, key, port)
    assert 200 <= status < 300, (path, status, payload)
    return payload


def wait_ready():
    until = time.monotonic() + 150
    while time.monotonic() < until:
        try:
            if all(request("/readyz", port=p)[0] == 200 for p in (18080, 18081, 18082)): return
        except OSError: pass
        time.sleep(1)
    raise RuntimeError("Java services did not become ready\n" + command("logs", "--tail", "60", "commerce-1", "commerce-2"))


def item(user, product, quantity=1):
    return ok(f"/api/cart/items/{product}", user, "PUT", {"quantity": quantity})


def checkout(user, key, body=None):
    if body is None:
        cart = ok("/api/cart", user)
        body = {"expectedCartVersion": cart["version"], "expectedTotalMinor": cart["totalMinor"]}
    return request("/api/orders", user, "POST", body, key)


def fingerprint(database):
    return json.loads(sql("""
        SELECT json_build_object(
          'inventory', (SELECT md5(string_agg(product_id||':'||on_hand||':'||reserved,'|' ORDER BY product_id)) FROM inventory),
          'orders', (SELECT md5(string_agg(id||':'||user_id||':'||status||':'||total_minor,'|' ORDER BY id)) FROM orders),
          'items', (SELECT count(*) FROM order_items),
          'reservations', (SELECT count(*) FROM reservations),
          'outbox', (SELECT count(*) FROM outbox_events),
          'idempotency', (SELECT count(*) FROM idempotency_keys));
        """, database))


def run():
    wait_ready()
    result = {"project": PROJECT, "backend_instances": 2, "client_workers": 64}
    assert request("/api/cart")[0] == 401
    assert request("/api/admin/cache-stats", 1)[0] == 403
    price = ok("/api/products/1")["priceMinor"]
    sql("INSERT INTO cart_items SELECT id,1,1 FROM users WHERE role='BUYER'; UPDATE carts SET version=1;")
    body = {"expectedCartVersion": 1, "expectedTotalMinor": price}
    before_wal = sql("SELECT pg_current_wal_lsn();").strip()
    print("Checking 1000 checkout attempts against 100 units", flush=True)
    started = time.monotonic()
    with concurrent.futures.ThreadPoolExecutor(max_workers=64) as pool:
        attempts = list(pool.map(lambda user: (user, checkout(user, "sale", body)), range(1, 1001)))
    counts = collections.Counter(status for _, (status, _) in attempts)
    assert counts == {201: 100, 409: 900}, (counts, [(u, r) for u, r in attempts if r[0] not in (201, 409)][:5])
    assert all(payload.get("code") == "OUT_OF_STOCK" for _, (status, payload) in attempts if status == 409)
    stock = json.loads(sql("SELECT row_to_json(i) FROM inventory i WHERE product_id=1;"))
    assert stock["on_hand"] == stock["reserved"] == 100, stock
    result["flash"] = {"attempts": 1000, "created": 100, "out_of_stock": 900, "elapsed_seconds": round(time.monotonic()-started, 2), "reserved": stock["reserved"]}
    result["flash"]["wal_bytes"] = int(sql(f"SELECT pg_wal_lsn_diff(pg_current_wal_lsn(),'{before_wal}');"))
    owner, (_, original) = next((u, r) for u, r in attempts if r[0] == 201)
    assert checkout(owner, "sale", body) == (201, original)
    assert checkout(owner, "sale", {**body, "expectedTotalMinor": price+1})[1]["code"] == "IDEMPOTENCY_CONFLICT"
    sql(f"DELETE FROM idempotency_keys WHERE user_id={owner} AND key='sale';")
    assert checkout(owner, "sale", body) == (201, original)
    other = 1 if owner != 1 else 2
    assert request("/api/orders/"+original["orderId"], other)[0] == 404
    result["ownership_and_durable_idempotency"] = "passed"

    item(1, 1, 0)
    cart = item(1, 3)
    duplicate_body = {"expectedCartVersion": cart["version"], "expectedTotalMinor": cart["totalMinor"]}
    with concurrent.futures.ThreadPoolExecutor(max_workers=32) as pool:
        duplicates = list(pool.map(lambda _: checkout(1, "duplicate", duplicate_body), range(64)))
    assert all(status == 201 for status, _ in duplicates), duplicates[:5]
    assert len({r["orderId"] for _, r in duplicates}) == 1
    duplicate_id = duplicates[0][1]["orderId"]
    assert int(sql("SELECT reserved FROM inventory WHERE product_id=3;")) == 1
    result["concurrent_retries"] = {"requests": 64, "orders": 1, "reserved": 1}

    item(2, 1, 0)
    ok("/api/admin/inventory/10", "admin", "PUT", {"onHand": 5})
    ok("/api/admin/inventory/11", "admin", "PUT", {"onHand": 0})
    item(2, 10)
    cart = item(2, 11)
    multi = {"expectedCartVersion": cart["version"], "expectedTotalMinor": cart["totalMinor"]}
    assert checkout(2, "atomic-fail", multi)[1]["code"] == "OUT_OF_STOCK"
    assert int(sql("SELECT reserved FROM inventory WHERE product_id=10;")) == 0
    ok("/api/admin/inventory/11", "admin", "PUT", {"onHand": 5})
    assert checkout(2, "atomic-fail", multi)[1]["code"] == "OUT_OF_STOCK"
    assert checkout(2, "atomic-success", multi)[0] == 201
    assert sql("SELECT sum(reserved) FROM inventory WHERE product_id IN (10,11);").strip() == "2"
    result["multi_sku_atomicity_and_failure_replay"] = "passed"

    for user, order in [(3, (20, 21)), (4, (21, 20))]:
        item(user, 1, 0)
        for product in order: item(user, product)
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        opposite = list(pool.map(lambda u: checkout(u, "ordered-locks"), (3, 4)))
    assert all(status == 201 for status, _ in opposite), opposite
    result["opposite_sku_order"] = "passed"

    old = ok("/api/products/30", port=18081)
    newer = ok("/api/admin/products/30", "admin", "PUT", {"name": "Updated product", "priceMinor": old["priceMinor"]+100, "active": True}, port=18082)
    refreshed = ok("/api/products/30", port=18081)
    assert refreshed["priceMinor"] == newer["priceMinor"] and refreshed["version"] >= newer["version"]
    item(5, 1, 0)
    cart = item(5, 30)
    assert checkout(5, "old-price", {"expectedCartVersion": cart["version"], "expectedTotalMinor": old["priceMinor"]})[1]["code"] == "PRICE_CHANGED"
    status, bought = checkout(5, "new-price")
    assert status == 201
    ok("/api/admin/products/30", "admin", "PUT", {"name": "Another name", "priceMinor": newer["priceMinor"]+100, "active": True})
    snapshot = ok("/api/orders/"+bought["orderId"], 5)["items"][0]
    assert snapshot["name"] == "Updated product" and snapshot["priceMinor"] == newer["priceMinor"]
    assert request("/api/admin/inventory/30", "admin", "PUT", {"onHand": 0})[1]["code"] == "STOCK_BELOW_RESERVED"
    result["cache_invalidation_authoritative_price_and_snapshot"] = "passed"

    command("exec", "-T", "redis", "redis-cli", "DEL", "product:40")
    before = ok("/api/admin/cache-stats", "admin", port=18081)["originLoads"]
    with concurrent.futures.ThreadPoolExecutor(max_workers=32) as pool:
        cards = list(pool.map(lambda _: request("/api/products/40", port=18081), range(64)))
    assert all(status == 200 for status, _ in cards)
    delta = ok("/api/admin/cache-stats", "admin", port=18081)["originLoads"] - before
    assert delta == 1, delta
    result["singleflight"] = {"requests": 64, "origin_loads": delta}
    command("stop", "redis")
    try:
        assert request("/api/products/50")[0] == 200
        item(6, 1, 0)
        item(6, 50)
        assert checkout(6, "without-redis")[0] == 201
    finally:
        command("start", "redis")
    result["redis_failure_keeps_catalog_and_checkout"] = "passed"

    sql(f"UPDATE orders SET expires_at=now()-interval '1 second' WHERE id='{duplicate_id}'; UPDATE reservations SET expires_at=now()-interval '1 second' WHERE order_id='{duplicate_id}';")
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        expiration = list(pool.map(lambda _: ok("/api/admin/reservations/expire", "admin", "POST"), range(2)))
    assert sum(r["expired"] for r in expiration) == 1, expiration
    assert ok("/api/admin/reservations/expire", "admin", "POST")["expired"] == 0
    assert int(sql("SELECT reserved FROM inventory WHERE product_id=3;")) == 0
    result["concurrent_expiration_once"] = "passed"
    mismatches = int(sql("""
        SELECT count(*) FROM inventory i LEFT JOIN (
          SELECT ri.product_id,sum(ri.quantity) qty FROM reservation_items ri
          JOIN reservations r ON r.id=ri.reservation_id WHERE r.state='ACTIVE' GROUP BY ri.product_id
        ) a ON a.product_id=i.product_id WHERE i.reserved<>coalesce(a.qty,0) OR i.reserved<0 OR i.reserved>i.on_hand;
        """))
    assert mismatches == 0, mismatches
    result["inventory_aggregate_invariant"] = "passed"

    print("Collecting plans on 100000 products and 200000 historical orders", flush=True)
    sql((ROOT / "infra/stage2/history_fixture.sql").read_text())
    queries = {
        "card": "SELECT p.id,p.name,pr.price_minor,i.on_hand-i.reserved FROM products p JOIN product_prices pr ON pr.product_id=p.id JOIN inventory i ON i.product_id=p.id WHERE p.id=99999 AND p.active",
        "category": "WITH selected AS MATERIALIZED (SELECT * FROM products WHERE active AND category_id=1 AND id>20000 ORDER BY id LIMIT 20) SELECT p.id,p.sku,p.category_id,p.name,p.description,p.active,p.version,pr.price_minor,pr.currency,i.on_hand-i.reserved AS available FROM selected p JOIN product_prices pr ON pr.product_id=p.id JOIN inventory i ON i.product_id=p.id ORDER BY p.id",
        "history": "SELECT id,total_minor FROM orders WHERE user_id=999 AND (created_at,id)<(now()-interval '32 days','ffffffff-ffff-ffff-ffff-ffffffffffff'::uuid) ORDER BY created_at DESC,id DESC LIMIT 20",
        "expiry": "SELECT id FROM orders WHERE status='PENDING_PAYMENT' AND expires_at<now() ORDER BY expires_at,id LIMIT 100 FOR UPDATE SKIP LOCKED",
        "outbox": "SELECT event_id FROM outbox_events WHERE published_at IS NULL ORDER BY created_at,event_id LIMIT 100",
    }
    plans = {name: json.loads(sql("EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) " + query)) for name, query in queries.items()}
    plan_summary = {}
    def nodes(plan):
        yield plan
        for child in plan.get("Plans", []): yield from nodes(child)
    for name, plan in plans.items():
        indexes = [n["Index Name"] for n in nodes(plan[0]["Plan"]) if "Index Name" in n]
        assert indexes, (name, plan)
        plan_summary[name] = {"indexes": indexes, "execution_ms": plan[0]["Execution Time"]}
    result["query_plans"] = plan_summary
    result["relation_sizes"] = json.loads(sql("SELECT json_agg(x) FROM (SELECT relname,pg_table_size(relid) table_bytes,pg_indexes_size(relid) index_bytes FROM pg_catalog.pg_statio_user_tables ORDER BY relname) x;"))
    print("Verifying pg_dump/pg_restore in a separate empty database", flush=True)
    original = fingerprint("commerce")
    started = time.monotonic()
    dump = command("exec", "-T", "postgres", "pg_dump", "-Fc", "--no-owner", "--no-acl", "-U", "commerce", "-d", "commerce", binary=True)
    backup_seconds = time.monotonic() - started
    restore_db = "restore_" + uuid.uuid4().hex[:8]
    command("exec", "-T", "postgres", "createdb", "-T", "template0", "-U", "commerce", restore_db)
    started = time.monotonic()
    command("exec", "-T", "postgres", "pg_restore", "--no-owner", "--no-acl", "--exit-on-error", "--single-transaction", "-U", "commerce", "-d", restore_db, data=dump, binary=True)
    restore_seconds = time.monotonic() - started
    assert fingerprint(restore_db) == original
    result["restore"] = {"dump_bytes": len(dump), "backup_seconds": round(backup_seconds, 2), "restore_seconds": round(restore_seconds, 2), "fingerprints_match": True, "orders": int(sql("SELECT count(*) FROM orders;", restore_db)), "items": int(sql("SELECT count(*) FROM order_items;", restore_db))}
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "results.json").write_text(json.dumps(result, indent=2, ensure_ascii=False)+"\n")
    (ROOT / "docs/stage2_query_plans.json").write_text(json.dumps(plans, indent=2)+"\n")
    print(json.dumps({k:v for k,v in result.items() if k!="relation_sizes"}, indent=2, ensure_ascii=False), flush=True)


def main():
    try:
        print("Starting isolated PostgreSQL/Redis/two-instance Java project " + PROJECT, flush=True)
        command("up", "-d", "--build")
        run()
    except Exception:
        print(command("logs", "--tail", "40", "commerce-1", "commerce-2"), flush=True)
        raise
    finally:
        # Only the randomly named test project's disposable volume is removed.
        command("down", "-v")


if __name__ == "__main__": main()
