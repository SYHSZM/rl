# Task 1 review package

Base/Head: 389294fd (Task 1 creates untracked audit evidence only; no commit by design).

## Git tracked diff
`git diff --name-status` and cached diff were empty after Task 1.

## Artifact manifest
See `manifest.json`; Sol independently verified 29 entries with zero missing/hash failures.

## Fresh Sol verification
- 360 step rows, times 1..360
- 12 reconciliation windows and 16 fields
- production_hash_guard unchanged=true, changed_paths=[]
- pytest exit_code=0, 18 passed
- required files non-empty

## Files under review
- metric_trace_runner.py
- task-1-report.md
- manifest.json
- experiment_result.json
- production_hash_guard.json
- step_trace.csv
- window_reconciliation.csv
- tripinfo_reconciliation.csv
- pytest_result.json and logs

## Runner source
```python
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import platform
import subprocess
import sys
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


AUDIT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = AUDIT_DIR.parents[1]
NATIVE_DIR = AUDIT_DIR / "native"
INDUCTION_LOOPS = {
    "det_main_0": ("main_0_1", "500"),
    "det_main_1": ("main_1_1", "500"),
    "det_main_2": ("main_2_1", "500"),
    "det_main_3": ("main_3_1", "500"),
    "det_main_4": ("main_4_1", "500"),
    "det_bottleneck_down": ("main_4_1", "150"),
    "det_ramp_arrival": ("ramp_0_0", "40"),
}
MAIN_DETECTORS = [f"det_main_{index}" for index in range(5)]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def production_hashes() -> dict[str, str]:
    network = PROJECT_ROOT / "network"
    guarded = sorted(network.glob("*.xml"))
    return {str(path.relative_to(PROJECT_ROOT)).replace("\\", "/"): sha256(path) for path in guarded}


def audit_config() -> tuple[Any, Any]:
    from experiment_config import DemandPhase, DemandPoint, default_config, validate_config

    config = default_config()
    config.simulation_duration_s = 360
    config.step_length_s = 1.0
    config.metrics_interval_s = 30
    config.mainline_range = (1200, 1200, 1)
    config.ramp_range = (120, 120, 1)
    config.seeds = (0,)
    config.demand_phases = [DemandPhase(0, 360, 1.0)]
    validate_config(config)
    return config, DemandPoint(1200, 120)


def write_additional() -> Path:
    NATIVE_DIR.mkdir(parents=True, exist_ok=True)
    root = ET.Element("additional")
    for detector_id, (lane, pos) in INDUCTION_LOOPS.items():
        ET.SubElement(
            root,
            "inductionLoop",
            {
                "id": detector_id,
                "lane": lane,
                "pos": pos,
                "freq": "30",
                "file": (NATIVE_DIR / f"{detector_id}.xml").as_posix(),
            },
        )
    ET.SubElement(
        root,
        "laneAreaDetector",
        {
            "id": "det_ramp_queue",
            "lane": "ramp_0_0",
            "pos": "0",
            "endPos": "300",
            "freq": "30",
            "file": (NATIVE_DIR / "det_ramp_queue.xml").as_posix(),
        },
    )
    path = AUDIT_DIR / "audit.add.xml"
    tree = ET.ElementTree(root)
    ET.indent(tree, space="    ")
    tree.write(path, encoding="utf-8", xml_declaration=True)
    return path


def sumo_command(config: Any, route_path: Path, additional_path: Path) -> list[str]:
    return [
        "sumo",
        "--net-file", str(PROJECT_ROOT / "network" / "merge.net.xml"),
        "--route-files", str(route_path),
        "--additional-files", str(additional_path),
        "--step-length", str(config.step_length_s),
        "--begin", "0",
        "--end", str(config.simulation_duration_s),
        "--seed", "0",
        "--time-to-teleport", "-1",
        "--max-depart-delay", "1800",
        "--tripinfo-output", str(AUDIT_DIR / "tripinfo.xml"),
        "--log", str(AUDIT_DIR / "sumo.log"),
        "--error-log", str(AUDIT_DIR / "sumo_error.log"),
    ]


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str] | None = None) -> None:
    if fieldnames is None:
        fieldnames = list(rows[0]) if rows else []
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def record_step(conn: Any, time_s: int, totals: dict[str, int]) -> tuple[dict[str, Any], set[str], set[str]]:
    loaded_ids = tuple(conn.simulation.getLoadedIDList())
    departed_ids = tuple(conn.simulation.getDepartedIDList())
    arrived_ids = tuple(conn.simulation.getArrivedIDList())
    pending_ids = tuple(conn.simulation.getPendingVehicles())
    active_ids = tuple(conn.vehicle.getIDList())
    starting_teleport_ids = tuple(conn.simulation.getStartingTeleportIDList())
    ending_teleport_ids = tuple(conn.simulation.getEndingTeleportIDList())
    values = {
        "time_s": time_s,
        "step_loaded_veh": conn.simulation.getLoadedNumber(),
        "loaded_total_veh": totals["loaded"],
        "step_departed_veh": conn.simulation.getDepartedNumber(),
        "departed_total_veh": totals["departed"],
        "step_arrived_veh": conn.simulation.getArrivedNumber(),
        "arrived_total_veh": totals["arrived"],
        "pending_veh": len(pending_ids),
        "in_network_veh": totals["departed"] - totals["arrived"],
        "active_vehicle_veh": len(active_ids),
        "min_expected_veh": conn.simulation.getMinExpectedNumber(),
        "step_starting_teleports": conn.simulation.getStartingTeleportNumber(),
        "starting_teleports_total": totals["teleports"],
        "step_ending_teleports": conn.simulation.getEndingTeleportNumber(),
        "system_vehicle_exposure_veh_s": totals["departed"] - totals["arrived"] + len(pending_ids),
        "loaded_ids": ";".join(loaded_ids),
        "departed_ids": ";".join(departed_ids),
        "arrived_ids": ";".join(arrived_ids),
        "pending_ids": ";".join(pending_ids),
        "active_vehicle_ids": ";".join(active_ids),
        "starting_teleport_ids": ";".join(starting_teleport_ids),
        "ending_teleport_ids": ";".join(ending_teleport_ids),
    }
    for detector_id in INDUCTION_LOOPS:
        values[f"{detector_id}_count"] = int(conn.inductionloop.getLastStepVehicleNumber(detector_id))
        values[f"{detector_id}_speed_mps"] = float(conn.inductionloop.getLastStepMeanSpeed(detector_id))
        values[f"{detector_id}_occupancy_fraction"] = float(conn.inductionloop.getLastStepOccupancy(detector_id)) / 100.0
    values["det_ramp_queue_vehicle_count"] = int(conn.lanearea.getLastStepVehicleNumber("det_ramp_queue"))
    values["det_ramp_queue_halting_count"] = int(conn.lanearea.getLastStepHaltingNumber("det_ramp_queue"))
    return values, set(active_ids), set(pending_ids)


def parse_native() -> dict[str, dict[tuple[int, int], dict[str, str]]]:
    native: dict[str, dict[tuple[int, int], dict[str, str]]] = {}
    for path in sorted(NATIVE_DIR.glob("*.xml")):
        intervals: dict[tuple[int, int], dict[str, str]] = {}
        root = ET.parse(path).getroot()
        for interval in root.findall("interval"):
            key = (int(float(interval.attrib["begin"])), int(float(interval.attrib["end"])))
            intervals[key] = dict(interval.attrib)
        native[path.stem] = intervals
    return native


def fvalue(value: Any) -> float | None:
    if value in (None, "", "NA", "N/A"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def numeric_match(left: Any, right: Any, tolerance: float = 1e-6) -> str:
    a, b = fvalue(left), fvalue(right)
    if a is None or b is None:
        if left is None or right is None:
            return "NA"
        return "YES" if str(left) == str(right) else "NO"
    return "YES" if abs(a - b) <= tolerance else "NO"


def native_speed(attrs: dict[str, str]) -> float | None:
    count = int(float(attrs.get("nVehContrib", "0")))
    speed = float(attrs.get("speed", "-1"))
    return speed if count > 0 and speed >= 0 else None


def weighted(values: list[tuple[float, float]]) -> float | None:
    denominator = sum(weight for _, weight in values if weight > 0)
    if denominator <= 0:
        return None
    return sum(value * weight for value, weight in values if weight > 0) / denominator


def reconciliation_rows(windows: list[Any], steps: list[dict[str, Any]], native: dict[str, dict[tuple[int, int], dict[str, str]]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    cumulative = {"departed_veh": 0, "arrived_veh": 0, "teleports": 0}
    for window in windows:
        current = asdict(window)
        end = int(window.time_s)
        begin = end - 30
        block = [row for row in steps if begin < int(row["time_s"]) <= end]
        if len(block) != 30:
            raise RuntimeError(f"window {begin}-{end} has {len(block)} step rows")
        for field, step_col in (("departed_veh", "step_departed_veh"), ("arrived_veh", "step_arrived_veh"), ("teleports", "step_starting_teleports")):
            cumulative[field] += sum(int(row[step_col]) for row in block)

        main_step_pairs = [
            (float(row[f"{det}_speed_mps"]), float(row[f"{det}_count"]))
            for row in block for det in MAIN_DETECTORS
            if float(row[f"{det}_speed_mps"]) >= 0
        ]
        upstream_step_pairs = [
            (float(row["det_main_3_speed_mps"]), float(row["det_main_3_count"]))
            for row in block if float(row["det_main_3_speed_mps"]) >= 0
        ]
        main_native_pairs: list[tuple[float, float]] = []
        for det in MAIN_DETECTORS:
            attrs = native[det][(begin, end)]
            speed = native_speed(attrs)
            count = float(attrs.get("nVehContrib", "0"))
            if speed is not None:
                main_native_pairs.append((speed, count))
        upstream_attrs = native["det_main_3"][(begin, end)]
        bottleneck_attrs = native["det_bottleneck_down"][(begin, end)]
        ramp_attrs = native["det_ramp_queue"][(begin, end)]

        comparisons: dict[str, tuple[Any, str, Any, str, Any, str, str]] = {
            "experiment_id": (current["experiment_id"], "constant_scenario_identity", current["experiment_id"], "NA", None, "SAME", "native XML has no experiment id"),
            "controller": (current["controller"], "constant_controller", "none", "NA", None, "SAME", "native XML has no controller"),
            "seed": (current["seed"], "constant_seed", 0, "NA", None, "SAME", "native XML has no seed"),
            "time_s": (current["time_s"], "30_step_rows_window_end", end, "interval.end", end, "SAME", "aligned interval end"),
            "mainline_vph": (current["mainline_vph"], "constant_demand", 1200, "NA", None, "SAME", "native XML has no requested demand"),
            "ramp_vph": (current["ramp_vph"], "constant_demand", 120, "NA", None, "SAME", "native XML has no requested demand"),
            "mean_speed_mps": (current["mean_speed_mps"], "vehicle_exposure_weighted_across_30s_and_5_loops", weighted(main_step_pairs), "5_interval_speed_weighted_by_nVehContrib", weighted(main_native_pairs), "DIFFERENT", "current is unweighted mean of five last-step speeds at window end"),
            "upstream_speed_mps": (current["upstream_speed_mps"], "vehicle_exposure_weighted_across_30s", weighted(upstream_step_pairs), "interval.speed", native_speed(upstream_attrs), "DIFFERENT", "current is last-step snapshot at window end"),
            "bottleneck_flow_veh": (current["bottleneck_flow_veh"], "sum_last_step_vehicle_number_30s", sum(int(row["det_bottleneck_down_count"]) for row in block), "interval.nVehEntered", int(float(bottleneck_attrs.get("nVehEntered", "0"))), "DIFFERENT", f"current is last-step snapshot; native nVehContrib={bottleneck_attrs.get('nVehContrib', '')}"),
            "bottleneck_occupancy": (current["bottleneck_occupancy"], "mean_last_step_occupancy_fraction_30s", sum(float(row["det_bottleneck_down_occupancy_fraction"]) for row in block) / 30.0, "interval.occupancy/100", float(bottleneck_attrs.get("occupancy", "0")) / 100.0, "DIFFERENT", "current is last-step snapshot; step/native are interval exposure"),
            "ramp_queue_veh": (current["ramp_queue_veh"], "mean_lanearea_vehicle_count_30s", sum(int(row["det_ramp_queue_vehicle_count"]) for row in block) / 30.0, "interval.meanVehicleNumber", float(ramp_attrs.get("meanVehicleNumber", "0")), "DIFFERENT", f"current is endpoint snapshot; step max={max(int(row['det_ramp_queue_vehicle_count']) for row in block)}"),
            "alinea_requested_rate_vph": (current["alinea_requested_rate_vph"], "constant_no_control_action", 1200.0, "NA", None, "SAME", "not a detector metric"),
            "alinea_applied_rate_vph": (current["alinea_applied_rate_vph"], "constant_no_control_action", 1200.0, "NA", None, "SAME", "not a detector metric"),
            "departed_veh": (current["departed_veh"], "cumulative_sum_of_step_departures", cumulative["departed_veh"], "NA", None, "SAME", f"window increment={sum(int(row['step_departed_veh']) for row in block)}"),
            "arrived_veh": (current["arrived_veh"], "cumulative_sum_of_step_arrivals", cumulative["arrived_veh"], "NA", None, "SAME", f"window increment={sum(int(row['step_arrived_veh']) for row in block)}"),
            "teleports": (current["teleports"], "cumulative_sum_of_step_starting_teleports", cumulative["teleports"], "NA", None, "SAME", f"window increment={sum(int(row['step_starting_teleports']) for row in block)}"),
        }
        for field, (current_value, step_method, step_value, native_source, native_value, semantics, note) in comparisons.items():
            current_step_numeric = numeric_match(current_value, step_value)
            native_tolerance = 0.0051 if field in {"mean_speed_mps", "upstream_speed_mps", "ramp_queue_veh"} else 1e-4
            step_native_numeric = numeric_match(step_value, native_value, tolerance=native_tolerance)
            current_native_numeric = numeric_match(current_value, native_value, tolerance=native_tolerance)
            rows.append({
                "window_begin_s": begin,
                "window_end_s": end,
                "field": field,
                "current_windows_value": current_value,
                "step_aggregation_method": step_method,
                "step_aggregate_value": step_value,
                "native_source": native_source,
                "native_value": native_value,
                "current_vs_step_numeric_match": current_step_numeric,
                "step_vs_native_numeric_match": step_native_numeric,
                "current_vs_native_numeric_match": current_native_numeric,
                "current_vs_interval_semantics": semantics,
                "reconciliation_status": "CONSISTENT" if semantics == "SAME" and current_step_numeric == "YES" and current_native_numeric in {"YES", "NA"} else "INCONSISTENT",
                "note": note,
            })
    return rows


def parse_tripinfo() -> list[dict[str, str]]:
    root = ET.parse(AUDIT_DIR / "tripinfo.xml").getroot()
    return [dict(element.attrib) for element in root.findall("tripinfo")]


def tripinfo_reconciliation(summary: Any, steps: list[dict[str, Any]], completed_exposure: dict[str, dict[str, int]], final_active: set[str], final_pending: set[str]) -> list[dict[str, Any]]:
    trips = parse_tripinfo()
    completed_ids = {row["id"] for row in trips}
    in_network_integral = sum(int(row["in_network_veh"]) for row in steps)
    pending_integral = sum(int(row["pending_veh"]) for row in steps)
    all_active_exposure = completed_exposure["active"]
    all_pending_exposure = completed_exposure["pending"]
    incomplete_ids = final_active | final_pending
    values = [
        ("current_summary_tts_s", summary.total_time_spent_s, "30 s right-endpoint integral of departed-arrived; excludes pending"),
        ("one_second_in_network_integral_s", in_network_integral, "sum of departed-arrived after each 1 s step"),
        ("one_second_waiting_pending_integral_s", pending_integral, "sum of pending-to-depart vehicle count after each 1 s step"),
        ("one_second_system_tts_s", in_network_integral + pending_integral, "one-second in-network plus pending integral"),
        ("completed_tripinfo_count", len(trips), "completed tripinfo records"),
        ("completed_tripinfo_duration_s", sum(float(row["duration"]) for row in trips), "sum tripinfo duration for completed vehicles"),
        ("completed_tripinfo_time_loss_s", sum(float(row["timeLoss"]) for row in trips), "sum tripinfo timeLoss for completed vehicles"),
        ("incomplete_in_network_count_at_360_s", len(final_active), "active vehicle IDs after final step"),
        ("incomplete_pending_count_at_360_s", len(final_pending), "pending vehicle IDs after final step"),
        ("incomplete_vehicle_count_union_at_360_s", len(incomplete_ids), "union of active and pending IDs"),
        ("completed_vehicle_active_exposure_s", sum(all_active_exposure.get(vehicle_id, 0) for vehicle_id in completed_ids), "ID-level active exposure for completed vehicles"),
        ("completed_vehicle_pending_exposure_s", sum(all_pending_exposure.get(vehicle_id, 0) for vehicle_id in completed_ids), "ID-level pending exposure for completed vehicles"),
        ("incomplete_vehicle_active_exposure_s", sum(all_active_exposure.get(vehicle_id, 0) for vehicle_id in incomplete_ids), "ID-level active exposure retained separately"),
        ("incomplete_vehicle_pending_exposure_s", sum(all_pending_exposure.get(vehicle_id, 0) for vehicle_id in incomplete_ids), "ID-level pending exposure retained separately"),
        ("current_minus_one_second_system_tts_s", float(summary.total_time_spent_s) - in_network_integral - pending_integral, "quantified current proxy difference"),
        ("tripinfo_duration_minus_one_second_system_tts_s", sum(float(row["duration"]) for row in trips) - in_network_integral - pending_integral, "completed-only tripinfo versus system exposure including incomplete"),
    ]
    return [{"component": key, "value": value, "unit": "veh" if "count" in key else "s", "definition": definition} for key, value, definition in values]


def software_versions() -> str:
    def output(command: list[str]) -> str:
        result = subprocess.run(command, cwd=PROJECT_ROOT, text=True, capture_output=True, check=False)
        return (result.stdout + result.stderr).strip()

    pytest_version = output([sys.executable, "-m", "pytest", "--version"])
    return "\n".join([
        f"captured_utc={datetime.now(timezone.utc).isoformat()}",
        f"python_executable={sys.executable}",
        f"python_version={sys.version}",
        f"platform={platform.platform()}",
        f"SUMO_HOME={os.environ.get('SUMO_HOME', '')}",
        f"pytest={pytest_version}",
        "sumo_version_begin",
        output(["sumo", "--version"]),
        "sumo_version_end",
        "netconvert_version_begin",
        output(["netconvert", "--version"]),
        "netconvert_version_end",
    ]) + "\n"


def write_command_record() -> None:
    commands = [
        f'$env:SUMO_HOME="{os.environ.get("SUMO_HOME", "D:\\sumo-1.25.0")}"',
        '$env:Path="D:\\sumo-1.25.0\\bin;"+$env:Path',
        f'& "{sys.executable}" "{AUDIT_DIR / "metric_trace_runner.py"}"',
        f'& "{sys.executable}" "{AUDIT_DIR / "metric_trace_runner.py"}" --run-tests',
        f'& "{sys.executable}" "{AUDIT_DIR / "metric_trace_runner.py"}" --refresh-manifest',
    ]
    (AUDIT_DIR / "command.txt").write_text("\n".join(commands) + "\n", encoding="utf-8")


def write_report_if_possible() -> None:
    experiment_path = AUDIT_DIR / "experiment_result.json"
    reconciliation_path = AUDIT_DIR / "window_reconciliation.csv"
    tripinfo_path = AUDIT_DIR / "tripinfo_reconciliation.csv"
    if not (experiment_path.exists() and reconciliation_path.exists() and tripinfo_path.exists()):
        return
    experiment = json.loads(experiment_path.read_text(encoding="utf-8"))
    with reconciliation_path.open(encoding="utf-8", newline="") as handle:
        reconciliation = list(csv.DictReader(handle))
    with tripinfo_path.open(encoding="utf-8", newline="") as handle:
        trip_rows = list(csv.DictReader(handle))
    inconsistent = [row for row in reconciliation if row["reconciliation_status"] == "INCONSISTENT"]
    pytest_result_path = AUDIT_DIR / "pytest_result.json"
    pytest_text = "尚未执行"
    if pytest_result_path.exists():
        pytest_result = json.loads(pytest_result_path.read_text(encoding="utf-8"))
        pytest_text = f'exit_code={pytest_result["exit_code"]}; 详见 pytest_stdout.log / pytest_stderr.log / pytest_junit.xml'
    trip = {row["component"]: row["value"] for row in trip_rows}
    def match_summary(rows: list[dict[str, str]], column: str) -> str:
        counts = Counter(row[column] for row in rows)
        return "; ".join(f"{key} {counts[key]}/12" for key in ("YES", "NO", "NA") if counts[key])

    field_lines = []
    for field in [
        "experiment_id", "controller", "seed", "time_s", "mainline_vph", "ramp_vph",
        "mean_speed_mps", "upstream_speed_mps", "bottleneck_flow_veh", "bottleneck_occupancy",
        "ramp_queue_veh", "alinea_requested_rate_vph", "alinea_applied_rate_vph",
        "departed_veh", "arrived_veh", "teleports",
    ]:
        rows = [row for row in reconciliation if row["field"] == field]
        statuses = sorted({row["reconciliation_status"] for row in rows})
        semantics = rows[0]["current_vs_interval_semantics"] if rows else "NA"
        conclusion = "一致" if statuses == ["CONSISTENT"] else "不一致（语义不同）"
        field_lines.append(
            f'| {field} | {match_summary(rows, "current_vs_step_numeric_match")} '
            f'| {match_summary(rows, "step_vs_native_numeric_match")} '
            f'| {match_summary(rows, "current_vs_native_numeric_match")} '
            f'| {semantics}；{conclusion} |'
        )
    file_names = sorted(
        str(path.relative_to(AUDIT_DIR)).replace("\\", "/")
        for path in AUDIT_DIR.rglob("*")
        if path.is_file() and path.name not in {"manifest.json", "task-1-report.md"}
    )
    file_lines = "\n".join(f"- `{name}`" for name in file_names + ["manifest.json", "task-1-report.md"])
    report = f"""# Task 1 指标数据血缘证据采集报告

本报告仅用于指标可信度审计，不形成交通研究结论。

## 修改/新增文件

仅在 `outputs/audit_0_3/` 内创建以下文件；未修改生产代码、测试、文档或 network 文件：

{file_lines}

## 实现和执行

- 固定场景：360 s，mainline 1200 veh/h，ramp 120 veh/h，controller none，seed 0，单阶段 0–360 s × 1.0，1 s 步长，30 s 窗口。
- 逐秒记录 loaded/departed/arrived/pending/in-network/teleport、全部 induction loop 的 count/speed/occupancy，以及 ramp lane-area vehicle/halting count。
- 复用 `env.py` 现有私有窗口采集函数和 `metrics.py` 汇总函数，不改变当前公式。
- 当前结果：{experiment["step_rows"]} 条 step rows，{experiment["window_rows"]} 条 windows；current summary TTS={experiment["summary"]["total_time_spent_s"]} s。

## 逐字段一致性

`YES/NO/NA n/12` 分别表示数值一致、数值不一致、该来源不适用（或该窗口无 detector exposure）的窗口数。native 速度和 lane-area `meanVehicleNumber` 按 XML 两位小数精度使用 ±0.0051 容差；占有率按换算后的 fraction 使用 ±0.0001 容差；计数要求精确一致。

| 字段 | current↔30 s step | 30 s step↔native XML | current↔native XML | 语义结论 |
|---|---|---|---|---|
{chr(10).join(field_lines)}

详细逐窗口数值、差值与 native 来源见 `window_reconciliation.csv`。共 {len(inconsistent)} 条字段-窗口记录标为 INCONSISTENT，主要原因是 current windows 使用窗口末 last-step 快照，而 30 s step/native XML 是完整区间聚合。

## TTS / tripinfo 对账

- current summary TTS: {trip.get("current_summary_tts_s", "NA")} s
- 1 s in-network integral: {trip.get("one_second_in_network_integral_s", "NA")} s
- 1 s waiting/pending integral: {trip.get("one_second_waiting_pending_integral_s", "NA")} s
- 1 s system TTS: {trip.get("one_second_system_tts_s", "NA")} s
- completed tripinfo duration: {trip.get("completed_tripinfo_duration_s", "NA")} s
- completed tripinfo timeLoss: {trip.get("completed_tripinfo_time_loss_s", "NA")} s
- incomplete active/pending at 360 s: {trip.get("incomplete_in_network_count_at_360_s", "NA")}/{trip.get("incomplete_pending_count_at_360_s", "NA")}

## 测试与实验命令/结果

命令见 `command.txt`；软件和 SUMO 版本见 `software_versions.txt`。全量 pytest：{pytest_text}。

## 剩余风险、异常或疑问

- last-step 快照与完整 30 s 区间的语义天然不同，数值偶合不代表语义一致。
- detector XML 的 `nVehEntered` 与 `nVehContrib` 在窗口边界可能不同；CSV 保留两者说明。
- tripinfo 只包含已完成车辆；未完成车辆以 ID 级 active/pending exposure 单独保留。
- 默认系统 Python 3.14 缺少 pytest/traci，审计需使用记录在 `software_versions.txt` 的 Python 3.12，并通过 SUMO_HOME/tools 加载 TraCI。
"""
    (AUDIT_DIR / "task-1-report.md").write_text(report, encoding="utf-8")


def run_experiment() -> None:
    os.chdir(PROJECT_ROOT)
    sys.path.insert(0, str(PROJECT_ROOT))
    from controllers import ControlAction
    from env import _apply_ramp_signal, _collect_window_record, _import_traci, _make_controller
    from experiment_config import experiment_id
    from metrics import summarize_episode, write_summary_csv, write_window_csv
    from rou_generate import generate_rou_file

    AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    write_command_record()
    before_hashes = production_hashes()
    config, demand = audit_config()
    route_path = AUDIT_DIR / "audit.rou.xml"
    generate_rou_file(route_path, demand, seed=0, config=config)
    additional_path = write_additional()
    command = sumo_command(config, route_path, additional_path)
    (AUDIT_DIR / "sumo_command.json").write_text(json.dumps(command, indent=2), encoding="utf-8")
    (AUDIT_DIR / "software_versions.txt").write_text(software_versions(), encoding="utf-8")

    traci = _import_traci()
    controller = _make_controller("none", config)
    action = ControlAction(config.alinea.max_rate_vph, config.alinea.max_rate_vph, config.alinea.cycle_s, 0.0)
    exp_id = experiment_id(config, demand, "none", 0)
    windows: list[Any] = []
    steps: list[dict[str, Any]] = []
    exposure: dict[str, dict[str, int]] = {"active": defaultdict(int), "pending": defaultdict(int)}
    totals = {"loaded": 0, "departed": 0, "arrived": 0, "teleports": 0}
    final_active: set[str] = set()
    final_pending: set[str] = set()
    label = f"audit_{os.getpid()}"
    conn = None
    try:
        traci.start(command, label=label)
        conn = traci.getConnection(label)
        current_time = 0
        while current_time < config.simulation_duration_s:
            if current_time % config.metrics_interval_s == 0:
                occupancy = float(conn.inductionloop.getLastStepOccupancy("det_bottleneck_down")) / 100.0
                queue = int(conn.lanearea.getLastStepVehicleNumber("det_ramp_queue"))
                action = controller.update(occupancy=occupancy, queue_veh=queue)
            _apply_ramp_signal(conn, action, current_time, config.alinea.cycle_s)
            conn.simulationStep()
            current_time = int(conn.simulation.getTime())
            totals["loaded"] += int(conn.simulation.getLoadedNumber())
            totals["departed"] += int(conn.simulation.getDepartedNumber())
            totals["arrived"] += int(conn.simulation.getArrivedNumber())
            totals["teleports"] += int(conn.simulation.getStartingTeleportNumber())
            row, active_ids, pending_ids = record_step(conn, current_time, totals)
            steps.append(row)
            final_active, final_pending = active_ids, pending_ids
            for vehicle_id in active_ids:
                exposure["active"][vehicle_id] += 1
            for vehicle_id in pending_ids:
                exposure["pending"][vehicle_id] += 1
            if current_time > 0 and current_time % config.metrics_interval_s == 0:
                windows.append(_collect_window_record(conn, exp_id, "none", 0, current_time, demand, action, totals["departed"], totals["arrived"], totals["teleports"]))
    finally:
        try:
            traci.close()
        except Exception:
            pass

    if len(steps) != 360 or len(windows) != 12:
        raise RuntimeError(f"expected 360 steps and 12 windows, got {len(steps)} and {len(windows)}")
    summary = summarize_episode(windows, config.free_flow_speed_mps, config.congestion_speed_ratio, config.congestion_min_duration_s)
    write_csv(AUDIT_DIR / "step_trace.csv", steps)
    write_window_csv(AUDIT_DIR / "current_windows.csv", windows)
    write_summary_csv(AUDIT_DIR / "current_summary.csv", [summary])
    native = parse_native()
    write_csv(AUDIT_DIR / "window_reconciliation.csv", reconciliation_rows(windows, steps, native))
    write_csv(AUDIT_DIR / "tripinfo_reconciliation.csv", tripinfo_reconciliation(summary, steps, exposure, final_active, final_pending))
    after_hashes = production_hashes()
    guard = {
        "before": before_hashes,
        "after": after_hashes,
        "unchanged": before_hashes == after_hashes,
        "changed_paths": sorted(path for path in set(before_hashes) | set(after_hashes) if before_hashes.get(path) != after_hashes.get(path)),
    }
    (AUDIT_DIR / "production_hash_guard.json").write_text(json.dumps(guard, indent=2, sort_keys=True), encoding="utf-8")
    if not guard["unchanged"]:
        raise RuntimeError(f"production network XML changed: {guard['changed_paths']}")
    (AUDIT_DIR / "experiment_result.json").write_text(json.dumps({"experiment_id": exp_id, "scenario": {"duration_s": 360, "mainline_vph": 1200, "ramp_vph": 120, "controller": "none", "seed": 0, "phase": [0, 360, 1.0]}, "step_rows": len(steps), "window_rows": len(windows), "summary": asdict(summary), "final_active_ids": sorted(final_active), "final_pending_ids": sorted(final_pending)}, indent=2), encoding="utf-8")
    print(json.dumps({"status": "experiment_complete", "steps": len(steps), "windows": len(windows), "summary_tts_s": summary.total_time_spent_s}))


def run_tests() -> int:
    write_command_record()
    command = [sys.executable, "-m", "pytest", "-q", "--junitxml", str(AUDIT_DIR / "pytest_junit.xml")]
    result = subprocess.run(command, cwd=PROJECT_ROOT, text=True, capture_output=True, check=False)
    (AUDIT_DIR / "pytest_stdout.log").write_text(result.stdout, encoding="utf-8")
    (AUDIT_DIR / "pytest_stderr.log").write_text(result.stderr, encoding="utf-8")
    (AUDIT_DIR / "pytest_result.json").write_text(json.dumps({"command": command, "exit_code": result.returncode}, indent=2), encoding="utf-8")
    print(result.stdout, end="")
    print(result.stderr, end="", file=sys.stderr)
    return result.returncode


def refresh_manifest() -> None:
    write_report_if_possible()
    files = []
    for path in sorted(AUDIT_DIR.rglob("*")):
        if path.is_file() and path.name != "manifest.json":
            files.append({"path": str(path.relative_to(AUDIT_DIR)).replace("\\", "/"), "bytes": path.stat().st_size, "sha256": sha256(path)})
    manifest = {"generated_utc": datetime.now(timezone.utc).isoformat(), "audit_scope": "metric lineage evidence only; no traffic-study conclusion", "files": files}
    (AUDIT_DIR / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-tests", action="store_true")
    parser.add_argument("--refresh-manifest", action="store_true")
    args = parser.parse_args()
    AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    write_command_record()
    if args.run_tests:
        code = run_tests()
    elif args.refresh_manifest:
        refresh_manifest()
        code = 0
    else:
        run_experiment()
        code = 0
    refresh_manifest()
    return code


if __name__ == "__main__":
    raise SystemExit(main())
```
