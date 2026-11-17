#!/usr/bin/env python3
"""Create the compact stage 3 evidence and optional load chart from raw artifacts."""
import argparse
import collections
import json
import math
import os
import subprocess
from pathlib import Path
from calculate_capacity import calculate, number, table

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "artifacts/stage3"


def analyze_jfr(path):
    directory = path.parent
    docker = ["docker", "run", "--rm", "--network", "none", "--mount",
              f"type=bind,src={directory},dst=/recordings,readonly", "eclipse-temurin:25-jdk", "jfr"]
    summary = subprocess.run(docker + ["summary", "/recordings/" + path.name],
                             capture_output=True, text=True, check=True).stdout
    (directory / (path.stem + "-jfr-summary.txt")).write_text(summary)
    raw = subprocess.run([
        "docker", "run", "--rm", "--network", "none", "--memory", "512m", "--cpus", "1",
        "--mount", f"type=bind,src={directory},dst=/recordings,readonly",
        "--mount", f"type=bind,src={ROOT / 'scripts/JfrSummary.java'},dst=/scripts/JfrSummary.java,readonly",
        "eclipse-temurin:25-jdk", "java", "-Xmx256m", "/scripts/JfrSummary.java", "/recordings/" + path.name,
    ], capture_output=True, text=True, check=True, timeout=120).stdout
    result = {"events": {}, "gc_causes": {}, "top_allocations_by_weight": {}, "top_execution_leaf_samples": {}}
    groups = {"count": "events", "cause": "gc_causes", "allocation": "top_allocations_by_weight", "execution": "top_execution_leaf_samples"}
    for line in raw.splitlines():
        kind, key, value = line.split("\t")
        if kind == "metric":
            result[key] = round(float(value), 3)
        else:
            result[groups[kind]][key] = int(value)
    return result


def meets_objectives(profile):
    metrics = profile["latency_ms"]
    ordinary = [value for name, value in profile["by_operation_ms"].items() if name != "checkout"]
    checkout = profile["by_operation_ms"]["checkout"]
    return (profile["technical_errors"]["rate"] < 0.001
            and profile["operation_errors"]["rate"] < 0.001
            and profile["support_errors"]["rate"] < 0.001
            and profile["dropped_iterations"] == 0
            and metrics["p(95)"] < 200 and metrics["p(99)"] < 500
            and all(row["p(95)"] < 200 and row["p(99)"] < 500 for row in ordinary)
            and checkout["p(95)"] < 500 and checkout["p(99)"] < 1000)


def make_chart(profiles):
    os.environ.setdefault("MPLCONFIGDIR", str(OUTPUT / "matplotlib"))
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    figure, axes = plt.subplots(1, 2, figsize=(11, 4.2), layout="constrained")
    for instances, label, color in ((2, "2 API instances", "#1565c0"), (1, "1 API instance", "#b45309")):
        rows = sorted((row for row in profiles if row["instances"] == instances and row["run"] != "warmup"),
                      key=lambda row: row["requestedRate"])
        if not rows:
            continue
        x = [row["configuredRate"] for row in rows]
        axes[0].plot(x, [row["achieved_primary_rps"] for row in rows], "o-", color=color, label=label)
        axes[1].plot(x, [row["latency_ms"]["p(95)"] for row in rows], "o-", color=color, label=label + " p95")
        axes[1].plot(x, [row["latency_ms"]["p(99)"] for row in rows], "x--", color=color, label=label + " p99")
    maximum = max(row["requestedRate"] for row in profiles)
    axes[0].plot([0, maximum], [0, maximum], "--", color="#888888", linewidth=1, label="Offered rate")
    axes[0].set_ylabel("Completed primary requests / second")
    axes[1].set_ylabel("Primary HTTP duration, ms")
    axes[1].set_yscale("log")
    axes[1].axhline(200, linestyle=":", color="#555555", label="Aggregate p95 objective")
    for axis in axes:
        axis.set_xlabel("Configured primary arrivals / second")
        axis.grid(alpha=0.25)
        axis.legend(fontsize=8)
    figure.savefig(ROOT / "docs/stage3_load.png", dpi=160)
    plt.close(figure)


def readme_summary(data):
    rows = []
    for profile in data["profiles"]:
        latency = profile["latency_ms"]
        status = "прогрев" if profile["run"] == "warmup" else ("пройден" if profile["objectives_met"] else "пороги превышены")
        rows.append([
            profile["run"], profile["instances"], profile["duration"],
            number(profile["configuredRate"]) + " / " + number(profile["achieved_primary_rps"]),
            " / ".join(number(latency[key]) for key in ("p(50)", "p(95)", "p(99)")),
            int(profile["technical_errors"]["passes"]), profile["dropped_iterations"], status,
        ])
    output = table(["Профиль", "API", "Секунды", "RPS задано / завершено", "p50 / p95 / p99, мс", "Тех. ошибки", "Dropped", "Результат"], rows)
    if "commerce_sizing" in data:
        sizing = data["commerce_sizing"]
        output += (f'\n\nОдин экземпляр прошёл проверенный уровень {number(sizing["tested_single_instance_rps"])} RPS. '
                   f'При резерве 30% используем {number(sizing["safe_per_instance_rps"])} RPS/экземпляр. '
                   f'Для Commerce пика {number(sizing["model_peak_rps"])} RPS выбираем '
                   f'{sizing["total_instances"]} экземпляра в {sizing["az_count"]} AZ. '
                   f'После потери самой большой AZ расчётная ёмкость составляет '
                   f'{number(sizing["capacity_after_largest_az_loss_rps"])} RPS. '
                   'Перенос этой ёмкости на облачные vCPU требует повторного измерения.')
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jfr", action="store_true", help="Analyze recordings with the Java 25 JDK Docker image")
    parser.add_argument("--plot", action="store_true", help="Render the chart with matplotlib")
    parser.add_argument("--check", action="store_true", help="Verify README against the committed measurement summary")
    args = parser.parse_args()
    readme_path = ROOT / "README.md"
    readme = readme_path.read_text()
    start, end = "<!-- stage3:start -->", "<!-- stage3:end -->"
    assert readme.count(start) == readme.count(end) == 1
    before, remainder = readme.split(start)
    _, after = remainder.split(end)
    if args.check:
        if args.jfr or args.plot:
            parser.error("--check is used independently of --jfr and --plot")
        committed = json.loads((ROOT / "docs/stage3_results.json").read_text())
        updated = before + start + "\n" + readme_summary(committed) + "\n" + end + after
        assert readme == updated, "README differs from measurements, run analyze_stage3.py"
        print("Stage 3 measurement summary and README are consistent")
        return
    data = json.loads((OUTPUT / "results.json").read_text())
    model = json.loads((ROOT / "model/capacity.json").read_text())
    for profile in data["profiles"]:
        # Normalize the one-off container name emitted by Docker Compose.
        resources = profile["resources"]
        if "k6-run" in resources:
            resources["k6"] = resources.pop("k6-run")
        profile["objectives_met"] = meets_objectives(profile)
        statements = json.loads((OUTPUT / (profile["run"] + "-sql.json")).read_text())["after"]["statements"]
        profile["top_sql_by_exec_time"] = statements[:5]
        raw_samples = json.loads((OUTPUT / (profile["run"] + "-resources.json")).read_text())
        cache = collections.Counter()
        for instance in range(1, profile["instances"] + 1):
            name = f"commerce-{instance}"
            values = [sample["jvm"][name]["cache"] for sample in raw_samples
                      if name in sample.get("jvm", {}) and "cache" in sample["jvm"][name]]
            if len(values) > 1:
                cache.update({key: values[-1][key] - values[0][key] for key in values[0]})
        profile["sampled_cache_delta"] = dict(cache)
        if cache["hits"] + cache["originLoads"]:
            profile["sampled_card_hit_ratio"] = round(cache["hits"] / (cache["hits"] + cache["originLoads"]), 4)
    single = [profile["configuredRate"] for profile in data["profiles"]
              if profile["instances"] == 1 and profile["objectives_met"]]
    if single:
        capacity = max(single)
        calculated = calculate(model)
        peak = sum(operation["daily"] for operation in calculated["rows"] if operation["service"] == "Commerce")
        peak = peak / 86400 * model["peak"]["factor"]
        safe = capacity * 0.70
        healthy = math.ceil(peak / safe)
        instances = 3
        while instances - math.ceil(instances / 3) < healthy:
            instances += 1
        data["commerce_sizing"] = {
            "model_peak_rps": round(peak, 4), "tested_single_instance_rps": round(capacity, 4),
            "reserve_fraction": 0.30, "safe_per_instance_rps": round(safe, 4),
            "required_healthy_instances": healthy, "az_count": 3, "total_instances": instances,
            "capacity_after_largest_az_loss_rps": round((instances - math.ceil(instances / 3)) * safe, 4),
        }
    if args.jfr:
        data["jfr"] = {path.stem: analyze_jfr(path) for path in sorted(OUTPUT.glob("commerce-*.jfr"))}
    (ROOT / "docs/stage3_results.json").write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    readme_path.write_text(before + start + "\n" + readme_summary(data) + "\n" + end + after)
    if args.plot:
        make_chart(data["profiles"])
    print(json.dumps(data.get("commerce_sizing", {}), indent=2))


if __name__ == "__main__":
    main()
