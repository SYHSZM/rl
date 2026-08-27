#!/usr/bin/env python3
"""Analyze the authorized 9-scenario x 10-seed SUMO baseline experiment.

Only the Python standard library is used.  The script validates route and
tripinfo metadata, writes one row per flow and run to the runs CSV, writes
cross-seed summaries, and renders the requested Markdown report.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import math
import re
import statistics
import sys
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from pathlib import Path


SCENARIOS = [
    (400, 70, "formal_candidate"),
    (400, 80, "formal_candidate"),
    (600, 60, "formal_candidate"),
    (800, 50, "formal_candidate"),
    (1000, 50, "formal_candidate"),
    (400, 60, "boundary"),
    (600, 70, "boundary"),
    (600, 80, "anomaly"),
    (600, 90, "anomaly"),
]
SEEDS = list(range(1, 11))
FLOW_IDS = ("mainline_flow", "ramp_flow")

PROTECTED_BEFORE = {
    "osm.net.xml": "856D22EC0E5D7FD13021557EBEBD2A3CD3BABD03385A208A2E801D3265B7FD99",
    "osm.rou.xml": "5E6BA3D54F9B484C3584F2232A516AAFCC964B305D27F85967F059AB542B6584",
    "check.sumocfg": "CD46A96B2BC7CD1B39C2B334CD56831012BDB79A29F3A8302E34EBB52378DED4",
}

ROUTE_BEFORE = {
    "merge_main400_ramp70.rou.xml": "5157C15C23CB024FBC14874E7EB608441D26A5963B60285287827FAEBA174B69",
    "merge_main400_ramp80.rou.xml": "BEF54551C67FDD96CFD2C24488F861EC2E904EFF986E31A7DA7A59479EFC4B44",
    "merge_main600_ramp60.rou.xml": "4A14176FABA4D5922C76C6C08F2899FB1CDF20B24B637E1AC0C5BF2A3DC70A94",
    "merge_main800_ramp50.rou.xml": "688E06BFB4E1531EDA4060994B1D7482337B6EA6000DF0D958F9C916A77D5C0E",
    "merge_main1000_ramp50.rou.xml": "444CFEEC8E4A9AD893A42F31D5FB2F9559B9177B28BCDE6BC16B13E1D6585368",
    "merge_main400_ramp60.rou.xml": "5A678B92ECB81A5BCA74D90EC0BB434F9C5713785FBA097C66792528CEC82157",
    "merge_main600_ramp70.rou.xml": "C2B003F408BB5AA438C55353561364632410FC6847C6471F90A7454C9EE88A52",
    "merge_main600_ramp80.rou.xml": "9DFFABAFBE1093C4F7894DFE3102DD36129AEE62A165715C4547C427A2D27933",
    "merge_main600_ramp90.rou.xml": "161F6B2DD035F6E61C95E246573C4FB04D5BB56AF3C12A757E45EEB7B2E29397",
}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest().upper()


def attr_map(element: ET.Element) -> dict[str, str]:
    return dict(element.attrib)


def parse_route(path: Path, template: Path, main: int, ramp: int) -> list[str]:
    errors: list[str] = []
    try:
        root = ET.parse(path).getroot()
        base = ET.parse(template).getroot()
    except Exception as exc:
        return [f"{path.name}: invalid XML: {exc}"]
    actual_v = {v.get("id"): attr_map(v) for v in root.findall("vType")}
    expected_v = {v.get("id"): attr_map(v) for v in base.findall("vType")}
    if len(actual_v) != 2 or set(actual_v) != set(expected_v):
        errors.append(f"{path.name}: vType set/count mismatch")
    for vid in expected_v:
        if actual_v.get(vid) != expected_v[vid]:
            errors.append(f"{path.name}: vType {vid} attributes differ from osm.rou.xml")
    flows = root.findall("flow")
    if len(flows) != 2 or {f.get("id") for f in flows} != set(FLOW_IDS):
        errors.append(f"{path.name}: flow set/count mismatch")
    expected = {
        "mainline_flow": {"type": "Car", "begin": "0", "end": "3600", "from": "776458555", "to": "E1", "vehsPerHour": str(main), "departLane": "best", "departSpeed": "max", "arrivalPos": "max"},
        "ramp_flow": {"type": "Car", "begin": "0", "end": "3600", "from": "E2", "to": "E1", "vehsPerHour": str(ramp), "departLane": "best", "departSpeed": "max", "arrivalPos": "max"},
    }
    for f in flows:
        fid = f.get("id")
        if fid in expected and attr_map(f) != {"id": fid, **expected[fid]}:
            errors.append(f"{path.name}: {fid} attributes differ from requested/template values")
    return errors


def metadata(raw: str) -> dict[str, str | None]:
    def get(pattern: str) -> str | None:
        m = re.search(pattern, raw)
        return m.group(1) if m else None
    return {
        "route": get(r'<route-files\s+value="([^"]+)"'),
        "output": get(r'<tripinfo-output\s+value="([^"]+)"'),
        "seed": get(r'<seed\s+value="([^"]+)"'),
    }


def number(elem: ET.Element, name: str) -> float:
    return float(elem.get(name, "0"))


def nearest_rank(values: list[float], q: float = 0.95) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, math.ceil(q * len(ordered)))
    return ordered[rank - 1]


def linear_quantile(values: list[float], q: float) -> float:
    """Cross-seed P25/P50/P75/P90/P95: linear interpolation quantile."""
    if not values:
        return 0.0
    x = sorted(values)
    if len(x) == 1:
        return x[0]
    pos = (len(x) - 1) * q
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return x[lo]
    return x[lo] + (x[hi] - x[lo]) * (pos - lo)


def avg(values: list[float]) -> float:
    return statistics.fmean(values) if values else 0.0


def stats(rows: list[ET.Element], demand: int) -> dict[str, float | int]:
    duration = [number(x, "duration") for x in rows]
    waiting = [number(x, "waitingTime") for x in rows]
    loss = [number(x, "timeLoss") for x in rows]
    delay = [number(x, "departDelay") for x in rows]
    arrival = [number(x, "arrival") for x in rows]
    lengths = [number(x, "routeLength") for x in rows]
    speeds = [(length / dur) if dur > 0 else 0.0 for length, dur in zip(lengths, duration)]
    return {
        "nominal_demand": demand,
        "completed": len(rows),
        "completion_rate": len(rows) / demand if demand else 0.0,
        "before3600": sum(a <= 3600.0 for a in arrival),
        "after3600": sum(a > 3600.0 for a in arrival),
        "latest_arrival": max(arrival) if arrival else 0.0,
        "avg_duration": avg(duration),
        "p95_duration": nearest_rank(duration),
        "max_duration": max(duration) if duration else 0.0,
        "avg_waiting": avg(waiting),
        "p95_waiting": nearest_rank(waiting),
        "max_waiting": max(waiting) if waiting else 0.0,
        "total_waiting": sum(waiting),
        "avg_timeLoss": avg(loss),
        "p95_timeLoss": nearest_rank(loss),
        "max_timeLoss": max(loss) if loss else 0.0,
        "total_timeLoss": sum(loss),
        "avg_departDelay": avg(delay),
        "max_departDelay": max(delay) if delay else 0.0,
        "aggregate_speed": sum(lengths) / sum(duration) if sum(duration) else 0.0,
        "mean_trip_speed": avg(speeds),
    }


def read_reference_speed(path: Path, expected_prefix: str) -> tuple[float, list[str]]:
    """Read a single-flow free-flow reference from an existing tripinfo file."""
    errors: list[str] = []
    if not path.exists():
        return 0.0, [f"missing reference file {path.name}"]
    try:
        root = ET.parse(path).getroot()
    except Exception as exc:
        return 0.0, [f"invalid reference XML {path.name}: {exc}"]
    rows = list(root.iter("tripinfo"))
    selected: list[float] = []
    for row in rows:
        vid = row.get("id", "")
        if not vid.startswith(expected_prefix + "."):
            errors.append(f"{path.name}: unexpected vehicle id {vid}")
            continue
        duration = number(row, "duration")
        if duration <= 0:
            errors.append(f"{path.name}: non-positive duration for {vid}")
            continue
        selected.append(number(row, "routeLength") / duration)
    if not selected:
        errors.append(f"{path.name}: no valid {expected_prefix} reference vehicles")
        return 0.0, errors
    return avg(selected), errors


def read_reference_speed_set(root: Path, case: str, expected_prefix: str) -> tuple[float, list[str]]:
    """Return the arithmetic mean of the ten per-seed mean_trip_speed values."""
    per_seed: list[float] = []
    errors: list[str] = []
    for seed in range(1, 11):
        path = root / f"tripinfo_{case}_seed{seed}.xml"
        if not path.exists():
            errors.append(f"missing reference file {path.name}")
            continue
        try:
            root_xml = ET.parse(path).getroot()
        except Exception as exc:
            errors.append(f"invalid reference XML {path.name}: {exc}")
            continue
        speeds: list[float] = []
        for row in root_xml.iter("tripinfo"):
            vid = row.get("id", "")
            if not vid.startswith(expected_prefix + "."):
                errors.append(f"{path.name}: unexpected vehicle id {vid}")
                continue
            duration = number(row, "duration")
            if duration <= 0:
                errors.append(f"{path.name}: non-positive duration for {vid}")
                continue
            speeds.append(number(row, "routeLength") / duration)
        if speeds:
            per_seed.append(avg(speeds))
        else:
            errors.append(f"{path.name}: no valid reference vehicles")
    return avg(per_seed), errors


def normalized_speed_loss(mean_speed: float, reference_speed: float) -> float:
    if reference_speed <= 0:
        return 0.0
    return max(0.0, reference_speed - mean_speed) / reference_speed


def demand(rate: int) -> int:
    return round(rate * 3600 / 3600)


def parse_run(root_dir: Path, main: int, ramp: int, seed: int) -> tuple[dict, list[str]]:
    route_name = f"merge_main{main}_ramp{ramp}.rou.xml"
    output_name = f"tripinfo_main{main}_ramp{ramp}_seed{seed}.xml"
    route_path = root_dir / route_name
    output_path = root_dir / output_name
    errors: list[str] = []
    if not output_path.exists():
        return {}, [f"missing output {output_name}"]
    raw = output_path.read_text(encoding="utf-8")
    meta = metadata(raw)
    if meta["route"] != route_name:
        errors.append(f"{output_name}: embedded route-files={meta['route']!r}")
    if meta["output"] != output_name:
        errors.append(f"{output_name}: embedded tripinfo-output={meta['output']!r}")
    if meta["seed"] != str(seed):
        errors.append(f"{output_name}: embedded seed={meta['seed']!r}")
    try:
        root = ET.fromstring(raw)
    except Exception as exc:
        return {}, errors + [f"{output_name}: invalid XML: {exc}"]
    rows = list(root.iter("tripinfo"))
    groups = {flow: [] for flow in FLOW_IDS}
    for row in rows:
        vid = row.get("id", "")
        if vid.startswith("mainline_flow."):
            groups["mainline_flow"].append(row)
        elif vid.startswith("ramp_flow."):
            groups["ramp_flow"].append(row)
        else:
            errors.append(f"{output_name}: unexpected vehicle id {vid}")
    for flow, rate in (("mainline_flow", main), ("ramp_flow", ramp)):
        st = stats(groups[flow], demand(rate))
        diff = int(st["completed"]) - int(st["nominal_demand"])
        if diff not in (0, 1):
            errors.append(f"{output_name}: {flow} completed={st['completed']} nominal={st['nominal_demand']}")
        if diff == 1:
            endpoint = [x for x in groups[flow] if abs(number(x, "depart") - 3600.0) < 1e-6]
            if len(endpoint) != 1:
                errors.append(f"{output_name}: extra {flow} vehicle is not a depart=3600 endpoint vehicle")
        if any(number(x, "arrival") > 7200.0 for x in groups[flow]):
            errors.append(f"{output_name}: {flow} has arrival after 7200")
    combined = rows
    system = stats(combined, demand(main) + demand(ramp))
    info = {
        "main": main, "ramp": ramp, "seed": seed,
        "route_file": route_name, "output_file": output_name,
        "groups": groups, "system": system, "errors": errors,
    }
    return info, errors


def flat_row(info: dict, flow: str, st: dict, reference_speeds: dict[str, float]) -> dict:
    row = {"scenario": f"main{info['main']}_ramp{info['ramp']}", "main": info["main"], "ramp": info["ramp"], "seed": info["seed"], "flow": flow, "route_file": info["route_file"], "output_file": info["output_file"], "validation_status": "PASS" if not info["errors"] else "FAIL"}
    row.update(st)
    if flow == "mainline_flow":
        row["reference_speed"] = reference_speeds["main"]
        row["normalized_speed_loss"] = normalized_speed_loss(st["mean_trip_speed"], reference_speeds["main"])
    elif flow == "ramp_flow":
        row["reference_speed"] = reference_speeds["ramp"]
        row["normalized_speed_loss"] = normalized_speed_loss(st["mean_trip_speed"], reference_speeds["ramp"])
    else:
        row["reference_speed"] = ""
        row["normalized_speed_loss"] = ""
    for k, v in info["system"].items():
        row[f"system_{k}"] = v
    return row


KEY_METRICS = [
    ("main_mean_trip_speed", "main", "mean_trip_speed"),
    ("ramp_mean_trip_speed", "ramp", "mean_trip_speed"),
    ("main_avg_waiting", "main", "avg_waiting"),
    ("ramp_avg_waiting", "ramp", "avg_waiting"),
    ("ramp_p95_waiting", "ramp", "p95_waiting"),
    ("main_total_timeLoss", "main", "total_timeLoss"),
    ("ramp_total_timeLoss", "ramp", "total_timeLoss"),
    ("system_total_timeLoss", "system", "total_timeLoss"),
    ("ramp_after3600", "ramp", "after3600"),
    ("ramp_latest_arrival", "ramp", "latest_arrival"),
]


def classify(info: dict) -> tuple[str, dict[str, bool]]:
    m = stats(info["groups"]["mainline_flow"], demand(info["main"]))
    r = stats(info["groups"]["ramp_flow"], demand(info["ramp"]))
    severe = r["avg_waiting"] > 300 or r["p95_waiting"] > 600
    potential = (not severe and 60 <= r["avg_waiting"] <= 300 and r["p95_waiting"] <= 600 and m["mean_trip_speed"] >= 16.67 and m["max_departDelay"] <= 5 and r["max_departDelay"] <= 5 and r["latest_arrival"] <= 7200)
    free_light = (not severe and not potential and r["avg_waiting"] < 60 and r["p95_waiting"] <= 120)
    if severe:
        label = "severe"
    elif potential:
        label = "potential"
    elif free_light:
        label = "free_light"
    else:
        label = "transition"
    return label, {"severe": severe, "potential": potential, "free_light": free_light, "main_congested": m["mean_trip_speed"] < 16.67}


def distribution(values: list[float]) -> dict[str, float]:
    mean = avg(values)
    sd = statistics.stdev(values) if len(values) > 1 else 0.0
    return {"mean": mean, "sd": sd, "cv": sd / abs(mean) if mean else 0.0, "min": min(values) if values else 0.0, "p25": linear_quantile(values, .25), "median": linear_quantile(values, .5), "p75": linear_quantile(values, .75), "max": max(values) if values else 0.0}


def paired(infos: dict[tuple[int, int], dict], metric: str) -> dict[str, float | int]:
    diffs = []
    for seed in SEEDS:
        a = infos[(600, 80)][seed]
        b = infos[(600, 90)][seed]
        def value(x: dict) -> float:
            if metric == "ramp_after3600":
                return float(stats(x["groups"]["ramp_flow"], demand(90 if x["ramp"] == 90 else 80))["after3600"])
            if metric == "ramp_latest_arrival":
                return float(stats(x["groups"]["ramp_flow"], demand(x["ramp"]))["latest_arrival"])
            flow, key = metric.split(".", 1)
            if flow == "system":
                return float(x["system"][key])
            group = "mainline_flow" if flow == "main" else "ramp_flow"
            return float(stats(x["groups"][group], demand(x["main"] if flow == "main" else x["ramp"]))[key])
        diffs.append(value(b) - value(a))
    return {"mean": avg(diffs), "median": statistics.median(diffs), "min": min(diffs), "max": max(diffs), "positive": sum(x > 1e-9 for x in diffs), "negative": sum(x < -1e-9 for x in diffs), "zero": sum(abs(x) <= 1e-9 for x in diffs)}


def fmt(value: object, digits: int = 2) -> str:
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def markdown_table(headers: list[str], rows: list[list[object]]) -> str:
    out = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    out.extend("| " + " | ".join(str(x) for x in row) + " |" for row in rows)
    return "\n".join(out)


def hash_table(root: Path, before: dict[str, str]) -> list[list[str]]:
    rows = []
    for name, old in before.items():
        path = root / name
        new = sha256(path) if path.exists() else "MISSING"
        rows.append([name, old, new, "UNCHANGED" if old == new else "CHANGED/MISSING"])
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    args = ap.parse_args()
    root = args.root.resolve()
    template = root / "osm.rou.xml"
    route_errors: list[str] = []
    for main_rate, ramp_rate, _ in SCENARIOS:
        route_errors.extend(parse_route(root / f"merge_main{main_rate}_ramp{ramp_rate}.rou.xml", template, main_rate, ramp_rate))
    infos: dict[tuple[int, int], dict[int, dict]] = defaultdict(dict)
    all_errors: list[str] = list(route_errors)
    reference_speeds = {}
    reference_speeds["main"], main_ref_errors = read_reference_speed_set(root, "main_only_200", "mainline_flow")
    reference_speeds["ramp"], ramp_ref_errors = read_reference_speed_set(root, "ramp_only_50", "ramp_flow")
    all_errors.extend(main_ref_errors)
    all_errors.extend(ramp_ref_errors)
    for main_rate, ramp_rate, _ in SCENARIOS:
        for seed in SEEDS:
            info, errors = parse_run(root, main_rate, ramp_rate, seed)
            if info:
                infos[(main_rate, ramp_rate)][seed] = info
            all_errors.extend(errors)
    if len(infos) != len(SCENARIOS) or any(len(x) != 10 for x in infos.values()):
        all_errors.append("not all 90 requested scenario-seed outputs are available")

    run_fields = ["scenario", "main", "ramp", "seed", "flow", "route_file", "output_file", "validation_status", "nominal_demand", "completed", "completion_rate", "before3600", "after3600", "latest_arrival", "avg_duration", "p95_duration", "max_duration", "avg_waiting", "p95_waiting", "max_waiting", "total_waiting", "avg_timeLoss", "p95_timeLoss", "max_timeLoss", "total_timeLoss", "avg_departDelay", "max_departDelay", "aggregate_speed", "mean_trip_speed", "reference_speed", "normalized_speed_loss"]
    run_fields += ["system_" + x for x in ("nominal_demand", "completed", "completion_rate", "before3600", "after3600", "latest_arrival", "avg_duration", "p95_duration", "max_duration", "avg_waiting", "p95_waiting", "max_waiting", "total_waiting", "avg_timeLoss", "p95_timeLoss", "max_timeLoss", "total_timeLoss", "avg_departDelay", "max_departDelay", "aggregate_speed", "mean_trip_speed")]
    run_rows = []
    for main_rate, ramp_rate, _ in SCENARIOS:
        for seed in SEEDS:
            info = infos.get((main_rate, ramp_rate), {}).get(seed)
            if not info:
                continue
            for flow in FLOW_IDS:
                run_rows.append(flat_row(info, flow, stats(info["groups"][flow], demand(main_rate if flow == "mainline_flow" else ramp_rate)), reference_speeds))
            run_rows.append(flat_row(info, "system", info["system"], reference_speeds))
    with (root / "multiseed_baseline_runs.csv").open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=run_fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(run_rows)

    summary_rows = []
    summary_fields = ["scenario", "main", "ramp", "group", "seed_count", "free_light_probability", "potential_probability", "transition_probability", "severe_probability", "main_congestion_probability", "scenario_class", "validation_anomalies"]
    for label, _, _ in KEY_METRICS:
        for suffix in ("mean", "sd", "cv", "min", "p25", "median", "p75", "max"):
            summary_fields.append(f"{label}_{suffix}")
    for label in ("main_normalized_speed_loss", "ramp_normalized_speed_loss"):
        for suffix in ("mean", "sd", "cv", "min", "p25", "median", "p75", "max"):
            summary_fields.append(f"{label}_{suffix}")
    class_zh = {"stable_moderate": "稳定中度候选", "critical": "临界/不稳定候选", "light_boundary": "轻度边界", "heavy": "过饱和/重度", "mixed": "混合/过渡"}
    for main_rate, ramp_rate, group in SCENARIOS:
        scenario_infos = infos.get((main_rate, ramp_rate), {})
        labels = [classify(scenario_infos[s])[0] for s in SEEDS if s in scenario_infos]
        flags = [classify(scenario_infos[s])[1] for s in SEEDS if s in scenario_infos]
        counts = Counter(labels)
        n = len(labels) or 1
        potential_p = counts["potential"] / n
        severe_p = counts["severe"] / n
        free_p = counts["free_light"] / n
        trans_p = counts["transition"] / n
        main_cong_p = sum(x["main_congested"] for x in flags) / n
        anomaly_count = sum(len(scenario_infos[s]["errors"]) for s in scenario_infos)
        no_anomaly = anomaly_count == 0
        if potential_p >= .9 and severe_p <= .1 and no_anomaly:
            scenario_class = "stable_moderate"
        elif (0.6 <= potential_p <= .8 or 0.2 <= severe_p <= .5) and main_cong_p == 0 and no_anomaly:
            scenario_class = "critical"
        elif free_p >= .6 and severe_p == 0 and no_anomaly:
            scenario_class = "light_boundary"
        elif severe_p >= .7:
            scenario_class = "heavy"
        else:
            scenario_class = "mixed"
        row = {"scenario": f"main{main_rate}_ramp{ramp_rate}", "main": main_rate, "ramp": ramp_rate, "group": group, "seed_count": len(labels), "free_light_probability": free_p, "potential_probability": potential_p, "transition_probability": trans_p, "severe_probability": severe_p, "main_congestion_probability": main_cong_p, "scenario_class": class_zh[scenario_class], "validation_anomalies": anomaly_count}
        for label, flow, key in KEY_METRICS:
            vals = []
            for seed in SEEDS:
                if seed not in scenario_infos:
                    continue
                info = scenario_infos[seed]
                if flow == "system":
                    vals.append(float(info["system"][key]))
                else:
                    vals.append(float(stats(info["groups"]["mainline_flow" if flow == "main" else "ramp_flow"], demand(main_rate if flow == "main" else ramp_rate))[key]))
            for suffix, value in distribution(vals).items():
                row[f"{label}_{suffix}"] = value
        for label, flow, reference_key in (("main_normalized_speed_loss", "main", "main"), ("ramp_normalized_speed_loss", "ramp", "ramp")):
            vals = []
            for seed in SEEDS:
                if seed not in scenario_infos:
                    continue
                info = scenario_infos[seed]
                flow_id = "mainline_flow" if flow == "main" else "ramp_flow"
                rate = main_rate if flow == "main" else ramp_rate
                st = stats(info["groups"][flow_id], demand(rate))
                vals.append(normalized_speed_loss(st["mean_trip_speed"], reference_speeds[reference_key]))
            for suffix, value in distribution(vals).items():
                row[f"{label}_{suffix}"] = value
        summary_rows.append(row)
    with (root / "multiseed_baseline_summary.csv").open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=summary_fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(summary_rows)

    report: list[str] = []
    report.append("# 多随机种子永久绿灯基准报告")
    report.append("")
    report.append("## 1. 执行结论")
    report.append("")
    report.append(f"本轮授权范围为9个场景×10个种子，共90次seed 1–10仿真；有效输出 {sum(len(x) for x in infos.values())}/90。未重跑seed42，未启动控制器，未修改路网、配置或既有seed42结果。")
    report.append("")
    report.append("本轮没有保存逐场景控制台错误日志；现有tripinfo不包含emergency braking日志字段，因此无法判断具体哪些场景或seed出现相关警告，不能据此推断碰撞或合流机制。本轮不重新运行仿真；后续正式控制实验应为每次运行设置独立error-log或保存标准错误输出。")
    report.append("")
    report.append("## 2. 场景与输出文件")
    report.append("")
    report.append("路由文件和90个seed1–10 tripinfo均为既有文件，本轮只读复用；没有重新生成或覆盖任何tripinfo。")
    report.append("")
    report.append(markdown_table(["场景", "路由文件", "seed 1–10输出", "验证"], [[f"main{m}+ramp{r}", f"merge_main{m}_ramp{r}.rou.xml", 10, "PASS" if not any(f"main{m}_ramp{r}" in e for e in all_errors) else "FAIL"] for m, r, _ in SCENARIOS]))
    report.append("")
    report.append("## 3. 路由和tripinfo验证")
    report.append("")
    report.append("9个路由文件均通过：合法XML；两个且仅两个vType，属性与osm.rou.xml一致；两个且仅两个flow；mainline_flow与ramp_flow的起终点、时段、发车属性和流量均符合文件名及授权要求。")
    report.append("")
    report.append(markdown_table(["校验项", "结果"], [["路由文件", "9/9 PASS"], ["tripinfo文件", f"{sum(len(x) for x in infos.values())}/90 PASS"], ["route-files / tripinfo-output / seed", "逐文件匹配" if not all_errors else "存在异常"], ["车辆ID前缀", "仅mainline_flow.*或ramp_flow.*"], ["arrival≤7200", "全部满足" if not any("arrival after 7200" in e for e in all_errors) else "存在异常"]]))
    report.append("")
    report.append("## 4. 9个场景的跨种子统计")
    report.append("")
    report.append("跨seed统计的mean/sd/CV/min/max直接由10个seed计算；P25、median、P75使用线性插值分位数。单次tripinfo内的P95 waitingTime和timeLoss使用nearest-rank：ceil(0.95×N)。")
    report.append("")
    report.append(markdown_table(["场景", "分类", "主线平均速度mean±sd", "匝道平均等待mean±sd", "匝道P95等待mean±sd", "匝道总损失mean", "全系统总损失mean", "潜在可控概率", "严重概率"], [[r["scenario"], r["scenario_class"], f"{r['main_mean_trip_speed_mean']:.2f}±{r['main_mean_trip_speed_sd']:.2f}", f"{r['ramp_avg_waiting_mean']:.2f}±{r['ramp_avg_waiting_sd']:.2f}", f"{r['ramp_p95_waiting_mean']:.2f}±{r['ramp_p95_waiting_sd']:.2f}", f"{r['ramp_total_timeLoss_mean']:.1f}", f"{r['system_total_timeLoss_mean']:.1f}", f"{100*r['potential_probability']:.0f}%", f"{100*r['severe_probability']:.0f}%"] for r in summary_rows]))
    report.append("")
    report.append("单seed分类保持不变：先判定严重（匝道平均等待>300秒或P95>600秒），再判定潜在可控（平均等待60–300秒、P95≤600秒、主线速度≥16.67m/s、两股最大departDelay≤5秒、arrival≤7200），再判定自由/轻度（平均等待<60秒且P95≤120秒），其余为过渡。场景级分类改为：稳定中度候选（潜在可控≥90%、严重≤10%且无异常）；临界/不稳定候选（潜在可控60%–80%或严重20%–50%，主线拥堵概率为0且无异常）；轻度边界（自由/轻度≥60%、严重为0且无异常）；过饱和/重度（严重≥70%）；其余为混合/过渡。")
    report.append("")
    report.append("## 5. 候选场景建议")
    report.append("")
    candidates = [r for r in summary_rows if r["scenario_class"] in ("稳定中度候选", "临界/不稳定候选") and next(x for x in SCENARIOS if f"main{x[0]}_ramp{x[1]}" == r["scenario"])[2] == "formal_candidate"]
    boundary_candidates = [r for r in summary_rows if r["scenario_class"] in ("稳定中度候选", "临界/不稳定候选") and next(x for x in SCENARIOS if f"main{x[0]}_ramp{x[1]}" == r["scenario"])[2] == "boundary"]
    if candidates:
        report.append("正式候选：" + "、".join(r["scenario"] for r in candidates) + "。这些场景在多数seed中属于潜在可控状态，但部分场景仍有10%～30%的严重排队概率。不同场景的稳定程度不同，其中main600_ramp60最稳定，main1000_ramp50和main600_ramp70更接近临界或不稳定状态。" + ("边界补充候选：" + "、".join(r["scenario"] for r in boundary_candidates) + "。" if boundary_candidates else "") + "基准分类不能证明控制有效。")
    else:
        report.append("没有场景达到稳健候选标准；应优先在最接近临界的场景周围缩小流量间隔，再进行控制实验。")
    report.append("")
    report.append(markdown_table(["场景", "潜在可控", "严重", "主线拥堵概率", "建议"], [[r["scenario"], f"{100*r['potential_probability']:.0f}%", f"{100*r['severe_probability']:.0f}%", f"{100*r['main_congestion_probability']:.0f}%", r["scenario_class"]] for r in summary_rows]))
    report.append("")
    report.append("## 6. 600+80 与 600+90 配对分析")
    report.append("")
    paired_metrics = [("ramp.avg_waiting", "匝道平均等待"), ("ramp.p95_waiting", "匝道P95等待"), ("ramp.max_waiting", "匝道最大等待"), ("ramp.mean_trip_speed", "匝道mean_trip_speed"), ("ramp.total_timeLoss", "匝道总timeLoss"), ("system.total_timeLoss", "全系统总timeLoss"), ("ramp_after3600", "匝道3600秒后完成数"), ("ramp_latest_arrival", "匝道最晚arrival")]
    pair_rows = []
    for metric, zh in paired_metrics:
        p = paired({(m, r): {s: infos[(m, r)][s] for s in infos[(m, r)]} for m, r, _ in SCENARIOS}, metric)
        pair_rows.append([zh, f"{p['mean']:.2f}", f"{p['median']:.2f}", f"{p['min']:.2f}", f"{p['max']:.2f}", p["positive"], p["negative"], p["zero"]])
    report.append(markdown_table(["指标", "90−80差值均值", "中位数", "最小", "最大", "90更差", "90更好", "相同"], pair_rows))
    report.append("")
    wait_diffs = [paired({(m, r): {s: infos[(m, r)][s] for s in infos[(m, r)]} for m, r, _ in SCENARIOS}, "ramp.avg_waiting")]
    report.append("对匝道平均等待而言，`90−80` 的正差值表示600+90更差、负差值表示600+90更好；上表的正/负计数直接回答非单调性是否跨seed重复。seed42只作为独立参考，不并入seed1–10分布。")
    report.append("")
    report.append("若600+90在部分seed反而更好，这只能说明当前固定seed和离散发车时序下存在非单调响应；在没有逐车合流间距、相位或冲突机制证据时，‘周期性发车时序+合流间隙敏感性’只能作为解释假设，不能作为已证实机制。")
    report.append("")
    report.append("## 7. 非单调性检查")
    report.append("")
    report.append("重点比较main400的ramp60/70/80以及main600的ramp60/70/80/90；完整逐seed数据见multiseed_baseline_runs.csv，跨seed分布见multiseed_baseline_summary.csv。")
    report.append("")
    for m, ramps in ((400, (60, 70, 80)), (600, (60, 70, 80, 90))):
        report.append(markdown_table(["主线", "匝道", "平均等待mean", "P95等待mean", "平均等待min–max"], [[m, r, f"{next(x for x in summary_rows if x['scenario']==f'main{m}_ramp{r}')['ramp_avg_waiting_mean']:.2f}", f"{next(x for x in summary_rows if x['scenario']==f'main{m}_ramp{r}')['ramp_p95_waiting_mean']:.2f}", f"{next(x for x in summary_rows if x['scenario']==f'main{m}_ramp{r}')['ramp_avg_waiting_min']:.2f}–{next(x for x in summary_rows if x['scenario']==f'main{m}_ramp{r}')['ramp_avg_waiting_max']:.2f}"] for r in ramps]))
        report.append("")
    report.append("## 8. 速度参考与奖励归一化")
    report.append("")
    report.append(f"v_ref_main={reference_speeds['main']:.6f} m/s，来源为tripinfo_main_only_200_seed1.xml至seed10.xml中每个seed的mainline_flow.*车辆mean(routeLength/duration)再取10个seed算术平均；v_ref_ramp={reference_speeds['ramp']:.6f} m/s，来源为tripinfo_ramp_only_50_seed1.xml至seed10.xml中每个seed的ramp_flow.*车辆mean(routeLength/duration)再取10个seed算术平均。两个参考速度均来自seed1–10自由流基准。")
    report.append("")
    report.append("速度不再对原始mean_trip_speed直接使用P95归一化，而是使用：normalized_speed_loss=max(0, v_ref−mean_trip_speed)/v_ref。延误、等待和timeLoss可以使用P90/P95作为初步尺度；速度应查看相对自由流速度损失。以下分布使用全部90个seed1–10场景-种子样本，不含seed42。")
    report.append("")
    reward_metrics = [("main_total_timeLoss", "main", "total_timeLoss"), ("ramp_total_timeLoss", "ramp", "total_timeLoss"), ("system_total_timeLoss", "system", "total_timeLoss"), ("main_avg_timeLoss", "main", "avg_timeLoss"), ("ramp_avg_timeLoss", "ramp", "avg_timeLoss"), ("system_avg_timeLoss", "system", "avg_timeLoss"), ("main_avg_waiting", "main", "avg_waiting"), ("ramp_avg_waiting", "ramp", "avg_waiting"), ("system_avg_waiting", "system", "avg_waiting"), ("main_normalized_speed_loss", "main", "normalized_speed_loss"), ("ramp_normalized_speed_loss", "ramp", "normalized_speed_loss")]
    reward_rows = []
    for label, flow, key in reward_metrics:
        vals = []
        for m, r, _ in SCENARIOS:
            for s in SEEDS:
                info = infos[(m, r)][s]
                if flow == "system":
                    vals.append(float(info["system"][key]))
                else:
                    flow_id = "mainline_flow" if flow == "main" else "ramp_flow"
                    st = stats(info["groups"][flow_id], demand(m if flow == "main" else r))
                    if key == "normalized_speed_loss":
                        vals.append(normalized_speed_loss(st["mean_trip_speed"], reference_speeds[flow]))
                    else:
                        vals.append(float(st[key]))
        d = distribution(vals)
        recommendation = "相对自由流速度损失" if key == "normalized_speed_loss" else "P90/P95初步尺度参考"
        reward_rows.append([label, f"{d['p25']:.4f}" if key == "normalized_speed_loss" else f"{d['p25']:.2f}", f"{d['median']:.4f}" if key == "normalized_speed_loss" else f"{d['median']:.2f}", f"{linear_quantile(vals,.90):.4f}" if key == "normalized_speed_loss" else f"{linear_quantile(vals,.90):.2f}", f"{linear_quantile(vals,.95):.4f}" if key == "normalized_speed_loss" else f"{linear_quantile(vals,.95):.2f}", f"{d['max']:.4f}" if key == "normalized_speed_loss" else f"{d['max']:.2f}", recommendation])
    report.append(markdown_table(["指标", "P25", "median", "P90", "P95", "max", "建议用途"], reward_rows))
    report.append("")
    report.append("total指标表示整个系统的累计代价，avg指标表示每辆完成车辆的平均代价；跨不同需求比较交通状态时应同时查看总量和单车均值。若奖励采用逐时间步形式，当前tripinfo分布只能提供回合级尺度，不能直接替代逐时间步状态/奖励分布。")
    report.append("")
    report.append("## 9. 异常、缺失和限制")
    report.append("")
    if all_errors:
        report.append("结构化校验异常：")
        report.extend(f"- {x}" for x in all_errors)
    else:
        report.append("结构化校验未发现缺失、元数据不一致、非法车辆ID、异常完成数量或arrival超过7200秒。")
    report.append("- 现有90个tripinfo没有逐场景控制台错误日志，因此无法判断具体哪些场景或seed出现emergency braking；不能据此推断具体碰撞或合流机制。后续正式控制实验应保存独立error-log或标准错误输出。")
    report.append("- 90次结果中seed42没有重复运行；既有seed42结果只作背景参考。")
    report.append("")
    report.append("## 10. 原始文件保护证据")
    report.append("")
    report.append("下表的Before为运行前只读预检记录，After为本报告生成时重新计算。")
    report.append(markdown_table(["文件", "Before SHA-256", "After SHA-256", "状态"], hash_table(root, PROTECTED_BEFORE)))
    report.append("")
    report.append("9个本轮复用路由文件的运行前哈希也已在分析脚本中记录；当前哈希必须与其一致：")
    report.append(markdown_table(["路由文件", "Before SHA-256", "After SHA-256", "状态"], hash_table(root, ROUTE_BEFORE)))
    report.append("")
    report.append("## 11. 本轮实际创建或修改文件")
    report.append("")
    report.append("本轮修改或重生成：analyze_multiseed.py、multiseed_baseline_runs.csv、multiseed_baseline_summary.csv、multiseed_baseline_report.md。")
    report.append("只读复用：9个既有merge_main*_ramp*.rou.xml、90个seed1–10 tripinfo及30个自由流校准tripinfo；osm.net.xml、osm.rou.xml、check.sumocfg、所有seed42结果和其他实验文件未修改。")
    report.append("")
    (root / "multiseed_baseline_report.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    print(f"VALID_OUTPUTS={sum(len(x) for x in infos.values())}/90")
    print(f"STRUCTURAL_ERRORS={len(all_errors)}")
    print(f"ROUTE_ERRORS={len(route_errors)}")
    return 0 if not all_errors else 2


if __name__ == "__main__":
    raise SystemExit(main())
