#!/usr/bin/env python3
"""Summarize local admission and soak measurements, then verify the README table."""
import argparse
import json
import os
import statistics
from pathlib import Path

from analyze_stage3 import analyze_jfr, meets_objectives
from calculate_capacity import number, table

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "artifacts/stage3-followup"
TARGET = ROOT / "docs/stage3_followup_results.json"


def summary(data):
    rows = []
    for profile in data["profiles"]:
        latency = profile["latency_ms"]
        rows.append([profile["run"], profile["duration"], number(profile["achieved_primary_rps"]),
                     number(latency["p(95)"]), number(latency["p(99)"]),
                     int(profile["technical_errors"]["passes"]), profile["dropped_iterations"],
                     "пройден" if profile["objectives_met"] else "пороги превышены"])
    return table(["Проверка", "Секунды", "RPS", "p95, мс", "p99, мс", "Тех. ошибки", "Dropped", "Результат"], rows)


def memory_windows(samples, instance):
    rows = [(sample["elapsed_seconds"], sample["jvm"][instance]) for sample in samples
            if instance in sample.get("jvm", {}) and "process_kib" in sample["jvm"][instance]]
    end = max(time for time, _ in rows)
    output = {}
    for label, low, high in (("minutes_10_to_15", 600, 900), ("last_5_minutes", end - 300, end)):
        values = [row["process_kib"]["VmRSS"] * 1024 for time, row in rows if low <= time <= high]
        if values:
            output[label + "_rss_mean_bytes"] = round(statistics.mean(values), 2)
    for label, metric in (("pending", "pool_pending"), ("heap_used_bytes", "heap_used")):
        values = [row[metric][0]["value"] for _, row in rows if row.get(metric)]
        output[label + "_max"] = max(values, default=0)
    gc = [{item["statistic"]: item["value"] for item in row["gc_pause"]} for _, row in rows if row.get("gc_pause")]
    if len(gc) > 1:
        output["gc_pause_count_delta"] = gc[-1]["COUNT"] - gc[0]["COUNT"]
        output["gc_pause_total_seconds_delta"] = gc[-1]["TOTAL_TIME"] - gc[0]["TOTAL_TIME"]
    return output


def make_chart(profiles):
    os.environ.setdefault("MPLCONFIGDIR", str(OUTPUT / "matplotlib"))
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    soaks = [profile for profile in profiles if profile["run"].startswith("soak-")]
    if not soaks:
        return
    figure, axes = plt.subplots(3, len(soaks), figsize=(12, 9), squeeze=False, layout="constrained")
    for column, profile in enumerate(soaks):
        minute = profile["minute_metrics"]
        axes[0, column].plot([row["minute"] for row in minute], [row["latency_ms"]["p(95)"] for row in minute], label="p95")
        axes[0, column].plot([row["minute"] for row in minute], [row["latency_ms"]["p(99)"] for row in minute], label="p99")
        axes[0, column].set_title(str(profile["requestedRate"]) + " primary RPS")
        axes[0, column].set_ylabel("HTTP duration, ms")
        samples = json.loads((OUTPUT / (profile["run"] + "-resources.json")).read_text())
        for instance in ("commerce-1", "commerce-2"):
            rows = [(sample["elapsed_seconds"] / 60, sample["jvm"][instance]) for sample in samples
                    if instance in sample.get("jvm", {}) and "process_kib" in sample["jvm"][instance]]
            axes[1, column].plot([time for time, _ in rows], [row["process_kib"]["VmRSS"] / 1024 for _, row in rows], label=instance)
        groups = {}
        for sample in samples:
            for row in sample.get("containers", []):
                name = row["Name"].removeprefix(profile["project"] + "-").rsplit("-", 1)[0]
                if name in ("commerce-1", "commerce-2", "postgres"):
                    groups.setdefault(name, []).append((sample["elapsed_seconds"] / 60, float(row["CPUPerc"].rstrip("%"))))
        for name, rows in groups.items():
            axes[2, column].plot([time for time, _ in rows], [cpu for _, cpu in rows], label=name, linewidth=0.9)
        axes[1, column].set_ylabel("JVM RSS, MiB")
        axes[2, column].set_ylabel("Docker CPU, % of one CPU")
        for axis in axes[:, column]:
            axis.set_xlabel("Elapsed minutes")
            axis.grid(alpha=0.25)
            axis.legend(fontsize=8)
    figure.savefig(ROOT / "docs/stage3_followup_load.png", dpi=160)
    plt.close(figure)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jfr", action="store_true")
    parser.add_argument("--plot", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    readme_path = ROOT / "README.md"
    readme = readme_path.read_text()
    start, end = "<!-- stage3-followup:start -->", "<!-- stage3-followup:end -->"
    assert readme.count(start) == readme.count(end) == 1
    before, remaining = readme.split(start)
    _, after = remaining.split(end)
    if args.check:
        data = json.loads(TARGET.read_text())
        assert readme == before + start + "\n" + summary(data) + "\n" + end + after
        print("Followup measurements and README are consistent")
        return
    data = json.loads((OUTPUT / "results.json").read_text())
    for profile in data["profiles"]:
        profile["project"] = data["project"]
        profile["objectives_met"] = meets_objectives(profile) and not profile["failed_thresholds"]
        samples = json.loads((OUTPUT / (profile["run"] + "-resources.json")).read_text())
        if profile["run"].startswith("soak-"):
            profile["memory_windows"] = {instance: memory_windows(samples, instance) for instance in ("commerce-1", "commerce-2")}
        profile["top_sql_by_exec_time"] = json.loads((OUTPUT / (profile["run"] + "-sql.json")).read_text())["after"]["statements"][:5]
    rolling = data["rolling"]
    rolling["observations"] = [{"elapsed_seconds": row["elapsed_seconds"],
                                "c1": row["servers"]["commerce/c1"], "c2": row["servers"]["commerce/c2"]}
                               for row in rolling.pop("samples")]
    integration = OUTPUT / "integration-check.json"
    if integration.exists():
        checks = json.loads(integration.read_text())
        data["functional_recheck"] = {key: value for key, value in checks.items() if key not in ("query_plans", "relation_sizes")}
    diagnostics = OUTPUT / "server-error-routes.json"
    if diagnostics.exists():
        routes = json.loads(diagnostics.read_text())
        if routes["project"] == data["project"]:
            data["server_error_routes"] = routes
    additional_memory = OUTPUT / "additional-memory.jsonl"
    if additional_memory.exists():
        data["additional_jvm_memory_observations"] = [row for row in
            (json.loads(line) for line in additional_memory.read_text().splitlines() if line)
            if row.get("project") == data["project"]]
    if args.jfr:
        data["jfr"] = {path.stem: analyze_jfr(path) for path in sorted(OUTPUT.glob("*.jfr"))}
    TARGET.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    readme_path.write_text(before + start + "\n" + summary(data) + "\n" + end + after)
    if args.plot:
        make_chart(data["profiles"])
    print(summary(data))


if __name__ == "__main__":
    main()
