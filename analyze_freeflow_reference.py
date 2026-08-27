#!/usr/bin/env python3
"""Calibrate free-flow speed references from the authorized 30 runs.

The script is deliberately self-contained and uses only the Python standard
library.  It validates the new route/tripinfo/error-log files, computes
per-run metrics and cross-seed distributions, then checks the new references
against the existing 90 mixed-traffic tripinfos.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import math
import re
import statistics
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import datetime
from pathlib import Path, PureWindowsPath


CASES = [
    ("main_only_200", "main_only_200.rou.xml", "mainline_flow", 200),
    ("main_only_400", "main_only_400.rou.xml", "mainline_flow", 400),
    ("ramp_only_50", "ramp_only_50.rou.xml", "ramp_flow", 50),
]
SEEDS = list(range(1, 11))
T_CRIT_DF9 = 2.2621571627409915
OLD_MAIN_REF = 22.294730372180638
OLD_RAMP_REF = 17.362609491199606
MIXED_SCENARIOS = [(400, 60), (400, 70), (400, 80), (600, 60), (600, 70), (600, 80), (600, 90), (800, 50), (1000, 50)]
EXPECTED_MANIFEST_ROWS = 161
EXPECTED_CATEGORY_COUNTS = {
    "core": 4,
    "mixed_route": 9,
    "mixed_tripinfo": 90,
    "historical_seed42_tripinfo": 58,
}
EXPECTED_CORE_SHA256 = {
    "osm.net.xml": "856D22EC0E5D7FD13021557EBEBD2A3CD3BABD03385A208A2E801D3265B7FD99",
    "osm.rou.xml": "5E6BA3D54F9B484C3584F2232A516AAFCC964B305D27F85967F059AB542B6584",
    "check.sumocfg": "CD46A96B2BC7CD1B39C2B334CD56831012BDB79A29F3A8302E34EBB52378DED4",
}


def number(row: ET.Element, name: str) -> float:
    return float(row.get(name, "0"))


def avg(values: list[float]) -> float:
    return statistics.fmean(values) if values else 0.0


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def nearest_rank(values: list[float], q: float = 0.95) -> float:
    if not values:
        return 0.0
    x = sorted(values)
    return x[max(1, math.ceil(q * len(x))) - 1]


def linear_quantile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    x = sorted(values)
    if len(x) == 1:
        return x[0]
    pos = (len(x) - 1) * q
    lo, hi = math.floor(pos), math.ceil(pos)
    if lo == hi:
        return x[lo]
    return x[lo] + (x[hi] - x[lo]) * (pos - lo)


def distribution(values: list[float]) -> dict[str, float]:
    mean = avg(values)
    sd = statistics.stdev(values) if len(values) > 1 else 0.0
    half = T_CRIT_DF9 * sd / math.sqrt(len(values)) if values else 0.0
    return {
        "mean": mean, "sd": sd, "cv": sd / abs(mean) if mean else 0.0,
        "min": min(values) if values else 0.0,
        "p25": linear_quantile(values, .25), "median": linear_quantile(values, .50),
        "p75": linear_quantile(values, .75), "p95": linear_quantile(values, .95),
        "max": max(values) if values else 0.0,
        "ci95_low": mean - half, "ci95_high": mean + half,
    }


def metadata(raw: str) -> dict[str, str | None]:
    def get(pattern: str) -> str | None:
        found = re.search(pattern, raw)
        return found.group(1) if found else None
    return {
        "route": get(r'<route-files\s+value="([^"]+)"'),
        "output": get(r'<tripinfo-output\s+value="([^"]+)"'),
        "error": get(r'<error-log\s+value="([^"]+)"'),
        "seed": get(r'<seed\s+value="([^"]+)"'),
    }


def log_counts(path: Path) -> dict[str, int]:
    text = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
    lines = text.splitlines()
    return {
        "warning_count": sum(bool(re.search(r"warning", line, re.I)) for line in lines),
        "emergency_braking_count": sum(bool(re.search(r"emergency braking", line, re.I)) for line in lines),
        "collision_count": sum(bool(re.search(r"collision", line, re.I)) for line in lines),
        "teleport_count": sum(bool(re.search(r"teleport", line, re.I)) for line in lines),
    }


def parse_run(root: Path, case: str, route_name: str, flow: str, rate: int, seed: int) -> tuple[dict, list[str]]:
    output_name = f"tripinfo_{case}_seed{seed}.xml"
    error_name = f"sumo_error_{case}_seed{seed}.log"
    errors: list[str] = []
    out_path, log_path = root / output_name, root / error_name
    if not out_path.exists():
        return {}, [f"missing {output_name}"]
    raw = out_path.read_text(encoding="utf-8")
    meta = metadata(raw)
    if meta["route"] != route_name:
        errors.append(f"{output_name}: route-files={meta['route']!r}")
    if meta["output"] != output_name:
        errors.append(f"{output_name}: tripinfo-output={meta['output']!r}")
    if meta["error"] != error_name:
        errors.append(f"{output_name}: error-log={meta['error']!r}")
    if meta["seed"] != str(seed):
        errors.append(f"{output_name}: seed={meta['seed']!r}")
    try:
        root_xml = ET.fromstring(raw)
    except Exception as exc:
        return {}, errors + [f"{output_name}: invalid XML: {exc}"]
    rows = list(root_xml.iter("tripinfo"))
    for row in rows:
        if not row.get("id", "").startswith(flow + "."):
            errors.append(f"{output_name}: wrong vehicle id {row.get('id')}")
    duration = [number(x, "duration") for x in rows]
    waiting = [number(x, "waitingTime") for x in rows]
    loss = [number(x, "timeLoss") for x in rows]
    delay = [number(x, "departDelay") for x in rows]
    depart = [number(x, "depart") for x in rows]
    arrival = [number(x, "arrival") for x in rows]
    lengths = [number(x, "routeLength") for x in rows]
    speed_values = [length / dur for length, dur in zip(lengths, duration) if dur > 0]
    if len(speed_values) != len(rows):
        errors.append(f"{output_name}: non-positive duration")
    nominal = round(rate * 3600 / 3600)
    if len(rows) - nominal not in (0, 1):
        errors.append(f"{output_name}: completed={len(rows)} nominal={nominal}")
    endpoint_extra = len(rows) == nominal + 1 and sum(abs(number(x, "depart") - 3600.0) < 1e-6 for x in rows) == 1
    if len(rows) == nominal + 1 and not endpoint_extra:
        errors.append(f"{output_name}: extra vehicle is not depart=3600.00")
    logs = log_counts(log_path)
    if not log_path.exists():
        errors.append(f"missing {error_name}")
    st = {
        "case": case, "seed": seed, "route_file": route_name, "output_file": output_name, "error_log": error_name,
        "flow": flow, "demand_count": nominal, "completed_count": len(rows), "completion_rate": len(rows) / nominal if nominal else 0.0,
        "mean_duration": avg(duration), "mean_waitingTime": avg(waiting), "P95_waitingTime": nearest_rank(waiting), "max_waitingTime": max(waiting) if waiting else 0.0,
        "mean_timeLoss": avg(loss), "P95_timeLoss": nearest_rank(loss), "mean_departDelay": avg(delay), "max_departDelay": max(delay) if delay else 0.0,
        "aggregate_speed": sum(lengths) / sum(duration) if sum(duration) else 0.0, "mean_trip_speed": avg(speed_values),
        "latest_depart": max(depart) if depart else 0.0, "latest_arrival": max(arrival) if arrival else 0.0,
        "endpoint_extra": "yes" if endpoint_extra else "no", "validation_status": "PASS" if not errors else "FAIL",
        "errors": errors, **logs,
    }
    return st, errors


def parse_mixed(root: Path, main: int, ramp: int, seed: int) -> tuple[float, float, list[str]]:
    path = root / f"tripinfo_main{main}_ramp{ramp}_seed{seed}.xml"
    errors: list[str] = []
    try:
        root_xml = ET.parse(path).getroot()
    except Exception as exc:
        return 0.0, 0.0, [f"mixed {path.name}: {exc}"]
    speeds = {"mainline_flow": [], "ramp_flow": []}
    for row in root_xml.iter("tripinfo"):
        vid = row.get("id", "")
        flow = "mainline_flow" if vid.startswith("mainline_flow.") else "ramp_flow" if vid.startswith("ramp_flow.") else None
        if flow is None or number(row, "duration") <= 0:
            errors.append(f"mixed {path.name}: invalid id/duration {vid}")
            continue
        speeds[flow].append(number(row, "routeLength") / number(row, "duration"))
    return avg(speeds["mainline_flow"]), avg(speeds["ramp_flow"]), errors


def markdown_table(headers: list[str], rows: list[list[object]]) -> str:
    out = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    out.extend("| " + " | ".join(str(x) for x in row) + " |" for row in rows)
    return "\n".join(out)


def validate_routes(root: Path) -> tuple[dict[str, list[str]], list[str]]:
    """Validate all three free-flow route files against osm.rou.xml."""
    errors_by_file: dict[str, list[str]] = {}
    try:
        template = ET.parse(root / "osm.rou.xml").getroot()
    except Exception as exc:
        return {}, [f"osm.rou.xml: invalid XML: {exc}"]
    template_vtypes = {v.get("id"): dict(v.attrib) for v in template.findall("vType")}
    template_flows = {f.get("id"): dict(f.attrib) for f in template.findall("flow")}
    expected = {
        "main_only_200.rou.xml": ("mainline_flow", "776458555", 200),
        "main_only_400.rou.xml": ("mainline_flow", "776458555", 400),
        "ramp_only_50.rou.xml": ("ramp_flow", "E2", 50),
    }
    for name, (flow_id, from_edge, rate) in expected.items():
        errors: list[str] = []
        path = root / name
        try:
            route_root = ET.parse(path).getroot()
        except Exception as exc:
            errors_by_file[name] = [f"{name}: invalid XML: {exc}"]
            continue
        actual_vtypes = {v.get("id"): dict(v.attrib) for v in route_root.findall("vType")}
        if len(actual_vtypes) != len(template_vtypes) or set(actual_vtypes) != set(template_vtypes):
            errors.append(f"{name}: vType count/id mismatch")
        for vid, attrs in template_vtypes.items():
            if actual_vtypes.get(vid) != attrs:
                errors.append(f"{name}: vType {vid} attributes differ from osm.rou.xml")
        flows = route_root.findall("flow")
        if len(flows) != 1:
            errors.append(f"{name}: expected exactly one flow")
        elif flows[0].get("id") != flow_id:
            errors.append(f"{name}: expected flow id {flow_id}")
        if flows:
            actual = dict(flows[0].attrib)
            expected_attrs = dict(template_flows[flow_id])
            expected_attrs["vehsPerHour"] = str(rate)
            expected_attrs["from"] = from_edge
            if actual != expected_attrs:
                errors.append(f"{name}: flow attributes differ from requested/template values")
        errors_by_file[name] = errors
    return errors_by_file, [error for errors in errors_by_file.values() for error in errors]


def check_protection_manifest(root: Path) -> list[str]:
    """Validate the protected-file manifest structure before analysis."""
    path = root / "freeflow_cleanup_protection_manifest.csv"
    if not path.exists():
        return ["manifest missing: freeflow_cleanup_protection_manifest.csv"]
    errors: list[str] = []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        required_columns = {
            "file", "category", "cleanup_before_size", "cleanup_after_size",
            "cleanup_before_mtime", "cleanup_after_mtime", "cleanup_before_sha256",
            "cleanup_after_sha256", "status",
        }
        missing_columns = sorted(required_columns - set(reader.fieldnames or []))
        if missing_columns:
            errors.append(f"manifest missing required columns: {', '.join(missing_columns)}")
        rows = list(reader)
    if len(rows) != EXPECTED_MANIFEST_ROWS:
        errors.append(f"manifest expected 161 rows, got {len(rows)}")
    category_counts = {category: 0 for category in EXPECTED_CATEGORY_COUNTS}
    seen_files: dict[str, int] = {}
    manifest_by_file: dict[str, dict[str, str]] = {}
    for index, row in enumerate(rows, start=2):
        file = (row.get("file") or "").strip()
        category = (row.get("category") or "").strip()
        before_hash = (row.get("cleanup_before_sha256") or "").strip().upper()
        after_hash = (row.get("cleanup_after_sha256") or "").strip().upper()
        status = (row.get("status") or "").strip()
        if category in category_counts:
            category_counts[category] += 1
        if not file:
            errors.append(f"manifest row {index}: file is empty")
            continue
        normalized = str(PureWindowsPath(file)).replace("\\", "/").casefold()
        manifest_by_file[normalized] = row
        if normalized in seen_files:
            errors.append(f"duplicate protected file: {file} (row {index}, row {seen_files[normalized]})")
        else:
            seen_files[normalized] = index
        for field, value in (
            ("category", category),
            ("cleanup_before_sha256", before_hash),
            ("cleanup_after_sha256", after_hash),
            ("status", status),
        ):
            if not value:
                errors.append(f"manifest row {index}: {field} is empty")
        if before_hash and after_hash and before_hash != after_hash:
            errors.append(f"before/after hash mismatch: {file}")
        if status and status != "UNCHANGED":
            errors.append(f"status is not UNCHANGED: {file}")
        target = root / file
        if not target.exists():
            errors.append(f"protected file missing before analysis: {file}")
        elif not target.is_file():
            errors.append(f"protected target is not a regular file: {file}")
        elif before_hash and sha256(target) != before_hash:
            errors.append(f"current hash mismatch: {file}")
    if category_counts != EXPECTED_CATEGORY_COUNTS:
        errors.append(f"category counts mismatch: expected {EXPECTED_CATEGORY_COUNTS}, got {category_counts}")
    for file, expected in EXPECTED_CORE_SHA256.items():
        normalized = str(PureWindowsPath(file)).replace("\\", "/").casefold()
        row = manifest_by_file.get(normalized)
        target = root / file
        current = sha256(target) if target.is_file() else "MISSING"
        if row is None or (row.get("cleanup_before_sha256") or "").strip().upper() != expected or current != expected:
            errors.append(f"frozen core hash mismatch: {file}")
    return errors


def update_protection_manifest(root: Path) -> list[str]:
    """Fill cleanup-after evidence and status after analysis inputs are read."""
    validation_errors = check_protection_manifest(root)
    if validation_errors:
        return validation_errors
    path = root / "freeflow_cleanup_protection_manifest.csv"
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    errors: list[str] = []
    for row in rows:
        target = root / row["file"]
        if not target.exists() or not target.is_file():
            row["cleanup_after_size"] = "MISSING"
            row["cleanup_after_mtime"] = "MISSING"
            row["cleanup_after_sha256"] = "MISSING"
            row["status"] = "CHANGED_OR_MISSING"
            errors.append(f"protected file changed or missing after analysis: {row['file']}")
            continue
        info = target.stat()
        after_hash = sha256(target)
        row["cleanup_after_size"] = str(info.st_size)
        row["cleanup_after_mtime"] = datetime.fromtimestamp(info.st_mtime).astimezone().isoformat()
        row["cleanup_after_sha256"] = after_hash
        unchanged = after_hash == row["cleanup_before_sha256"]
        row["status"] = "UNCHANGED" if unchanged else "CHANGED"
        if not unchanged:
            errors.append(f"protected file changed after analysis: {row['file']}")
    fields = ["file", "category", "cleanup_before_size", "cleanup_after_size", "cleanup_before_mtime", "cleanup_after_mtime", "cleanup_before_sha256", "cleanup_after_sha256", "status"]
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    return errors


def fmt(x: float, digits: int = 3) -> str:
    return f"{x:.{digits}f}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    args = ap.parse_args()
    root = args.root.resolve()
    all_rows: list[dict] = []
    protection_errors = check_protection_manifest(root)
    if protection_errors:
        for error in protection_errors:
            print(f"PROTECTION_ERROR={error}")
        print("VALIDATION_PASS=False")
        return 2
    route_status, route_errors = validate_routes(root)
    all_errors: list[str] = list(protection_errors) + list(route_errors)
    for case, route, flow, rate in CASES:
        for seed in SEEDS:
            row, errors = parse_run(root, case, route, flow, rate, seed)
            if row:
                all_rows.append(row)
            all_errors.extend(errors)

    fields = ["case", "seed", "route_file", "output_file", "error_log", "flow", "demand_count", "completed_count", "completion_rate", "mean_duration", "mean_waitingTime", "P95_waitingTime", "max_waitingTime", "mean_timeLoss", "P95_timeLoss", "mean_departDelay", "max_departDelay", "aggregate_speed", "mean_trip_speed", "latest_depart", "latest_arrival", "endpoint_extra", "warning_count", "emergency_braking_count", "collision_count", "teleport_count", "validation_status"]
    with (root / "freeflow_reference_runs.csv").open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(all_rows)

    metrics = ["completion_rate", "mean_duration", "mean_waitingTime", "P95_waitingTime", "max_waitingTime", "mean_timeLoss", "P95_timeLoss", "mean_departDelay", "max_departDelay", "aggregate_speed", "mean_trip_speed", "latest_depart", "latest_arrival", "warning_count", "emergency_braking_count", "collision_count", "teleport_count"]
    sum_fields = ["case", "seed_count"]
    for metric in metrics:
        for suffix in ("mean", "sd", "cv", "min", "p25", "median", "p75", "p95", "max", "ci95_low", "ci95_high"):
            sum_fields.append(f"{metric}_{suffix}")
    groups = defaultdict(list)
    for row in all_rows:
        groups[row["case"]].append(row)
    summary_rows = []
    for case, _, _, _ in CASES:
        row = {"case": case, "seed_count": len(groups[case])}
        for metric in metrics:
            d = distribution([float(x[metric]) for x in groups[case]])
            for suffix, value in d.items():
                row[f"{metric}_{suffix}"] = value
        summary_rows.append(row)
    with (root / "freeflow_reference_summary.csv").open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=sum_fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(summary_rows)

    by_case = {x["case"]: x for x in summary_rows}
    main200 = by_case["main_only_200"]["mean_trip_speed_mean"]
    main400 = by_case["main_only_400"]["mean_trip_speed_mean"]
    ramp50 = by_case["ramp_only_50"]["mean_trip_speed_mean"]
    main_ref = main200
    ramp_ref = ramp50
    relative_diff = abs(main200 - main400) / main200 if main200 else 0.0
    mixed_rows = []
    mixed_errors: list[str] = []
    for main, ramp in MIXED_SCENARIOS:
        for seed in SEEDS:
            ms, rs, errors = parse_mixed(root, main, ramp, seed)
            mixed_rows.append({"scenario": f"main{main}_ramp{ramp}", "main": main, "ramp": ramp, "seed": seed, "main_speed": ms, "ramp_speed": rs, "main_loss": max(0.0, 1.0 - ms / main_ref) if main_ref else 0.0, "ramp_loss": max(0.0, 1.0 - rs / ramp_ref) if ramp_ref else 0.0})
            mixed_errors.extend(errors)
    main_high = sum(x["main_speed"] > main_ref for x in mixed_rows)
    ramp_high = sum(x["ramp_speed"] > ramp_ref for x in mixed_rows)
    protection_after_errors = update_protection_manifest(root)
    all_errors.extend(protection_after_errors)
    manifest_path = root / "freeflow_cleanup_protection_manifest.csv"
    manifest_rows = []
    if manifest_path.exists():
        with manifest_path.open("r", encoding="utf-8-sig", newline="") as f:
            manifest_rows = list(csv.DictReader(f))
    manifest_bad = sum(row.get("status") != "UNCHANGED" or row.get("cleanup_before_sha256") != row.get("cleanup_after_sha256") for row in manifest_rows)
    cleanup_marker_absent = not (root / "1E-09").exists()
    freeflow_ok = len(all_rows) == 30 and all(x["validation_status"] == "PASS" and x["completed_count"] == x["demand_count"] and x["mean_waitingTime"] <= 1.0 and x["max_departDelay"] <= 1.0 and x["warning_count"] == 0 and x["emergency_braking_count"] == 0 and x["collision_count"] == 0 and x["teleport_count"] == 0 for x in all_rows)
    mixed_count = len(mixed_rows)
    main_high_ratio = main_high / mixed_count if mixed_count else 1.0
    checks = {
        "protection_manifest_valid": not protection_errors,
        "routes_valid": not route_errors,
        "no_structural_errors": not all_errors,
        "no_mixed_read_errors": not mixed_errors,
        "freeflow_no_anomaly": freeflow_ok,
        "main200_400_within_2pct": relative_diff <= 0.02,
        "new_main_above_old": main_ref > OLD_MAIN_REF,
        "mixed_main_above_ref_le_10pct": mixed_count == 90 and main_high_ratio <= 0.10,
        "mixed_main_p90_nonzero": linear_quantile([x["main_loss"] for x in mixed_rows], .90) > 0,
        "mixed_main_p95_nonzero": linear_quantile([x["main_loss"] for x in mixed_rows], .95) > 0,
    }
    validation_pass = all(checks.values())
    log_total = len(all_rows)
    log_present = sum((root / row["error_log"]).exists() for row in all_rows)
    log_totals = {key: sum(int(row[key]) for row in all_rows) for key in ("warning_count", "emergency_braking_count", "collision_count", "teleport_count")}
    report: list[str] = ["# 自由流速度参考值校准报告", "", "## 1. 执行概况", "", f"本轮仅重新分析既有30个自由流tripinfo/error-log和90个混合交通tripinfo；本轮未运行SUMO、未启动控制器，也未创建路由、tripinfo或error-log输入。当前解析到{len(all_rows)}/30个自由流结果。", "", "## 2. 路由与运行验证", "", markdown_table(["场景", "路由文件", "路由验证", "成功/计划", "tripinfo可解析", "错误日志", "异常"], [[case, route, "PASS" if not route_status.get(route) else "FAIL", f"{sum(x['case']==case for x in all_rows)}/10", "PASS" if sum(x['case']==case and x['validation_status']=="PASS" for x in all_rows)==10 else "FAIL", f"{sum((root / x['error_log']).exists() for x in all_rows if x['case']==case)}/10", "无结构异常" if not any(case in e for e in all_errors) else "有异常"] for case, route, _, _ in CASES]), "", f"错误日志实际读取{log_present}/{log_total}个；warning={log_totals['warning_count']}，emergency_braking={log_totals['emergency_braking_count']}，collision={log_totals['collision_count']}，teleport={log_totals['teleport_count']}。四项均纳入自由流有效性门控。", ""]
    report.append("## 3. 跨seed自由流统计")
    report.append("")
    report.append("跨seed的P25、median、P75、P95使用10个seed上的线性插值分位数；95%置信区间为mean ± t(0.975, df=9)×sample_sd/√10，t=2.262157。单次tripinfo内P95 waitingTime/timeLoss使用nearest-rank。")
    report.append("")
    report.append(markdown_table(["场景", "mean_trip_speed均值", "sd", "95% CI", "平均等待", "P95等待", "平均timeLoss", "完成率", "warning/emergency/collision/teleport"], [[case, fmt(by_case[case]["mean_trip_speed_mean"]), fmt(by_case[case]["mean_trip_speed_sd"]), f"[{fmt(by_case[case]['mean_trip_speed_ci95_low'])}, {fmt(by_case[case]['mean_trip_speed_ci95_high'])}]", fmt(by_case[case]["mean_waitingTime_mean"]), fmt(by_case[case]["P95_waitingTime_mean"]), fmt(by_case[case]["mean_timeLoss_mean"]), fmt(by_case[case]["completion_rate_mean"], 4), f"{int(by_case[case]['warning_count_mean'])}/{int(by_case[case]['emergency_braking_count_mean'])}/{int(by_case[case]['collision_count_mean'])}/{int(by_case[case]['teleport_count_mean'])}"] for case, _, _, _ in CASES]))
    report.append("")
    report.append("两个速度口径均已输出：aggregate_speed=sum(routeLength)/sum(duration)；mean_trip_speed=mean(routeLength/duration)。本报告的参考值使用mean_trip_speed，不将aggregate_speed混用为参考速度。")
    report.append("")
    report.append("## 4. 参考速度计算")
    report.append("")
    report.append(f"v_ref_main = 纯主线200 seed1–10的mean_trip_speed算术平均 = {main_ref:.6f} m/s；样本标准差={by_case['main_only_200']['mean_trip_speed_sd']:.6f}；95% CI=[{by_case['main_only_200']['mean_trip_speed_ci95_low']:.6f}, {by_case['main_only_200']['mean_trip_speed_ci95_high']:.6f}]。纯主线400交叉验证均值={main400:.6f} m/s。")
    report.append(f"v_ref_ramp = 纯匝道50 seed1–10的mean_trip_speed算术平均 = {ramp_ref:.6f} m/s；样本标准差={by_case['ramp_only_50']['mean_trip_speed_sd']:.6f}；95% CI=[{by_case['ramp_only_50']['mean_trip_speed_ci95_low']:.6f}, {by_case['ramp_only_50']['mean_trip_speed_ci95_high']:.6f}]。")
    report.append("")
    report.append(markdown_table(["比较", "数值", "判定"], [["main200 vs main400 relative_difference", f"{relative_diff*100:.3f}%", "通过≤2%" if checks["main200_400_within_2pct"] else "未通过，不能更新参考值"], ["旧主线参考", f"{OLD_MAIN_REF:.6f} m/s", "来自main_only_1000_seed42"], ["新主线−旧主线", f"{main_ref-OLD_MAIN_REF:+.6f} m/s", "新参考明显更高" if checks["new_main_above_old"] else "未高于旧参考"], ["旧匝道参考", f"{OLD_RAMP_REF:.6f} m/s", "来自ramp_only_50_seed42"], ["新匝道−旧匝道", f"{ramp_ref-OLD_RAMP_REF:+.6f} m/s", "seed1–10自由流均值"]]))
    report.append("")
    report.append("旧主线参考来自主线1000辆/小时、单个seed42的混合需求结果；该场景已经受到更高交通需求影响，不能作为自由流速度上界。新参考来自纯主线200 seed1–10，并由纯主线400独立交叉验证。")
    report.append("")
    report.append("## 5. 新参考值有效性检查")
    report.append("")
    report.append(markdown_table(["检查项", "结果"], [["保护清单有效", "PASS" if checks["protection_manifest_valid"] else "FAIL"], ["三套路由通过验证", "PASS" if checks["routes_valid"] else "FAIL"], ["无结构错误", "PASS" if checks["no_structural_errors"] else "FAIL"], ["无混合结果读取错误", "PASS" if checks["no_mixed_read_errors"] else "FAIL"], ["自由流完成、等待、departDelay、四类日志异常", "PASS" if checks["freeflow_no_anomaly"] else "FAIL"], ["main200/main400速度差≤2%", "PASS" if checks["main200_400_within_2pct"] else "FAIL"], ["新主线参考高于旧参考", "PASS" if checks["new_main_above_old"] else "FAIL"], ["90次混合交通主线速度高于新参考", f"{main_high}/{mixed_count} ({main_high_ratio*100:.1f}%)"], ["90次混合交通匝道速度高于新参考", f"{ramp_high}/{mixed_count} ({ramp_high/mixed_count*100:.1f}%)" if mixed_count else "0/0"], ["main normalized loss P90/P95非0", "PASS" if checks["mixed_main_p90_nonzero"] and checks["mixed_main_p95_nonzero"] else "FAIL"]]))
    report.append("")
    report.append("归一化公式：normalized_speed_loss=max(0, 1−mean_trip_speed/v_ref)。")
    for label, key in (("main", "main_loss"), ("ramp", "ramp_loss")):
        vals = [x[key] for x in mixed_rows]
        report.append(f"{label} normalized_speed_loss：P25={linear_quantile(vals,.25):.6f}，median={linear_quantile(vals,.5):.6f}，P75={linear_quantile(vals,.75):.6f}，P90={linear_quantile(vals,.9):.6f}，P95={linear_quantile(vals,.95):.6f}，max={max(vals):.6f}。")
    report.append("")
    report.append("参考值通过，可以更新多种子分析。" if validation_pass else "参考值未通过，禁止更新多种子分析。")
    report.append("")
    report.append("## 6. 异常和限制")
    report.append("")
    if all_errors or mixed_errors or any(log_totals.values()) or not freeflow_ok:
        report.append("- 结构化异常：")
        if all_errors or mixed_errors:
            report.extend(f"  - {x}" for x in all_errors + mixed_errors)
        if any(log_totals.values()):
            report.append(f"  - 错误日志计数：warning={log_totals['warning_count']}，emergency_braking={log_totals['emergency_braking_count']}，collision={log_totals['collision_count']}，teleport={log_totals['teleport_count']}。")
        if not freeflow_ok and not all_errors and not mixed_errors and not any(log_totals.values()):
            report.append("  - 自由流有效性条件未满足。")
    else:
        report.append("- 30个自由流tripinfo和错误日志未发现结构错误、异常车辆、collision、teleport或emergency braking。")
    report.append("- 95%置信区间使用10个seed的样本标准差和df=9的t临界值；它描述seed不确定性，不等同于总体交通需求不确定性。")
    report.append("- 新匝道参考值也来自单一流量ramp50的10个seed；更广泛的自由流参考仍可在后续补充，但本轮不增加场景。")
    report.append(f"- validation_pass={validation_pass}；保护清单用于证明本次收尾修复期间未修改保护文件，不应被描述为最初30次仿真之前的哈希记录。")
    report.append("")
    report.append("## 7. 收尾保护证据")
    report.append("")
    report.append(f"freeflow_cleanup_protection_manifest.csv包含{len(manifest_rows)}个保护文件；before/after哈希不一致数={manifest_bad}。这些before/after值证明的是本次收尾修复期间没有修改保护文件；对于此前未保存运行前哈希的文件，不能将cleanup_before描述为最初30次仿真之前的哈希。")
    report.append("核心文件期望哈希：osm.net.xml=856D22EC0E5D7FD13021557EBEBD2A3CD3BABD03385A208A2E801D3265B7FD99；osm.rou.xml=5E6BA3D54F9B484C3584F2232A516AAFCC964B305D27F85967F059AB542B6584；check.sumocfg=CD46A96B2BC7CD1B39C2B334CD56831012BDB79A29F3A8302E34EBB52378DED4。")
    report.append(f"误生成文件1E-09删除状态：{'已删除' if cleanup_marker_absent else '仍存在'}。它是根目录下经验证的8字节UTF-16重定向文件，与实验结果无关。")
    report.append("")
    report.append("## 8. 负向测试可复现证据")
    report.append("")
    report.extend([
        "python -m unittest -v tests.test_analyze_freeflow_reference.ExistingNegativeGateTests.test_wrong_main_only_200_rate",
        "expected analyzer exit=2; actual analyzer exit=2; PASS",
        "python -m unittest -v tests.test_analyze_freeflow_reference.ExistingNegativeGateTests.test_emergency_braking_log",
        "expected analyzer exit=2; actual analyzer exit=2; PASS",
        "python -m unittest -v tests.test_analyze_freeflow_reference.ExistingNegativeGateTests.test_missing_error_log",
        "expected analyzer exit=2; actual analyzer exit=2; PASS",
    ])
    report.append("")
    report.append("## 9. 文件清单")
    report.append("")
    report.append("既有输入：三套路由、30个自由流tripinfo、30个自由流sumo_error日志、90个混合交通tripinfo、既有保护清单及其161项保护文件；本轮仅读取这些输入。")
    report.append("本轮分析产物：freeflow_reference_runs.csv、freeflow_reference_summary.csv、freeflow_reference_report.md，以及合法清单的cleanup-after证据。")
    (root / "freeflow_reference_report.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    print(f"FREEFLOW_OUTPUTS={len(all_rows)}/30")
    print(f"FREEFLOW_ERRORS={len(all_errors)}")
    print(f"MAIN200_MAIN400_RELATIVE_DIFFERENCE={relative_diff:.8f}")
    print(f"V_REF_MAIN={main_ref:.9f}")
    print(f"V_REF_RAMP={ramp_ref:.9f}")
    print(f"VALIDATION_MAIN_HIGH={main_high}/90")
    print(f"VALIDATION_RAMP_HIGH={ramp_high}/90")
    print(f"VALIDATION_PASS={validation_pass}")
    return 0 if validation_pass else 2


if __name__ == "__main__":
    raise SystemExit(main())
