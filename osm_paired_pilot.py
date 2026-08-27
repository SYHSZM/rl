from __future__ import annotations

import argparse
import csv
import hashlib
import importlib
import json
import math
import os
import subprocess
import sys
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import xml.etree.ElementTree as ET

from analyze_freeflow_reference import check_protection_manifest
from controllers import AlineaController, NoControlController
from experiment_config import default_config
from osm_control_adapter import (
    OSM_CONTROL_PROFILE,
    aggregate_mainline_occupancy,
    apply_ramp_signal,
    read_ramp_queue,
)
from osm_smoke_runner import scan_logs, sha256, write_runtime_detector_file


ROOT = Path(__file__).resolve().parent
OUTPUT_ROOT = ROOT / "outputs/stage4c_pilot"
SUMO_BINARY = Path(r"D:\sumo-1.25.0\bin\sumo.exe")
NET_SHA256 = "856D22EC0E5D7FD13021557EBEBD2A3CD3BABD03385A208A2E801D3265B7FD99"
DETECTOR_SHA256 = "52844C6A95E17F2C10F44E52B3CED9ABE5A0C3FEC8ED13CE4674A3EC7B01362D"
DEMAND_END_S = 3600
MAX_END_S = 7200
WINDOW_S = 30
SEED = 1


@dataclass(frozen=True)
class PilotScenario:
    name: str
    mainline_vph: int
    ramp_vph: int
    nominal_veh: int
    route_sha256: str

    @property
    def mainline_veh(self) -> int:
        return self.mainline_vph

    @property
    def ramp_veh(self) -> int:
        return self.ramp_vph


@dataclass(frozen=True)
class RunSpec:
    scenario: PilotScenario
    controller: str
    seed: int
    route_path: Path
    output_dir: Path


SCENARIOS = (
    PilotScenario("600+60", 600, 60, 660, "ECF07999D44C8EA4B2FB5FEED908066FF82088602DE6F59A5ABCF96EDEB250EB"),
    PilotScenario("400+80", 400, 80, 480, "D9E70ABEF4DFBC163E859F71F3FE87B8DE65700331909505B01B9EF8321BE887"),
    PilotScenario("1000+50", 1000, 50, 1050, "B28C03F19441559DFF2EAA9331B4A44293E07C882F125EA7BD6EDAB6FB0B0BDC"),
)

WINDOW_FIELDS = [
    "scenario", "controller", "seed", "window_end_s",
    "mainline_vehicle_observations", "mainline_speed_mps", "mainline_occupancy",
    "bottleneck_throughput_veh", "ramp_vehicle_mean_veh", "ramp_vehicle_max_veh",
    "ramp_halting_mean_veh", "ramp_halting_max_veh", "departed_veh", "arrived_veh",
    "teleports", "collisions", "requested_rate_vph", "applied_rate_vph", "green_s",
    "red_s", "state_mismatches",
]


def build_run_matrix(root: Path = ROOT, output_root: Path | None = None) -> list[RunSpec]:
    output_root = (output_root or root / "outputs/stage4c_pilot").resolve()
    runs = []
    for scenario in SCENARIOS:
        folder = f"main{scenario.mainline_vph}_ramp{scenario.ramp_vph}"
        route = (root / "demand_delivery/fixed" / f"{scenario.name}.rou.xml").resolve()
        for controller in ("none", "alinea"):
            runs.append(RunSpec(scenario, controller, SEED, route, output_root / folder / f"{controller}_seed1"))
    return runs


def nearest_rank(values: list[float], probability: float = 0.95) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(probability * len(ordered)) - 1))
    return ordered[index]


def _group_metrics(records: list[dict[str, float]], nominal: int) -> dict[str, object]:
    durations = [item["duration"] for item in records]
    waiting = [item["waitingTime"] for item in records]
    losses = [item["timeLoss"] for item in records]
    delays = [item["departDelay"] for item in records]
    route_lengths = [item["routeLength"] for item in records]
    speed_values = [length / duration for length, duration in zip(route_lengths, durations) if duration > 0]
    duration_total = sum(durations)
    def mean(values):
        return sum(values) / len(values) if values else 0.0
    def max_value(values):
        return max(values) if values else 0.0
    return {
        "nominal_demand": nominal,
        "completed": len(records),
        "completion_rate": len(records) / nominal if nominal else 0.0,
        "before3600": sum(item["arrival"] < DEMAND_END_S for item in records),
        "after3600": sum(item["arrival"] >= DEMAND_END_S for item in records),
        "latest_arrival": max_value([item["arrival"] for item in records]),
        "mean_duration": mean(durations),
        "p95_duration": nearest_rank(durations),
        "max_duration": max_value(durations),
        "mean_waitingTime": mean(waiting),
        "p95_waitingTime": nearest_rank(waiting),
        "max_waitingTime": max_value(waiting),
        "total_waitingTime": sum(waiting),
        "mean_timeLoss": mean(losses),
        "p95_timeLoss": nearest_rank(losses),
        "max_timeLoss": max_value(losses),
        "total_timeLoss": sum(losses),
        "mean_departDelay": mean(delays),
        "max_departDelay": max_value(delays),
        "aggregate_speed": sum(route_lengths) / duration_total if duration_total else 0.0,
        "mean_trip_speed": mean(speed_values),
        "total_system_time_s": sum(item["duration"] + item["departDelay"] for item in records),
    }


def parse_tripinfo(path: Path, spec: RunSpec) -> dict[str, object]:
    groups = {"mainline": [], "ramp": []}
    for element in ET.parse(path).getroot().findall("tripinfo"):
        vehicle_id = element.attrib["id"]
        if vehicle_id.startswith("mainline_"):
            group = "mainline"
        elif vehicle_id.startswith("ramp_"):
            group = "ramp"
        else:
            raise ValueError(f"unknown tripinfo vehicle ID: {vehicle_id}")
        groups[group].append({
            "depart": float(element.attrib["depart"]),
            "arrival": float(element.attrib["arrival"]),
            "duration": float(element.attrib["duration"]),
            "waitingTime": float(element.attrib["waitingTime"]),
            "timeLoss": float(element.attrib["timeLoss"]),
            "departDelay": float(element.attrib["departDelay"]),
            "routeLength": float(element.attrib["routeLength"]),
        })
    mainline = _group_metrics(groups["mainline"], spec.scenario.mainline_vph)
    ramp = _group_metrics(groups["ramp"], spec.scenario.ramp_vph)
    system_records = groups["mainline"] + groups["ramp"]
    system = _group_metrics(system_records, spec.scenario.nominal_veh)
    return {"mainline": mainline, "ramp": ramp, "system": system}


def parse_native_mainline_throughput(detector_1_path: Path, detector_2_path: Path) -> dict[str, object]:
    """Read the two frozen downstream induction-loop XML files by nVehContrib."""
    errors: list[str] = []
    expected_ids = ("osm_det_main_down_1", "osm_det_main_down_2")
    parsed: dict[str, list[tuple[float, float, int]]] = {}
    for path, expected_id in zip((Path(detector_1_path), Path(detector_2_path)), expected_ids):
        try:
            root = ET.parse(path).getroot()
        except (OSError, ET.ParseError) as exc:
            errors.append(f"{path.name}: XML parse error: {exc}")
            continue
        intervals: list[tuple[float, float, int]] = []
        seen: set[tuple[float, float]] = set()
        for node in root.findall("interval"):
            try:
                begin, end = float(node.attrib["begin"]), float(node.attrib["end"])
                value_text = node.attrib["nVehContrib"]
                value = int(value_text)
            except (KeyError, TypeError, ValueError):
                errors.append(f"{path.name}: malformed interval")
                continue
            if node.attrib.get("id") != expected_id:
                errors.append(f"{path.name}: detector id mismatch")
            if value < 0 or begin < 0 or end <= begin:
                errors.append(f"{path.name}: invalid interval bounds or nVehContrib")
            key = (begin, end)
            if key in seen:
                errors.append(f"{path.name}: duplicate interval {begin}-{end}")
            seen.add(key)
            intervals.append((begin, end, value))
        parsed[expected_id] = intervals
    expected_windows = [(float(i * WINDOW_S), float((i + 1) * WINDOW_S)) for i in range(120)]
    if set(parsed) != set(expected_ids):
        errors.append("both downstream detector XML files are required")
        return {"windows": [], "evaluation_total": 0, "terminal_total": 0, "errors": errors}
    window_values: dict[str, dict[tuple[float, float], int]] = {}
    for detector_id in expected_ids:
        intervals = parsed[detector_id]
        values = {(begin, end): value for begin, end, value in intervals}
        evaluation_keys = [(begin, end) for begin, end, _ in intervals if begin >= 0 and end <= DEMAND_END_S]
        if set(evaluation_keys) != set(expected_windows):
            errors.append(f"{detector_id}: evaluation intervals are not exactly 120 aligned windows")
        window_values[detector_id] = values
    if errors:
        return {"windows": [], "evaluation_total": 0,
                "terminal_total": sum(value for intervals in parsed.values() for _, _, value in intervals),
                "errors": errors}
    windows = [{"window_end_s": int(end), "bottleneck_throughput_veh": sum(
        window_values[detector_id][(begin, end)] for detector_id in expected_ids)}
               for begin, end in expected_windows]
    evaluation_total = sum(row["bottleneck_throughput_veh"] for row in windows)
    terminal_total = sum(value for intervals in parsed.values() for _, _, value in intervals)
    return {"windows": windows, "evaluation_total": evaluation_total,
            "terminal_total": terminal_total, "errors": []}


def _ratio(none: float, alinea: float) -> float:
    return (none - alinea) / none if none else 0.0


def compare_pair(none: dict[str, object], alinea: dict[str, object]) -> dict[str, object]:
    valid_pair = bool(
        none.get("valid") and alinea.get("valid")
        and none.get("scenario") == alinea.get("scenario")
        and none.get("seed") == alinea.get("seed")
        and none.get("controller") == "none"
        and alinea.get("controller") == "alinea"
    )
    n_system = none.get("tripinfo", {}).get("system", {})
    a_system = alinea.get("tripinfo", {}).get("system", {})
    n_main = none.get("tripinfo", {}).get("mainline", {})
    a_main = alinea.get("tripinfo", {}).get("mainline", {})
    n_ramp = none.get("tripinfo", {}).get("ramp", {})
    a_ramp = alinea.get("tripinfo", {}).get("ramp", {})
    n_online = none.get("online", {})
    a_online = alinea.get("online", {})
    n_runtime = none.get("runtime", {})
    a_runtime = alinea.get("runtime", {})
    n_logs = none.get("log_counts", {})
    a_logs = alinea.get("log_counts", {})
    n_tts = float(n_system.get("total_system_time_s", 0.0))
    a_tts = float(a_system.get("total_system_time_s", 0.0))
    row = {
        "scenario": none.get("scenario"), "seed": none.get("seed"), "valid_pair": valid_pair,
        "none_actual_end_s": none.get("actual_end_s"), "alinea_actual_end_s": alinea.get("actual_end_s"),
        "none_total_system_time_s": n_tts, "alinea_total_system_time_s": a_tts, "tts_improvement_ratio": _ratio(n_tts, a_tts),
        "none_system_total_timeLoss": n_system.get("total_timeLoss", 0.0), "alinea_system_total_timeLoss": a_system.get("total_timeLoss", 0.0), "system_timeLoss_improvement_ratio": _ratio(float(n_system.get("total_timeLoss", 0.0)), float(a_system.get("total_timeLoss", 0.0))),
        "none_mainline_total_timeLoss": n_main.get("total_timeLoss", 0.0), "alinea_mainline_total_timeLoss": a_main.get("total_timeLoss", 0.0), "mainline_timeLoss_improvement_ratio": _ratio(float(n_main.get("total_timeLoss", 0.0)), float(a_main.get("total_timeLoss", 0.0))),
        "none_ramp_total_timeLoss": n_ramp.get("total_timeLoss", 0.0), "alinea_ramp_total_timeLoss": a_ramp.get("total_timeLoss", 0.0), "ramp_timeLoss_improvement_ratio": _ratio(float(n_ramp.get("total_timeLoss", 0.0)), float(a_ramp.get("total_timeLoss", 0.0))),
        "mainline_mean_trip_speed_delta_mps": float(a_main.get("mean_trip_speed", 0.0)) - float(n_main.get("mean_trip_speed", 0.0)),
        "online_bottleneck_throughput_delta_veh": a_online.get("bottleneck_throughput_veh", 0) - n_online.get("bottleneck_throughput_veh", 0),
        "breakdown_start_delta_s": a_online.get("breakdown_start_s", 0) - n_online.get("breakdown_start_s", 0),
        "congestion_duration_delta_s": a_online.get("congestion_duration_s", 0) - n_online.get("congestion_duration_s", 0),
        "ramp_vehicle_mean_delta_veh": a_online.get("ramp_vehicle_mean_veh", 0.0) - n_online.get("ramp_vehicle_mean_veh", 0.0),
        "ramp_vehicle_max_delta_veh": a_online.get("ramp_vehicle_max_veh", 0.0) - n_online.get("ramp_vehicle_max_veh", 0.0),
        "arrived_at_3600_delta": a_online.get("arrived_at_3600", 0) - n_online.get("arrived_at_3600", 0),
        "unfinished_at_3600_delta": a_online.get("unfinished_at_3600", 0) - n_online.get("unfinished_at_3600", 0),
        "none_completion_rate": n_system.get("completion_rate", 0.0), "alinea_completion_rate": a_system.get("completion_rate", 0.0),
        "none_collision": n_runtime.get("collision", 0), "alinea_collision": a_runtime.get("collision", 0),
        "none_teleport": n_runtime.get("teleport", 0), "alinea_teleport": a_runtime.get("teleport", 0),
        "none_fatal": n_logs.get("fatal", 0), "alinea_fatal": a_logs.get("fatal", 0),
        "none_emergency_braking": n_logs.get("emergency_braking", 0), "alinea_emergency_braking": a_logs.get("emergency_braking", 0),
    }
    positive = valid_pair and row["tts_improvement_ratio"] >= 0.05 and a_online.get("ramp_vehicle_max_veh", 0) <= 80 and all(
        a_runtime.get(key, 0) == 0 for key in ("collision", "teleport")
    ) and all(a_logs.get(key, 0) == 0 for key in ("fatal", "emergency_braking"))
    row["pilot_direction"] = "positive_5pct" if positive else ("neutral_or_negative" if valid_pair else "invalid_pair")
    return row


def build_sumo_command(root: Path, spec: RunSpec, runtime_additional: Path) -> list[str]:
    output = spec.output_dir.resolve()
    return [
        str(SUMO_BINARY.resolve()),
        "--net-file", str((root / "osm.net.xml").resolve()),
        "--route-files", str(spec.route_path.resolve()),
        "--additional-files", str(runtime_additional.resolve()),
        "--begin", "0", "--end", "7200", "--step-length", "1.0", "--seed", "1",
        "--time-to-teleport", "-1", "--max-depart-delay", "1800", "--collision.action", "warn",
        "--tripinfo-output", str((output / "tripinfo.xml").resolve()),
        "--log", str((output / "sumo.log").resolve()),
        "--error-log", str((output / "sumo_error.log").resolve()),
        "--no-step-log", "true", "--duration-log.statistics", "true",
    ]


def _finish_window(spec: RunSpec, window_end_s: int, accumulator: dict[str, object], action: object) -> dict[str, object]:
    count = accumulator["observations"]
    speed = accumulator["speed_numerator"] / accumulator["speed_denominator"] if accumulator["speed_denominator"] else None
    return {
        "scenario": spec.scenario.name, "controller": spec.controller, "seed": spec.seed,
        "window_end_s": window_end_s,
        "mainline_vehicle_observations": accumulator["vehicle_observations"],
        "mainline_speed_mps": speed,
        "mainline_occupancy": accumulator["occupancy_sum"] / count if count else None,
        "bottleneck_throughput_veh": accumulator["vehicle_observations"],
        "ramp_vehicle_mean_veh": accumulator["ramp_vehicle_sum"] / count if count else 0.0,
        "ramp_vehicle_max_veh": accumulator["ramp_vehicle_max"],
        "ramp_halting_mean_veh": accumulator["ramp_halting_sum"] / count if count else 0.0,
        "ramp_halting_max_veh": accumulator["ramp_halting_max"],
        "departed_veh": accumulator["departed"], "arrived_veh": accumulator["arrived"],
        "teleports": accumulator["teleports"], "collisions": accumulator["collisions"],
        "requested_rate_vph": action.requested_rate_vph, "applied_rate_vph": action.applied_rate_vph,
        "green_s": action.green_s, "red_s": action.red_s,
        "state_mismatches": accumulator["state_mismatches"],
    }


def _new_window_accumulator() -> dict[str, object]:
    return {
        "observations": 0, "vehicle_observations": 0, "speed_numerator": 0.0,
        "speed_denominator": 0, "occupancy_sum": 0.0, "ramp_vehicle_sum": 0.0,
        "ramp_vehicle_max": 0, "ramp_halting_sum": 0.0, "ramp_halting_max": 0,
        "departed": 0, "arrived": 0, "teleports": 0, "collisions": 0,
        "state_mismatches": 0,
    }


def _congestion_metrics(windows: list[dict[str, object]]) -> tuple[int, int]:
    runs = []
    current = []
    for row in windows:
        speed = row["mainline_speed_mps"]
        if speed is not None and speed < 16.67:
            current.append(row)
        else:
            if current:
                runs.append(current)
                current = []
    if current:
        runs.append(current)
    qualified = [run for run in runs if len(run) >= 10]
    if not qualified:
        return 0, 0
    return qualified[0][0]["window_end_s"] - WINDOW_S, sum(len(run) * WINDOW_S for run in qualified)


def execute_episode(conn: Any, spec: RunSpec) -> dict[str, object]:
    initial_j5_state = conn.trafficlight.getRedYellowGreenState(OSM_CONTROL_PROFILE.tls_id)
    controller = NoControlController(default_config().alinea) if spec.controller == "none" else AlineaController(default_config().alinea)
    config = default_config().alinea
    action = None
    windows = []
    accumulator = _new_window_accumulator()
    controller_update_times = []
    state_mismatches = 0
    mainline_departed_ids = set()
    ramp_departed_ids = set()
    arrived_total = 0
    teleport_total = 0
    collision_total = 0
    arrived_at_3600 = 0
    unfinished_at_3600 = spec.scenario.nominal_veh
    detector_read_counts = {detector_id: 0 for detector_id in OSM_CONTROL_PROFILE.mainline_detector_ids + (OSM_CONTROL_PROFILE.ramp_queue_detector_id, OSM_CONTROL_PROFILE.ramp_arrival_detector_id)}

    while True:
        time_s = int(round(conn.simulation.getTime()))
        if time_s % config.cycle_s == 0:
            occupancy = aggregate_mainline_occupancy(conn)
            queue_veh, _ = read_ramp_queue(conn)
            action = controller.update(occupancy, queue_veh)
            controller_update_times.append(time_s)
        commanded = apply_ramp_signal(conn, action, time_s, config.cycle_s)
        observed = conn.trafficlight.getRedYellowGreenState(OSM_CONTROL_PROFILE.tls_id)
        if commanded != observed:
            state_mismatches += 1
        conn.simulationStep()

        occupancies = [conn.inductionloop.getLastStepOccupancy(detector_id) for detector_id in OSM_CONTROL_PROFILE.mainline_detector_ids]
        vehicle_counts = [conn.inductionloop.getLastStepVehicleNumber(detector_id) for detector_id in OSM_CONTROL_PROFILE.mainline_detector_ids]
        speeds = [conn.inductionloop.getLastStepMeanSpeed(detector_id) for detector_id in OSM_CONTROL_PROFILE.mainline_detector_ids]
        ramp_arrival_count = conn.inductionloop.getLastStepVehicleNumber(OSM_CONTROL_PROFILE.ramp_arrival_detector_id)
        ramp_vehicle_count = conn.lanearea.getLastStepVehicleNumber(OSM_CONTROL_PROFILE.ramp_queue_detector_id)
        ramp_halting_count = conn.lanearea.getLastStepHaltingNumber(OSM_CONTROL_PROFILE.ramp_queue_detector_id)
        departed_ids = conn.simulation.getDepartedIDList()
        arrived_ids = conn.simulation.getArrivedIDList()
        step_teleport = conn.simulation.getStartingTeleportNumber()
        step_collision = conn.simulation.getCollidingVehiclesNumber()
        for detector_id in detector_read_counts:
            detector_read_counts[detector_id] += 1
        mainline_departed_ids.update(item for item in departed_ids if item.startswith("mainline_"))
        ramp_departed_ids.update(item for item in departed_ids if item.startswith("ramp_"))
        arrived_total += len(arrived_ids)
        teleport_total += step_teleport
        collision_total += step_collision

        if time_s < DEMAND_END_S:
            accumulator["observations"] += 1
            accumulator["vehicle_observations"] += sum(vehicle_counts)
            for count, speed in zip(vehicle_counts, speeds):
                if count > 0 and speed >= 0:
                    accumulator["speed_numerator"] += count * speed
                    accumulator["speed_denominator"] += count
            accumulator["occupancy_sum"] += sum(occupancies) / 200.0
            accumulator["ramp_vehicle_sum"] += ramp_vehicle_count
            accumulator["ramp_vehicle_max"] = max(accumulator["ramp_vehicle_max"], ramp_vehicle_count)
            accumulator["ramp_halting_sum"] += ramp_halting_count
            accumulator["ramp_halting_max"] = max(accumulator["ramp_halting_max"], ramp_halting_count)
            accumulator["departed"] = len(mainline_departed_ids) + len(ramp_departed_ids)
            accumulator["arrived"] = arrived_total
            accumulator["teleports"] = teleport_total
            accumulator["collisions"] = collision_total
            accumulator["state_mismatches"] = state_mismatches
            actual_time = int(round(conn.simulation.getTime()))
            if actual_time % WINDOW_S == 0 and actual_time <= DEMAND_END_S:
                windows.append(_finish_window(spec, actual_time, accumulator, action))
                accumulator = _new_window_accumulator()

        actual_time = int(round(conn.simulation.getTime()))
        min_expected = conn.simulation.getMinExpectedNumber()
        if actual_time == DEMAND_END_S:
            arrived_at_3600 = arrived_total
            unfinished_at_3600 = max(spec.scenario.nominal_veh - arrived_total, 0)
        if actual_time >= DEMAND_END_S and min_expected == 0:
            break
        if actual_time >= MAX_END_S:
            break

    actual_end_s = int(round(conn.simulation.getTime()))
    terminal_expected = conn.simulation.getMinExpectedNumber()
    breakdown_start_s, congestion_duration_s = _congestion_metrics(windows)
    return {
        "initial_j5_state": initial_j5_state,
        "windows": windows,
        "actual_end_s": actual_end_s,
        "terminal_expected_veh": terminal_expected,
        "terminal_censoring": terminal_expected > 0,
        "controller_update_times": controller_update_times,
        "state_mismatches": state_mismatches,
        "detector_read_counts": detector_read_counts,
        "mainline_departed": len(mainline_departed_ids),
        "ramp_departed": len(ramp_departed_ids),
        "arrived": arrived_total,
        "teleport": teleport_total,
        "collision": collision_total,
        "arrived_at_3600": arrived_at_3600,
        "unfinished_at_3600": unfinished_at_3600,
        "breakdown_start_s": breakdown_start_s,
        "congestion_duration_s": congestion_duration_s,
    }


def write_windows(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=WINDOW_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


RUN_SUMMARY_FIELDS = [
    "scenario", "mainline_vph", "ramp_vph", "controller", "seed", "valid",
    "actual_end_s", "terminal_expected_veh", "terminal_censoring", "errors",
    "mainline_departed", "ramp_departed", "arrived", "teleport", "collision",
    "mainline_completion_rate", "ramp_completion_rate", "system_completion_rate",
    "mainline_total_timeLoss", "ramp_total_timeLoss", "system_total_timeLoss",
    "system_total_system_time_s", "mainline_aggregate_speed", "ramp_mean_trip_speed",
    "online_mainline_speed_mps", "online_bottleneck_throughput_veh",
    "online_ramp_vehicle_mean_veh", "online_ramp_vehicle_max_veh",
    "online_breakdown_start_s", "online_congestion_duration_s",
    "online_arrived_at_3600", "online_unfinished_at_3600", "fatal",
    "emergency_braking", "warning", "formal_sumo_start_count",
]
PAIR_FIELDS = [
    "scenario", "seed", "valid_pair",
    "none_actual_end_s", "alinea_actual_end_s",
    "none_total_system_time_s", "alinea_total_system_time_s", "tts_improvement_ratio",
    "none_system_total_timeLoss", "alinea_system_total_timeLoss", "system_timeLoss_improvement_ratio",
    "none_mainline_total_timeLoss", "alinea_mainline_total_timeLoss", "mainline_timeLoss_improvement_ratio",
    "none_ramp_total_timeLoss", "alinea_ramp_total_timeLoss", "ramp_timeLoss_improvement_ratio",
    "mainline_mean_trip_speed_delta_mps", "online_bottleneck_throughput_delta_veh",
    "breakdown_start_delta_s", "congestion_duration_delta_s",
    "ramp_vehicle_mean_delta_veh", "ramp_vehicle_max_delta_veh",
    "arrived_at_3600_delta", "unfinished_at_3600_delta",
    "none_completion_rate", "alinea_completion_rate",
    "none_collision", "alinea_collision", "none_teleport", "alinea_teleport",
    "none_fatal", "alinea_fatal", "none_emergency_braking", "alinea_emergency_braking",
    "pilot_direction",
]


def _import_traci() -> object:
    sumo_home = os.environ.get("SUMO_HOME")
    if sumo_home:
        tools_dir = str(Path(sumo_home) / "tools")
        if tools_dir not in sys.path:
            sys.path.insert(0, tools_dir)
    return importlib.import_module("traci")


def _sumo_version_text(binary: Path) -> str:
    result = subprocess.run([str(binary), "--version"], capture_output=True, text=True, check=False)
    return (result.stdout + result.stderr).strip()


def _traci_version_info(traci_module: object) -> tuple[str, str]:
    constants = getattr(traci_module, "constants", None)
    value = getattr(constants, "TRACI_VERSION", None)
    if value is not None:
        return str(value), "traci.constants.TRACI_VERSION"
    return "not_recorded_during_formal_run", "not_recorded_during_formal_run"


def _traci_version_reason(source: str) -> str:
    if source == "not_recorded_during_formal_run":
        return "The formal run did not record a package-level TraCI version; no version is inferred from the protocol constant."
    return f"Recorded from {source}."


def _jsonable(value: object) -> object:
    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


def validate_preconditions(root: Path, output_root: Path, sumo_binary: Path) -> dict[str, object]:
    root, output_root, sumo_binary = Path(root), Path(output_root), Path(sumo_binary)
    errors: list[str] = []
    hashes: dict[str, str] = {}
    expected = {"osm.net.xml": NET_SHA256, "control_adapter/osm_control.add.xml": DETECTOR_SHA256}
    for scenario in SCENARIOS:
        expected[f"demand_delivery/fixed/{scenario.name}.rou.xml"] = scenario.route_sha256
    for name, digest in expected.items():
        path = root / name
        if not path.exists():
            errors.append(f"missing frozen input: {name}")
            continue
        actual = sha256(path)
        hashes[name] = actual
        if actual != digest:
            errors.append(f"hash mismatch: {name}")
    protection_errors = check_protection_manifest(root)
    errors.extend(protection_errors)
    if not sumo_binary.exists():
        errors.append(f"missing SUMO binary: {sumo_binary}")
    if "sumo-gui" in sumo_binary.name.lower():
        errors.append("SUMO-GUI is forbidden")
    if output_root.exists() and any(output_root.iterdir()):
        errors.append("output directory is non-empty")
    matrix = build_run_matrix(root, output_root)
    return {"valid": not errors, "errors": errors, "protection_errors": protection_errors,
            "hashes": hashes, "matrix": matrix}


def _write_csv(path: Path, fields: list[str], rows: list[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _online_summary(episode: dict[str, object]) -> dict[str, object]:
    windows = episode["windows"]
    valid_speed = [float(row["mainline_speed_mps"]) for row in windows if row["mainline_speed_mps"] is not None]
    return {
        "mainline_speed_mps": sum(valid_speed) / len(valid_speed) if valid_speed else None,
        "bottleneck_throughput_veh": sum(int(row["bottleneck_throughput_veh"]) for row in windows),
        "ramp_vehicle_mean_veh": sum(float(row["ramp_vehicle_mean_veh"]) for row in windows) / len(windows),
        "ramp_vehicle_max_veh": max(int(row["ramp_vehicle_max_veh"]) for row in windows),
        "breakdown_start_s": episode["breakdown_start_s"],
        "congestion_duration_s": episode["congestion_duration_s"],
        "arrived_at_3600": episode["arrived_at_3600"],
        "unfinished_at_3600": episode["unfinished_at_3600"],
    }


def run_one(root: Path, spec: RunSpec, traci_module: object) -> dict[str, object]:
    run_dir = spec.output_dir
    native_dir = run_dir / "native"
    run_dir.mkdir(parents=True, exist_ok=True)
    native_dir.mkdir(parents=True, exist_ok=True)
    runtime_detector = run_dir / "runtime_osm_control.add.xml"
    write_runtime_detector_file(root / "control_adapter/osm_control.add.xml", runtime_detector, native_dir)
    command = build_sumo_command(root, spec, runtime_detector)
    summary: dict[str, object] = {"scenario": spec.scenario.name, "controller": spec.controller,
                                   "seed": spec.seed, "valid": False, "errors": [],
                                   "formal_sumo_start_count": 1}
    conn = None
    try:
        label = f"{spec.scenario.name}_{spec.controller}_seed{spec.seed}"
        traci_module.start(command, label=label)
        conn = traci_module.getConnection(label)
        episode = execute_episode(conn, spec)
        if hasattr(conn, "close"):
            conn.close()
        conn = None
        native_throughput = parse_native_mainline_throughput(
            native_dir / "osm_det_main_down_1.xml", native_dir / "osm_det_main_down_2.xml"
        )
        if native_throughput["errors"]:
            raise ValueError("native throughput parse failed: " + "; ".join(native_throughput["errors"]))
        for row, native_row in zip(episode["windows"], native_throughput["windows"]):
            row["bottleneck_throughput_veh"] = native_row["bottleneck_throughput_veh"]
        windows_path = run_dir / "window_metrics.csv"
        write_windows(windows_path, episode["windows"])
        tripinfo = parse_tripinfo(run_dir / "tripinfo.xml", spec)
        logs = scan_logs(run_dir)
        errors: list[str] = []
        if len(episode["windows"]) != 120:
            errors.append("window count is not 120")
        if episode["mainline_departed"] != spec.scenario.mainline_veh or episode["ramp_departed"] != spec.scenario.ramp_veh:
            errors.append("departed counts do not match nominal demand")
        if episode["terminal_censoring"] or episode["state_mismatches"]:
            errors.append("episode did not clear cleanly")
        if episode["teleport"] or episode["collision"] or logs["fatal"] or logs["emergency_braking"]:
            errors.append("safety or fatal log count is non-zero")
        if any(count != episode["actual_end_s"] for count in episode["detector_read_counts"].values()):
            errors.append("detector read count mismatch")
        if tripinfo["system"]["completed"] != spec.scenario.nominal_veh:
            errors.append("tripinfo completion count mismatch")
        online = _online_summary(episode)
        runtime = {key: episode[key] for key in ("mainline_departed", "ramp_departed", "arrived", "teleport", "collision")}
        runtime.update({"evaluation_throughput": native_throughput["evaluation_total"],
                        "terminal_throughput": native_throughput["terminal_total"]})
        summary.update({"valid": not errors, "errors": errors, "actual_end_s": episode["actual_end_s"],
                        "terminal_expected_veh": episode["terminal_expected_veh"],
                        "terminal_censoring": episode["terminal_censoring"], "runtime": runtime,
                        "online": online, "tripinfo": tripinfo, "log_counts": logs})
    except Exception as exc:
        summary["errors"] = [f"{type(exc).__name__}: {exc}"]
        if conn is not None and hasattr(conn, "close"):
            conn.close()
    (run_dir / "run_summary.json").write_text(json.dumps(_jsonable(summary), indent=2), encoding="utf-8")
    return summary


def _flat_run_row(summary: dict[str, object], spec: RunSpec) -> dict[str, object]:
    trip = summary.get("tripinfo", {})
    system, mainline, ramp = trip.get("system", {}), trip.get("mainline", {}), trip.get("ramp", {})
    online, runtime, logs = summary.get("online", {}), summary.get("runtime", {}), summary.get("log_counts", {})
    return {"scenario": spec.scenario.name, "mainline_vph": spec.scenario.mainline_vph, "ramp_vph": spec.scenario.ramp_vph,
            "controller": spec.controller, "seed": spec.seed, "valid": summary.get("valid", False),
            "actual_end_s": summary.get("actual_end_s", ""), "terminal_expected_veh": summary.get("terminal_expected_veh", ""),
            "terminal_censoring": summary.get("terminal_censoring", True), "errors": json.dumps(summary.get("errors", [])),
            "mainline_departed": runtime.get("mainline_departed", ""), "ramp_departed": runtime.get("ramp_departed", ""),
            "arrived": runtime.get("arrived", ""), "teleport": runtime.get("teleport", ""), "collision": runtime.get("collision", ""),
            "mainline_completion_rate": mainline.get("completion_rate", ""), "ramp_completion_rate": ramp.get("completion_rate", ""),
            "system_completion_rate": system.get("completion_rate", ""), "mainline_total_timeLoss": mainline.get("total_timeLoss", ""),
            "ramp_total_timeLoss": ramp.get("total_timeLoss", ""), "system_total_timeLoss": system.get("total_timeLoss", ""),
            "system_total_system_time_s": system.get("total_system_time_s", ""), "mainline_aggregate_speed": mainline.get("aggregate_speed", ""),
            "ramp_mean_trip_speed": ramp.get("mean_trip_speed", ""), "online_mainline_speed_mps": online.get("mainline_speed_mps", ""),
            "online_bottleneck_throughput_veh": online.get("bottleneck_throughput_veh", ""), "online_ramp_vehicle_mean_veh": online.get("ramp_vehicle_mean_veh", ""),
            "online_ramp_vehicle_max_veh": online.get("ramp_vehicle_max_veh", ""), "online_breakdown_start_s": online.get("breakdown_start_s", ""),
            "online_congestion_duration_s": online.get("congestion_duration_s", ""), "online_arrived_at_3600": online.get("arrived_at_3600", ""),
            "online_unfinished_at_3600": online.get("unfinished_at_3600", ""), "fatal": logs.get("fatal", ""),
            "emergency_braking": logs.get("emergency_braking", ""), "warning": logs.get("warning", ""), "formal_sumo_start_count": 1}


def run_pilot(root: Path = ROOT, output_root: Path = OUTPUT_ROOT, traci_module: object | None = None) -> dict[str, object]:
    preflight = validate_preconditions(root, output_root, SUMO_BINARY)
    if not preflight["valid"]:
        return preflight
    traci_module = traci_module or _import_traci()
    traci_version, traci_version_source = _traci_version_info(traci_module)
    output_root.mkdir(parents=True, exist_ok=False)
    rows: list[dict[str, object]] = []
    summaries: list[dict[str, object]] = []
    matrix = preflight["matrix"]
    for spec in matrix:
        summary = run_one(root, spec, traci_module)
        summaries.append(summary)
        rows.append(_flat_run_row(summary, spec))
        _write_csv(output_root / "run_summary.csv", RUN_SUMMARY_FIELDS, rows)
        if not summary.get("valid", False):
            manifest = {"schema": "stage4c-a-osm-paired-pilot-v1", "valid": False, "errors": summary.get("errors", []),
                        "formal_sumo_start_count": len(summaries), "runs_completed": len(summaries),
                        "sumo_version": _sumo_version_text(SUMO_BINARY),
                        "traci_version": traci_version, "traci_version_source": traci_version_source,
                        "traci_version_reason": _traci_version_reason(traci_version_source),
                        "input_hashes": preflight["hashes"], "matrix": [_jsonable(spec) for spec in matrix],
                        "run_order": [{"scenario": s["scenario"], "controller": s["controller"],
                                       "seed": s["seed"], "valid": s.get("valid", False),
                                       "status": "valid" if s.get("valid", False) else "invalid"} for s in summaries],
                        "protection_errors": preflight["protection_errors"]}
            (output_root / "pilot_manifest.json").write_text(json.dumps(_jsonable(manifest), indent=2), encoding="utf-8")
            return manifest
    pairs = []
    for index in range(0, len(summaries), 2):
        pair = compare_pair(summaries[index], summaries[index + 1])
        pairs.append(pair)
    _write_csv(output_root / "pair_comparison.csv", PAIR_FIELDS, pairs)
    post_errors = check_protection_manifest(root)
    run_order = [{"scenario": summary["scenario"], "controller": summary["controller"],
                  "seed": summary["seed"], "valid": summary.get("valid", False),
                  "status": "valid" if summary.get("valid", False) else "invalid"}
                 for summary in summaries]
    manifest = {"schema": "stage4c-a-osm-paired-pilot-v1", "valid": not post_errors,
                "errors": post_errors, "formal_sumo_start_count": len(summaries),
                "runs_completed": len(summaries), "sumo_version": _sumo_version_text(SUMO_BINARY),
                "traci_version": traci_version, "traci_version_source": traci_version_source,
                "traci_version_reason": _traci_version_reason(traci_version_source),
                "input_hashes": preflight["hashes"], "matrix": [_jsonable(spec) for spec in matrix],
                "run_order": run_order, "protection_errors": post_errors}
    (output_root / "pilot_manifest.json").write_text(json.dumps(_jsonable(manifest), indent=2), encoding="utf-8")
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--preflight-only", action="store_true")
    args = parser.parse_args(argv)
    if args.preflight_only:
        result = validate_preconditions(ROOT, OUTPUT_ROOT, SUMO_BINARY)
        print("PRECHECK_PASS" if result["valid"] else "PRECHECK_FAIL")
        return 0 if result["valid"] else 2
    result = run_pilot()
    print(f"FORMAL_SUMO_START_COUNT={result.get('formal_sumo_start_count', 0)}")
    print(f"MANIFEST_PATH={OUTPUT_ROOT / 'pilot_manifest.json'}")
    print("VALIDATION_PASS" if result.get("valid") else "VALIDATION_FAIL")
    return 0 if result.get("valid") else 2


if __name__ == "__main__":
    raise SystemExit(main())
