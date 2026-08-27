from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import calibrated_demand
import osm_paired_pilot as pilot
from analyze_freeflow_reference import check_protection_manifest
from osm_smoke_runner import sha256


ROOT = Path(__file__).resolve().parent
OUTPUT_ROOT = ROOT / "outputs/mixed_demand_screen"
SUMO_BINARY = pilot.SUMO_BINARY
MAINLINE_RATES = (1200, 1925, 1950, 1975)
RAMP_RATES = (30, 60, 90)
DEMAND_END_S = 3600
WINDOW_S = 30

SCREEN_FIELDS = [
    "mainline_vph", "ramp_vph", "controller", "seed", "valid", "classification", "errors",
    "congestion_duration_s", "breakdown_start_s", "online_mainline_speed_mps", "online_mainline_occupancy",
    "system_total_system_time_s", "mainline_total_timeLoss", "ramp_total_timeLoss", "system_total_timeLoss",
    "mainline_evaluation_throughput", "mainline_terminal_throughput", "ramp_evaluation_arrivals", "ramp_terminal_arrivals",
    "online_ramp_vehicle_mean_veh", "online_ramp_vehicle_max_veh", "mainline_completion_rate",
    "ramp_completion_rate", "system_completion_rate", "collision", "teleport", "fatal", "emergency_braking",
]


def build_screen_matrix(root: Path = ROOT, output_root: Path = OUTPUT_ROOT) -> list[pilot.RunSpec]:
    output_root = Path(output_root).resolve()
    return [
        pilot.RunSpec(
            pilot.PilotScenario(f"{mainline}+{ramp}", mainline, ramp, mainline + ramp, ""),
            "none", 1,
            output_root / "routes" / f"main{mainline}_ramp{ramp}.rou.xml",
            output_root / f"main{mainline}_ramp{ramp}" / "none_seed1",
        )
        for mainline in MAINLINE_RATES for ramp in RAMP_RATES
    ]


def validate_preconditions(root: Path = ROOT, output_root: Path = OUTPUT_ROOT,
                           sumo_binary: Path = SUMO_BINARY) -> dict[str, object]:
    root, output_root, sumo_binary = Path(root), Path(output_root), Path(sumo_binary)
    errors: list[str] = []
    for name, expected in (("osm.net.xml", pilot.NET_SHA256),
                           ("control_adapter/osm_control.add.xml", pilot.DETECTOR_SHA256),
                           ("osm.rou.xml", None), ("check.sumocfg", None)):
        path = root / name
        if not path.exists():
            errors.append(f"missing input: {name}")
        elif expected and sha256(path) != expected:
            errors.append(f"hash mismatch: {name}")
    errors.extend(check_protection_manifest(root))
    if not sumo_binary.exists(): errors.append(f"missing SUMO binary: {sumo_binary}")
    if "sumo-gui" in sumo_binary.name.lower(): errors.append("SUMO-GUI is forbidden")
    if output_root.exists() and any(output_root.iterdir()): errors.append("output directory is non-empty")
    matrix = build_screen_matrix(root, output_root)
    return {"valid": not errors, "errors": errors, "protection_errors": check_protection_manifest(root),
            "matrix": matrix, "planned_sumo_starts": len(matrix),
            "input_hashes": {name: sha256(root / name) for name in ("osm.net.xml", "osm.rou.xml",
                                                                       "control_adapter/osm_control.add.xml", "check.sumocfg")
                             if (root / name).exists()}}


def parse_ramp_arrivals(path: Path) -> dict[str, object]:
    errors: list[str] = []
    intervals: list[tuple[float, float, int]] = []
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError) as exc:
        return {"evaluation_total": 0, "terminal_total": 0, "errors": [f"{Path(path).name}: XML parse error: {exc}"]}
    for node in root.findall("interval"):
        try:
            begin, end, value = float(node.attrib["begin"]), float(node.attrib["end"]), int(node.attrib["nVehContrib"])
        except (KeyError, TypeError, ValueError):
            errors.append(f"{Path(path).name}: malformed interval")
            continue
        if node.attrib.get("id") != "osm_det_ramp_arrival": errors.append(f"{Path(path).name}: detector id mismatch")
        if begin < 0 or end <= begin or value < 0: errors.append(f"{Path(path).name}: invalid interval")
        intervals.append((begin, end, value))
    expected = {(float(i * WINDOW_S), float((i + 1) * WINDOW_S)) for i in range(120)}
    evaluation = {(begin, end): value for begin, end, value in intervals if begin >= 0 and end <= DEMAND_END_S}
    if set(evaluation) != expected: errors.append("ramp arrival evaluation intervals are not exactly 120 aligned windows")
    return {"evaluation_total": sum(evaluation.values()) if not errors else 0,
            "terminal_total": sum(value for _, _, value in intervals), "errors": errors}


def classify_screen_run(summary: dict[str, object], pure_mainline_congested_runs: int) -> str:
    if not summary.get("valid"):
        return "invalid"
    online = summary.get("online", {})
    runtime = summary.get("runtime", {})
    duration = float(online.get("congestion_duration_s", 0) or 0)
    if duration < 300:
        return "free_flow"
    if pure_mainline_congested_runs == 0 and runtime.get("ramp_evaluation_throughput", 0) > 0 \
            and float(online.get("ramp_vehicle_max_veh", 0) or 0) <= 80:
        return "control_candidate"
    return "baseline_unstable"


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=SCREEN_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _route_specs(root: Path, output_root: Path) -> list[pilot.RunSpec]:
    specs = build_screen_matrix(root, output_root)
    for spec in specs:
        scenario = calibrated_demand.CalibratedScenario(spec.scenario.name, spec.scenario.mainline_vph,
                                                        spec.scenario.ramp_vph, "mixed-screen")
        calibrated_demand.write_tree(calibrated_demand.build_fixed_tree(root / "osm.rou.xml", scenario), spec.route_path)
    return specs


def _read_occupancy(path: Path) -> float | None:
    try:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            values = [float(row["mainline_occupancy"]) for row in csv.DictReader(handle)
                      if row.get("mainline_occupancy") not in (None, "")]
        return sum(values) / len(values) if values else None
    except (OSError, KeyError, ValueError):
        return None


def _flat(summary: dict[str, object], spec: pilot.RunSpec) -> dict[str, object]:
    trip = summary.get("tripinfo", {})
    system, mainline, ramp = trip.get("system", {}), trip.get("mainline", {}), trip.get("ramp", {})
    online, runtime, logs = summary.get("online", {}), summary.get("runtime", {}), summary.get("log_counts", {})
    return {"mainline_vph": spec.scenario.mainline_vph, "ramp_vph": spec.scenario.ramp_vph,
            "controller": spec.controller, "seed": spec.seed, "valid": summary.get("valid", False),
            "classification": summary.get("classification", "invalid"), "errors": json.dumps(summary.get("errors", [])),
            "congestion_duration_s": online.get("congestion_duration_s", ""), "breakdown_start_s": online.get("breakdown_start_s", ""),
            "online_mainline_speed_mps": online.get("mainline_speed_mps", ""), "online_mainline_occupancy": online.get("mainline_occupancy", ""),
            "system_total_system_time_s": system.get("total_system_time_s", ""), "mainline_total_timeLoss": mainline.get("total_timeLoss", ""),
            "ramp_total_timeLoss": ramp.get("total_timeLoss", ""), "system_total_timeLoss": system.get("total_timeLoss", ""),
            "mainline_evaluation_throughput": runtime.get("evaluation_throughput", ""), "mainline_terminal_throughput": runtime.get("terminal_throughput", ""),
            "ramp_evaluation_arrivals": runtime.get("ramp_evaluation_throughput", ""), "ramp_terminal_arrivals": runtime.get("ramp_terminal_throughput", ""),
            "online_ramp_vehicle_mean_veh": online.get("ramp_vehicle_mean_veh", ""), "online_ramp_vehicle_max_veh": online.get("ramp_vehicle_max_veh", ""),
            "mainline_completion_rate": mainline.get("completion_rate", ""), "ramp_completion_rate": ramp.get("completion_rate", ""),
            "system_completion_rate": system.get("completion_rate", ""), "collision": runtime.get("collision", ""),
            "teleport": runtime.get("teleport", ""), "fatal": logs.get("fatal", ""), "emergency_braking": logs.get("emergency_braking", "")}


def _write_report(path: Path, rows: list[dict[str, object]]) -> None:
    lines = ["# Mixed demand single-seed screen", "", "结果仅用于任务 1 阶段筛选，不能单独证明可控性。", "",
             "| mainline | ramp | valid | congestion(s) | speed(m/s) | ramp eval | ramp terminal | queue max | classification |",
             "|---:|---:|---|---:|---:|---:|---:|---:|---|"]
    for row in rows:
        lines.append(f"| {row['mainline_vph']} | {row['ramp_vph']} | {row['valid']} | {row['congestion_duration_s']} | "
                     f"{row['online_mainline_speed_mps']} | {row['ramp_evaluation_arrivals']} | {row['ramp_terminal_arrivals']} | "
                     f"{row['online_ramp_vehicle_max_veh']} | {row['classification']} |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_screen(root: Path = ROOT, output_root: Path = OUTPUT_ROOT,
               traci_module: object | None = None) -> dict[str, object]:
    preflight = validate_preconditions(root, output_root, SUMO_BINARY)
    if not preflight["valid"]:
        return {**preflight, "formal_sumo_start_count": 0}
    traci_module = traci_module or _import_traci()
    output_root.mkdir(parents=True, exist_ok=False)
    specs = _route_specs(root, output_root)
    pure_path = root / "outputs/stage4d_mainline_multiseed/multiseed_classification.json"
    pure = json.loads(pure_path.read_text(encoding="utf-8"))
    rows: list[dict[str, object]] = []
    summaries: list[dict[str, object]] = []
    for spec in specs:
        summary = pilot.run_one(root, spec, traci_module)
        ramp = parse_ramp_arrivals(spec.output_dir / "native/osm_det_ramp_arrival.xml")
        summary.setdefault("runtime", {}).update({"ramp_evaluation_throughput": ramp["evaluation_total"],
                                                   "ramp_terminal_throughput": ramp["terminal_total"]})
        summary.setdefault("online", {})["mainline_occupancy"] = _read_occupancy(spec.output_dir / "window_metrics.csv")
        if ramp["errors"]:
            summary["valid"] = False
            summary.setdefault("errors", []).extend(ramp["errors"])
        baseline = int(pure.get(str(spec.scenario.mainline_vph), {"congested_runs": 0})["congested_runs"])
        summary["classification"] = classify_screen_run(summary, baseline)
        shutil.copyfile(spec.route_path, spec.output_dir / "fixed_route.rou.xml")
        (spec.output_dir / "run_summary.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
        summaries.append(summary)
        rows.append(_flat(summary, spec))
        _write_csv(output_root / "run_summary.csv", rows)
        if not summary.get("valid"):
            manifest = {"schema": "mixed-demand-screen-v1", "valid": False, "errors": summary.get("errors", []),
                        "formal_sumo_start_count": len(summaries), "planned_sumo_starts": len(specs),
                        "matrix": [{"mainline_vph": s.scenario.mainline_vph, "ramp_vph": s.scenario.ramp_vph,
                                    "controller": s.controller, "seed": s.seed} for s in specs],
                        "run_order": [{"mainline_vph": s.scenario.mainline_vph, "ramp_vph": s.scenario.ramp_vph,
                                       "seed": s.seed, "valid": x.get("valid", False)} for s, x in zip(specs, summaries)]}
            (output_root / "scan_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
            return manifest
    classifications = {f"{spec.scenario.mainline_vph}+{spec.scenario.ramp_vph}": row["classification"]
                       for spec, row in zip(specs, rows)}
    (output_root / "screening_classification.json").write_text(json.dumps(classifications, indent=2), encoding="utf-8")
    _write_report(output_root / "mixed_demand_screen_report.md", rows)
    manifest = {"schema": "mixed-demand-screen-v1", "valid": True, "errors": [],
                "formal_sumo_start_count": len(summaries), "planned_sumo_starts": len(specs),
                "matrix": [{"mainline_vph": s.scenario.mainline_vph, "ramp_vph": s.scenario.ramp_vph,
                            "controller": s.controller, "seed": s.seed} for s in specs],
                "run_order": [{"mainline_vph": s.scenario.mainline_vph, "ramp_vph": s.scenario.ramp_vph,
                               "seed": s.seed, "valid": x.get("valid", False), "classification": x.get("classification")}
                              for s, x in zip(specs, summaries)]}
    (output_root / "scan_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def _import_traci() -> object:
    sumo_home = os.environ.get("SUMO_HOME")
    if sumo_home and str(Path(sumo_home) / "tools") not in sys.path:
        sys.path.insert(0, str(Path(sumo_home) / "tools"))
    import importlib
    return importlib.import_module("traci")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--preflight-only", action="store_true")
    args = parser.parse_args(argv)
    if args.preflight_only:
        result = validate_preconditions()
        print(f"PRECHECK_PASS={result['valid']}")
        print(f"PLANNED_SUMO_STARTS={result['planned_sumo_starts']}")
        print(f"PROTECTION_ERRORS={len(result['protection_errors'])}")
        print(f"FORMAL_OUTPUT_EXISTS={OUTPUT_ROOT.exists()}")
        return 0 if result["valid"] else 2
    result = run_screen()
    print(f"FORMAL_SUMO_START_COUNT={result.get('formal_sumo_start_count', 0)}")
    print(f"VALIDATION_PASS={result.get('valid', False)}")
    print(f"MANIFEST_PATH={OUTPUT_ROOT / 'scan_manifest.json'}")
    return 0 if result.get("valid") else 2


if __name__ == "__main__":
    raise SystemExit(main())
