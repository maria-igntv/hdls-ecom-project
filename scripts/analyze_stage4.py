#!/usr/bin/env python3
"""Render stage-4 evidence from tracked JSON; --check detects stale README numbers."""
import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
START = "<!-- stage4-results:start -->"
END = "<!-- stage4-results:end -->"


def render():
    checks = json.loads((ROOT / "docs/stage4_checks.json").read_text())
    profiles = json.loads((ROOT / "docs/stage4_load_results.json").read_text())
    load = profiles["mixed"]
    warmup = profiles["warmup"]
    assert profiles.get("cold_cache_before_warmup") and not warmup["failed_thresholds"]
    assert checks["passed"] and checks["checks"]["inventory_order_reservation_violations"] == 0
    assert not load["failed_thresholds"] and not load["oom_killed"] and load["invariant_violations"] == 0
    assert load["dropped"] == 0
    errors = int(load["technical_errors"]["passes"])
    unexpected = int(load["operation_errors"]["passes"])
    media = int(load["media_errors"]["passes"] + load["media_errors"]["fails"])
    rss = max(load["max_rss_bytes"][name] for name in ("commerce-1", "commerce-2")) / 1024**2
    return f"""{START}
Проверки этапа 4: {len(checks['checks'])} групп проверок, нарушений инвариантов 0. Изменение товара стало видимым в поиске за {checks['checks']['search_update_visible_seconds']:.3f} с в отдельном опыте.

| Профиль | Длительность | API RPS | API p95 / p99, мс | Технические / неожиданные ошибки | Dropped |
| --- | --- | --- | --- | --- | --- |
| Пустой Redis + media | {warmup['duration']} с | {warmup['operations']/warmup['duration']:.2f} | {warmup['latency_ms']['p(95)']:.2f} / {warmup['latency_ms']['p(99)']:.2f} | {int(warmup['technical_errors']['passes'])} / {int(warmup['operation_errors']['passes'])} | {warmup['dropped']} |
| Полный API + media | {load['duration']} с | {load['operations']/load['duration']:.2f} | {load['latency_ms']['p(95)']:.2f} / {load['latency_ms']['p(99)']:.2f} | {errors} / {unexpected} | {load['dropped']} |

Дополнительно выполнено {media} media GET по {load['media_object_bytes']} байт; media p95 {load['media_latency_ms']['p(95)']:.2f} мс, ошибок {int(load['media_errors']['passes'])}. Максимальный RSS Commerce — {rss:.2f} МиБ при лимите 640 МиБ. Deadlocks — {load['deadlocks']}, OOM — 0. Пороги измерительного профиля пройдены; особенности прогрева и support traffic описаны в отчёте.
{END}"""


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    block = render()
    readme = ROOT / "README.md"
    text = readme.read_text()
    if START not in text:
        assert args.write, "Stage 4 result block is missing"
        text = text.replace("## Обеспечение надёжности", block + "\n\n## Обеспечение надёжности")
    else:
        old = text[text.index(START):text.index(END)+len(END)]
        if args.check:
            assert old == block, "Stage 4 README numbers are stale"
        text = text.replace(old, block)
    if args.write:
        readme.write_text(text)
    print("Stage 4 checks, load thresholds and README are consistent")
