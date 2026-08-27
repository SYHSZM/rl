from __future__ import annotations

import argparse
import csv
import importlib
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import calibrated_demand
import osm_paired_pilot as pilot
from analyze_freeflow_reference import check_protection_manifest
from osm_smoke_runner import scan_logs, sha256, write_runtime_detector_file


ROOT = Path(__file__).resolve().parent
OUTPUT_ROOT = ROOT / "outputs/stage4d_mainline_coarse"
MULTISEED_OUTPUT_ROOT = ROOT / "outputs/stage4d_mainline_multiseed"
SUMO_BINARY = pilot.SUMO_BINARY
FLOW_RATES = (1200, 1600, 1750, 1850, 1900, 2000)
DEMAND_END_S = 3600
MAX_END_S = 7200
SEED = 1
MULTISEED_RATES = (1900, 1925, 1950, 1975, 2000)
MULTISEED_SEEDS = (1, 2, 3, 4, 5)

RUN_FIELDS = [
    "mainline_vph", "controller", "seed", "valid", "errors", "actual_end_s",
    "departed", "arrived", "completion_rate", "terminal_censoring",
    "mainline_aggregate_speed", "mainline_mean_trip_speed", "mean_duration",
    "p95_duration", "max_duration", "mean_waitingTime", "p95_waitingTime",
    "max_waitingTime", "total_timeLoss", "total_system_time_s", "mean_departDelay",
    "max_departDelay", "online_mainline_speed_mps", "online_mainline_occupancy",
    "online_bottleneck_throughput_veh", "breakdown_start_s", "congestion_duration_s",
    "collision", "teleport", "fatal", "emergency_braking", "warning", "state_mismatches",
    "status",
]
MULTISEED_FIELDS = RUN_FIELDS + ["evaluation_throughput", "terminal_throughput"]


def _scenario(rate: int, route_sha256: str = "") -> pilot.PilotScenario:
    return pilot.PilotScenario(f"{rate}+0", rate, 0, rate, route_sha256)


def build_scan_matrix(root: Path = ROOT, output_root: Path | None = None) -> list[pilot.RunSpec]:
    output_root = (output_root or root / "outputs/stage4d_mainline_coarse").resolve()
    return [
        pilot.RunSpec(_scenario(rate), "none", SEED,
                      output_root / "routes" / f"main{rate}_ramp0.rou.xml",
                      output_root / f"main{rate}_ramp0" / "none_seed1")
        for rate in FLOW_RATES
    ]


def build_multiseed_scan_matrix(root: Path, output_root: Path,
                                rates: tuple[int, ...], seeds: tuple[int, ...]) -> list[pilot.RunSpec]:
    output_root = Path(output_root).resolve()
    return [
        pilot.RunSpec(
            _scenario(rate), "none", seed,
            output_root / "routes" / f"main{rate}_ramp0.rou.xml",
            output_root / f"main{rate}_ramp0" / f"none_seed{seed}",
        )
        for rate in rates for seed in seeds
    ]


def classify_windows(rows: list[dict[str, object]], seed: int = SEED) -> dict[str, object]:
    if not rows:
        return {"status": "invalid", "breakdown_start_s": 0, "congestion_duration_s": 0}
    runs: list[list[dict[str, object]]] = []
    current: list[dict[str, object]] = []
    observed = False
    for row in rows:
        speed = row.get("mainline_speed_mps")
        if speed is None:
            if current:
                runs.append(current)
                current = []
            continue
        observed = True
        if float(speed) < 16.67:
            current.append(row)
        elif current:
            runs.append(current)
            current = []
    if current:
        runs.append(current)
    if not observed:
        return {"status": "invalid", "breakdown_start_s": 0, "congestion_duration_s": 0}
    qualified = [run for run in runs if len(run) >= 10]
    duration = sum(len(run) * pilot.WINDOW_S for run in qualified)
    start = qualified[0][0]["window_end_s"] - pilot.WINDOW_S if qualified else 0
    status = f"congested_seed{seed}" if qualified else f"uncongested_seed{seed}"
    return {"status": status, "breakdown_start_s": start, "congestion_duration_s": duration}


def summarize_bracket(rows: list[dict[str, object]]) -> dict[str, object]:
    ordered = sorted(rows, key=lambda row: int(row["mainline_vph"]))
    uncongested = [row for row in ordered if row["status"] == "uncongested_seed1"]
    congested = [row for row in ordered if row["status"] == "congested_seed1"]
    bad_order = any(
        ordered[index]["status"] == "congested_seed1" and ordered[index + 1]["status"] == "uncongested_seed1"
        for index in range(len(ordered) - 1)
    )
    if bad_order:
        return {"highest_uncongested_vph": "", "first_congested_vph": "", "bracket_found": False,
                "monotonicity_valid": False, "status": "non_monotonic_seed1"}
    highest = max((int(row["mainline_vph"]) for row in uncongested), default="lower_bound_not_found")
    first = min((int(row["mainline_vph"]) for row in congested), default="upper_bound_not_found")
    return {"highest_uncongested_vph": highest, "first_congested_vph": first,
            "bracket_found": bool(uncongested and congested),
            "monotonicity_valid": True, "status": "bracket_found" if uncongested and congested else "no_bracket"}


def _import_traci() -> object:
    sumo_home = os.environ.get("SUMO_HOME")
    if sumo_home and str(Path(sumo_home) / "tools") not in sys.path:
        sys.path.insert(0, str(Path(sumo_home) / "tools"))
    return importlib.import_module("traci")


def _traci_info(module: object) -> tuple[str, str]:
    value = getattr(getattr(module, "constants", None), "TRACI_VERSION", None)
    return (str(value), "traci.constants.TRACI_VERSION") if value is not None else (
        "not_recorded_during_formal_run", "not_recorded_during_formal_run")


def parse_native_mainline_throughput(detector_1_path: Path, detector_2_path: Path) -> dict[str, object]:
    return pilot.parse_native_mainline_throughput(detector_1_path, detector_2_path)


def validate_preconditions(root: Path, output_root: Path, sumo_binary: Path) -> dict[str, object]:
    root, output_root = Path(root), Path(output_root)
    errors: list[str] = []
    hashes: dict[str, str] = {}
    expected = {
        "osm.net.xml": pilot.NET_SHA256,
        "control_adapter/osm_control.add.xml": pilot.DETECTOR_SHA256,
        "osm.rou.xml": sha256(root / "osm.rou.xml") if (root / "osm.rou.xml").exists() else "",
    }
    for name, expected_hash in expected.items():
        path = root / name
        if not path.exists():
            errors.append(f"missing input: {name}")
        else:
            hashes[name] = sha256(path)
            if expected_hash and hashes[name] != expected_hash:
                errors.append(f"hash mismatch: {name}")
    protection_errors = check_protection_manifest(root)
    errors.extend(protection_errors)
    if not sumo_binary.exists(): errors.append("missing SUMO binary")
    if "sumo-gui" in sumo_binary.name.lower(): errors.append("SUMO-GUI is forbidden")
    if output_root.exists() and any(output_root.iterdir()): errors.append("output directory is non-empty")
    matrix = build_scan_matrix(root, output_root)
    return {"valid": not errors, "errors": errors, "hashes": hashes,
            "protection_errors": protection_errors, "matrix": matrix}


def validate_multiseed_preconditions(root: Path, output_root: Path, sumo_binary: Path) -> dict[str, object]:
    result = validate_preconditions(root, output_root, sumo_binary)
    errors = list(result["errors"])
    config = Path(root) / "check.sumocfg"
    if not config.exists():
        errors.append("missing input: check.sumocfg")
    matrix = build_multiseed_scan_matrix(root, output_root, MULTISEED_RATES, MULTISEED_SEEDS)
    result.update({"valid": not errors, "errors": errors, "matrix": matrix,
                   "rates": MULTISEED_RATES, "seeds": MULTISEED_SEEDS})
    return result


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=RUN_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _write_multiseed_csv(path: Path, rows: list[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=MULTISEED_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _route_specs_from_matrix(root: Path, matrix: list[pilot.RunSpec], mode: str) -> list[pilot.RunSpec]:
    specs: list[pilot.RunSpec] = []
    for spec in matrix:
        scenario = _scenario(spec.scenario.mainline_vph)
        path = spec.route_path
        calibrated_demand.write_tree(calibrated_demand.build_mainline_fixed_tree(
            root / "osm.rou.xml", calibrated_demand.CalibratedScenario(
                scenario.name, scenario.mainline_vph, 0, mode)), path)
        specs.append(pilot.RunSpec(_scenario(scenario.mainline_vph, sha256(path)), spec.controller,
                                  spec.seed, path, spec.output_dir))
    return specs


def _route_specs(root: Path, output_root: Path) -> list[pilot.RunSpec]:
    return _route_specs_from_matrix(root, build_scan_matrix(root, output_root), "coarse")


def _build_seeded_sumo_command(root: Path, spec: pilot.RunSpec, runtime_detector: Path) -> list[str]:
    command = pilot.build_sumo_command(root, spec, runtime_detector)
    index = command.index("--seed") + 1
    command[index] = str(spec.seed)
    return command


def _run_one(root: Path, spec: pilot.RunSpec, traci_module: object,
             command_builder: object = pilot.build_sumo_command) -> dict[str, object]:
    run_dir, native_dir = spec.output_dir, spec.output_dir / "native"
    run_dir.mkdir(parents=True, exist_ok=True)
    native_dir.mkdir(parents=True, exist_ok=True)
    runtime_detector = run_dir / "runtime_osm_control.add.xml"
    write_runtime_detector_file(root / "control_adapter/osm_control.add.xml", runtime_detector, native_dir)
    command = command_builder(root, spec, runtime_detector)
    label = f"main{spec.scenario.mainline_vph}_ramp0_none_seed{spec.seed}"
    result: dict[str, object] = {"mainline_vph": spec.scenario.mainline_vph, "controller": "none", "seed": spec.seed,
                                 "valid": False, "errors": [], "formal_sumo_start_count": 1}
    conn = None
    try:
        traci_module.start(command, label=label)
        conn = traci_module.getConnection(label)
        episode = pilot.execute_episode(conn, spec)
        initial_state = episode["initial_j5_state"]
        if hasattr(conn, "close"): conn.close()
        conn = None
        native_throughput = pilot.parse_native_mainline_throughput(
            native_dir / "osm_det_main_down_1.xml", native_dir / "osm_det_main_down_2.xml"
        )
        if native_throughput["errors"]:
            raise ValueError("native throughput parse failed: " + "; ".join(native_throughput["errors"]))
        for row, native_row in zip(episode["windows"], native_throughput["windows"]):
            row["bottleneck_throughput_veh"] = native_row["bottleneck_throughput_veh"]
        pilot.write_windows(run_dir / "window_metrics.csv", episode["windows"])
        trip = pilot.parse_tripinfo(run_dir / "tripinfo.xml", spec)
        logs = scan_logs(run_dir)
        online = pilot._online_summary(episode)
        online.update({
            "green_s": sum(int(row["green_s"]) for row in episode["windows"]),
            "red_s": sum(int(row["red_s"]) for row in episode["windows"]),
            "mainline_occupancy": sum(float(row["mainline_occupancy"]) for row in episode["windows"]) / len(episode["windows"]),
        })
        errors: list[str] = []
        cls = classify_windows(episode["windows"], spec.seed)
        if len(episode["windows"]) != 120: errors.append("window count is not 120")
        if initial_state != "GG": errors.append("initial J5 state is not GG")
        if online["red_s"] != 0 or episode["state_mismatches"]: errors.append("signal was not always green")
        if episode["mainline_departed"] != spec.scenario.mainline_vph: errors.append("departed count mismatch")
        if trip["system"]["completed"] != spec.scenario.mainline_vph: errors.append("tripinfo completion mismatch")
        if episode["terminal_censoring"] or episode["collision"] or episode["teleport"]: errors.append("episode safety/clearance failure")
        if logs["fatal"] or logs["emergency_braking"]: errors.append("fatal or emergency braking log")
        runtime = {"departed": episode["mainline_departed"], "arrived": episode["arrived"],
                   "collision": episode["collision"], "teleport": episode["teleport"],
                   "evaluation_throughput": native_throughput["evaluation_total"],
                   "terminal_throughput": native_throughput["terminal_total"]}
        result.update({"valid": not errors, "errors": errors, "actual_end_s": episode["actual_end_s"],
                       "terminal_censoring": episode["terminal_censoring"], "runtime": runtime,
                       "online": online, "tripinfo": trip, "log_counts": logs, "classification": cls,
                       "state_mismatches": episode["state_mismatches"]})
    except Exception as exc:
        result["errors"] = [f"{type(exc).__name__}: {exc}"]
        if conn is not None and hasattr(conn, "close"): conn.close()
    (run_dir / "run_summary.json").write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    return result


def _flat(summary: dict[str, object]) -> dict[str, object]:
    trip = summary.get("tripinfo", {}).get("system", {})
    online, runtime, logs = summary.get("online", {}), summary.get("runtime", {}), summary.get("log_counts", {})
    return {"mainline_vph": summary["mainline_vph"], "controller": "none", "seed": summary.get("seed", 1),
            "valid": summary.get("valid", False), "errors": json.dumps(summary.get("errors", [])),
            "actual_end_s": summary.get("actual_end_s", ""), "departed": runtime.get("departed", ""),
            "arrived": runtime.get("arrived", ""), "completion_rate": trip.get("completion_rate", ""),
            "terminal_censoring": summary.get("terminal_censoring", True),
            "mainline_aggregate_speed": summary.get("tripinfo", {}).get("mainline", {}).get("aggregate_speed", ""),
            "mainline_mean_trip_speed": summary.get("tripinfo", {}).get("mainline", {}).get("mean_trip_speed", ""),
            "mean_duration": trip.get("mean_duration", ""), "p95_duration": trip.get("p95_duration", ""),
            "max_duration": trip.get("max_duration", ""), "mean_waitingTime": trip.get("mean_waitingTime", ""),
            "p95_waitingTime": trip.get("p95_waitingTime", ""), "max_waitingTime": trip.get("max_waitingTime", ""),
            "total_timeLoss": trip.get("total_timeLoss", ""), "total_system_time_s": trip.get("total_system_time_s", ""),
            "mean_departDelay": trip.get("mean_departDelay", ""), "max_departDelay": trip.get("max_departDelay", ""),
            "online_mainline_speed_mps": online.get("mainline_speed_mps", ""),
            "online_mainline_occupancy": online.get("mainline_occupancy", ""),
            "online_bottleneck_throughput_veh": online.get("bottleneck_throughput_veh", ""),
            "breakdown_start_s": summary.get("classification", {}).get("breakdown_start_s", ""),
            "congestion_duration_s": summary.get("classification", {}).get("congestion_duration_s", ""),
            "collision": runtime.get("collision", ""), "teleport": runtime.get("teleport", ""),
            "fatal": logs.get("fatal", ""), "emergency_braking": logs.get("emergency_braking", ""),
            "warning": logs.get("warning", ""), "state_mismatches": summary.get("state_mismatches", ""),
            "status": summary.get("classification", {}).get("status", "invalid"),
            "evaluation_throughput": runtime.get("evaluation_throughput", ""),
            "terminal_throughput": runtime.get("terminal_throughput", "")}


def _manifest(root: Path, preflight: dict[str, object], specs: list[pilot.RunSpec], summaries: list[dict[str, object]],
              traci_module: object, bracket: dict[str, object]) -> dict[str, object]:
    traci_version, traci_source = _traci_info(traci_module)
    return {"schema": "stage4d-a-osm-mainline-coarse-v1", "valid": all(s.get("valid") for s in summaries),
            "errors": [error for s in summaries for error in s.get("errors", [])],
            "formal_sumo_start_count": len(summaries), "runs_completed": len(summaries),
            "sumo_version": pilot._sumo_version_text(SUMO_BINARY), "traci_version": traci_version,
            "traci_version_source": traci_source, "input_hashes": preflight["hashes"],
            "route_hashes": {str(s.scenario.mainline_vph): s.scenario.route_sha256 for s in specs},
            "matrix": [pilot._jsonable(s) for s in specs],
            "run_order": [{"mainline_vph": s["mainline_vph"], "valid": s.get("valid", False),
                           "status": "valid" if s.get("valid") else "invalid"} for s in summaries],
            "protection_errors": preflight["protection_errors"], "bracket": bracket}


def run_scan(root: Path = ROOT, output_root: Path = OUTPUT_ROOT, traci_module: object | None = None) -> dict[str, object]:
    preflight = validate_preconditions(root, output_root, SUMO_BINARY)
    if not preflight["valid"]: return preflight
    traci_module = traci_module or _import_traci()
    output_root.mkdir(parents=True, exist_ok=False)
    specs = _route_specs(root, output_root)
    summaries: list[dict[str, object]] = []
    rows: list[dict[str, object]] = []
    for spec in specs:
        summary = _run_one(root, spec, traci_module)
        summaries.append(summary)
        rows.append(_flat(summary))
        _write_csv(output_root / "run_summary.csv", rows)
        if not summary.get("valid"):
            manifest = _manifest(root, preflight, specs[:len(summaries)], summaries, traci_module, {})
            (output_root / "scan_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
            return manifest
    bracket = summarize_bracket([{"mainline_vph": r["mainline_vph"], "status": r["status"]} for r in rows])
    (output_root / "coarse_bracket.json").write_text(json.dumps(bracket, indent=2), encoding="utf-8")
    manifest = _manifest(root, preflight, specs, summaries, traci_module, bracket)
    (output_root / "scan_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def aggregate_multiseed_results(summaries: list[dict[str, object]], rates: tuple[int, ...],
                                seeds: tuple[int, ...]) -> dict[str, dict[str, object]]:
    expected = set(seeds)
    aggregate: dict[str, dict[str, object]] = {}
    for rate in rates:
        by_seed = {
            int(summary["seed"]): summary
            for summary in summaries
            if int(summary.get("mainline_vph", -1)) == rate
            and int(summary.get("seed", -1)) in expected
            and summary.get("valid")
        }
        ordered = [by_seed[seed] for seed in seeds if seed in by_seed]
        congested = [summary for summary in ordered
                     if str(summary.get("classification", {}).get("status", "")).startswith("congested")]
        if len(ordered) != len(seeds):
            status = "invalid"
        elif len(congested) == 0:
            status = "stable_free_flow_upper_bound_candidate"
        elif len(congested) == len(seeds):
            status = "oversaturated_candidate"
        else:
            status = "critical_candidate"
        def mean(values: list[object]) -> float | None:
            numbers = [float(value) for value in values if value not in (None, "")]
            return sum(numbers) / len(numbers) if numbers else None

        system = [summary.get("tripinfo", {}).get("system", {}) for summary in ordered]
        online = [summary.get("online", {}) for summary in ordered]
        runtime = [summary.get("runtime", {}) for summary in ordered]
        aggregate[str(rate)] = {
            "mainline_vph": rate, "valid_runs": len(ordered), "expected_runs": len(seeds),
            "congested_runs": len(congested), "seed_results": [
                {"seed": int(summary["seed"]), "valid": bool(summary.get("valid")),
                 "status": summary.get("classification", {}).get("status", "invalid"),
                 "congestion_duration_s": summary.get("classification", {}).get("congestion_duration_s", 0),
                 "downstream_window_speed_mps": summary.get("online", {}).get("mainline_speed_mps"),
                 "tts": summary.get("tripinfo", {}).get("system", {}).get("total_system_time_s"),
                 "timeLoss": summary.get("tripinfo", {}).get("system", {}).get("total_timeLoss"),
                 "evaluation_throughput": summary.get("runtime", {}).get("evaluation_throughput"),
                 "terminal_throughput": summary.get("runtime", {}).get("terminal_throughput")}
                for summary in ordered
            ],
            "mean_congestion_duration_s": mean([summary.get("classification", {}).get("congestion_duration_s") for summary in ordered]),
            "mean_downstream_window_speed_mps": mean([item.get("mainline_speed_mps") for item in online]),
            "mean_tts": mean([item.get("total_system_time_s") for item in system]),
            "mean_timeLoss": mean([item.get("total_timeLoss") for item in system]),
            "mean_evaluation_throughput": mean([item.get("evaluation_throughput") for item in runtime]),
            "mean_terminal_throughput": mean([item.get("terminal_throughput") for item in runtime]),
            "classification": status,
        }
    return aggregate


def _write_multiseed_report(path: Path, aggregate: dict[str, dict[str, object]]) -> None:
    lines = ["# Stage 4D-B 纯主线多 seed 流量标定", "",
             "本报告仅提供纯主线分类候选，不能单独证明可控区间。", "",
             "| 流量 | valid/5 | congested/5 | 拥堵持续时间(s) | 下游窗口速度(m/s) | TTS | timeLoss | evaluation throughput | terminal throughput | 分类 |",
             "|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|"]
    for rate, item in aggregate.items():
        lines.append(f"| {rate} | {item['valid_runs']}/{item['expected_runs']} | "
                     f"{item['congested_runs']}/{item['expected_runs']} | {item['mean_congestion_duration_s']} | "
                     f"{item['mean_downstream_window_speed_mps']} | {item['mean_tts']} | {item['mean_timeLoss']} | "
                     f"{item['mean_evaluation_throughput']} | {item['mean_terminal_throughput']} | {item['classification']} |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_multiseed_scan(root: Path = ROOT, output_root: Path = MULTISEED_OUTPUT_ROOT,
                       traci_module: object | None = None) -> dict[str, object]:
    preflight = validate_multiseed_preconditions(root, output_root, SUMO_BINARY)
    if not preflight["valid"]:
        return preflight
    traci_module = traci_module or _import_traci()
    output_root.mkdir(parents=True, exist_ok=False)
    specs = _route_specs_from_matrix(root, preflight["matrix"], "stage4d-b")
    summaries: list[dict[str, object]] = []
    for spec in specs:
        summary = _run_one(root, spec, traci_module, _build_seeded_sumo_command)
        summary["input_hashes"] = preflight["hashes"]
        summary["run_parameters"] = {"mainline_vph": spec.scenario.mainline_vph, "ramp_vph": 0,
                                     "controller": "none", "seed": spec.seed,
                                     "demand_end_s": DEMAND_END_S, "max_end_s": MAX_END_S,
                                     "window_s": pilot.WINDOW_S, "j5": "GG"}
        spec.output_dir.joinpath("run_summary.json").write_text(
            json.dumps(summary, indent=2, default=str), encoding="utf-8")
        summaries.append(summary)
        _write_multiseed_csv(output_root / "run_summary.csv", [_flat(item) for item in summaries])
        if not summary.get("valid"):
            manifest = {"schema": "stage4d-b-osm-mainline-multiseed-v1", "valid": False,
                        "formal_sumo_start_count": len(summaries), "runs_completed": len(summaries),
                        "errors": [error for item in summaries for error in item.get("errors", [])],
                        "input_hashes": preflight["hashes"], "matrix": [pilot._jsonable(s) for s in specs],
                        "run_order": [{"mainline_vph": item["mainline_vph"], "seed": item["seed"],
                                       "valid": item.get("valid", False)} for item in summaries]}
            (output_root / "scan_manifest.json").write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
            return manifest
    aggregate = aggregate_multiseed_results(summaries, MULTISEED_RATES, MULTISEED_SEEDS)
    (output_root / "multiseed_classification.json").write_text(json.dumps(aggregate, indent=2), encoding="utf-8")
    _write_multiseed_report(output_root / "mainline_multiseed_report.md", aggregate)
    manifest = {"schema": "stage4d-b-osm-mainline-multiseed-v1", "valid": True,
                "formal_sumo_start_count": len(summaries), "runs_completed": len(summaries),
                "input_hashes": preflight["hashes"], "matrix": [pilot._jsonable(s) for s in specs],
                "run_order": [{"mainline_vph": item["mainline_vph"], "seed": item["seed"],
                               "valid": item.get("valid", False),
                               "status": item.get("classification", {}).get("status", "invalid")}
                              for item in summaries],
                "classification": aggregate}
    (output_root / "scan_manifest.json").write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--preflight-only", action="store_true")
    args = parser.parse_args(argv)
    if args.preflight_only:
        result = validate_preconditions(ROOT, OUTPUT_ROOT, SUMO_BINARY)
        print(f"PRECHECK_PASS={result['valid']}")
        return 0 if result["valid"] else 2
    result = run_scan()
    print(f"FORMAL_SUMO_START_COUNT={result.get('formal_sumo_start_count', 0)}")
    print(f"MANIFEST_PATH={OUTPUT_ROOT / 'scan_manifest.json'}")
    print(f"VALIDATION_PASS={result.get('valid', False)}")
    return 0 if result.get("valid") else 2


if __name__ == "__main__":
    raise SystemExit(main())
