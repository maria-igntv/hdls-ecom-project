"""Shared, project-scoped stage-5 controls; never changes other Compose projects."""
import json
import os
import time
import urllib.parse
from pathlib import Path
import check_stage4 as api

ROOT = Path(__file__).resolve().parents[1]
PROJECT = os.environ.get("STAGE5_PROJECT", "hdls-stage5")
COMPOSE = ["docker", "compose", "-p", PROJECT, "-f", str(ROOT / "infra/stage4/compose.yaml"), "-f", str(ROOT / "infra/stage5/compose.yaml")]
api.PROJECT = PROJECT
api.COMPOSE = COMPOSE
OUTPUT = ROOT / "artifacts/stage5"
OUTPUT.mkdir(parents=True, exist_ok=True)


def prom(query):
    return api.ok("/api/v1/query?query=" + urllib.parse.quote(query, safe=""), port=19090)["data"]["result"]


def alert_firing(name):
    return bool(prom('ALERTS{alertstate="firing",alertname="' + name + '"}'))


def save(name, value):
    (OUTPUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def invariants():
    return int(api.sql("""
        WITH active AS (SELECT ri.product_id,sum(ri.quantity) AS qty FROM reservation_items ri JOIN reservations r ON r.id=ri.reservation_id WHERE r.state='ACTIVE' GROUP BY ri.product_id)
        SELECT (SELECT count(*) FROM inventory i LEFT JOIN active a ON a.product_id=i.product_id WHERE i.reserved<>coalesce(a.qty,0) OR i.reserved<0 OR i.reserved>i.on_hand) +
        (SELECT count(*) FROM orders o JOIN reservations r ON r.order_id=o.id WHERE (o.status='PAID')<>(r.state='CONFIRMED'));
        """))


def wait(label,predicate,timeout=120):
    deadline=time.monotonic()+timeout
    last=None
    while time.monotonic()<deadline:
        try:
            last=predicate()
            if last:return last
        except (OSError,ValueError,AssertionError,RuntimeError) as e:last=str(e)[-500:]
        time.sleep(0.5)
    raise AssertionError(f"Timed out: {label}: {last}")
