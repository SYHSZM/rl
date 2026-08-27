from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
import importlib
from pathlib import Path
import xml.etree.ElementTree as ET

from analyze_freeflow_reference import check_protection_manifest
from controllers import AlineaController
from experiment_config import default_config
from osm_control_adapter import (
    OSM_CONTROL_PROFILE,
    aggregate_mainline_occupancy,
    apply_ramp_signal,
    audit_osm_network,
    read_ramp_queue,
)


ROOT = Path(__file__).resolve().parent
FORMAL_OUTPUT = ROOT / "outputs/stage4b_smoke/main600_ramp60_alinea_seed1"
SUMO_BINARY = Path(r"D:\sumo-1.25.0\bin\sumo.exe")
EXPECTED_NET_SHA256 = "856D22EC0E5D7FD13021557EBEBD2A3CD3BABD03385A208A2E801D3265B7FD99"
EXPECTED_DEMAND_SHA256 = "ECF07999D44C8EA4B2FB5FEED908066FF82088602DE6F59A5ABCF96EDEB250EB"
EXPECTED_DETECTOR_SHA256 = "52844C6A95E17F2C10F44E52B3CED9ABE5A0C3FEC8ED13CE4674A3EC7B01362D"
EXPECTED_AUDIT_CORE_SHA256 = "8B48B582DF4214B364F87DF6F672FB229BB6CD98DD58E3925A5C5C244B242D07"
STEPS = 600
SEED = 1

FROZEN_DETECTOR_IDS = (
    "osm_det_main_down_1",
    "osm_det_main_down_2",
    "osm_det_ramp_queue",
    "osm_det_ramp_arrival",
)

SIGNAL_FIELDS = [
    "time_s", "commanded_state", "observed_state",
    "requested_rate_vph", "applied_rate_vph", "green_s", "red_s",
]
DETECTOR_FIELDS = [
    "time_s", "main_down_1_occupancy_pct", "main_down_2_occupancy_pct",
    "occupancy", "main_down_1_vehicle_count", "main_down_2_vehicle_count",
    "ramp_arrival_vehicle_count", "departed", "arrived", "teleport", "collision",
]
QUEUE_FIELDS = ["time_s", "ramp_vehicle_count", "ramp_halting_count"]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def _validate_frozen_hashes(root: Path) -> list[str]:
    expected = {
        root / "osm.net.xml": ("network", EXPECTED_NET_SHA256),
        root / "demand_delivery/fixed/600+60.rou.xml": ("demand", EXPECTED_DEMAND_SHA256),
        root / "control_adapter/osm_control.add.xml": ("detector", EXPECTED_DETECTOR_SHA256),
    }
    errors = []
    for path, (label, digest) in expected.items():
        if not path.is_file():
            errors.append(f"{label} file missing: {path}")
        elif sha256(path) != digest:
            errors.append(f"{label} SHA-256 mismatch: {path}")
    return errors


def validate_preconditions(root: Path, output_dir: Path, sumo_binary: Path) -> dict[str, object]:
    errors = _validate_frozen_hashes(root)
    protection_errors = check_protection_manifest(root)
    audit_path = root / "control_adapter/control_adapter_audit.json"
    audit = {}
    if not audit_path.is_file():
        errors.append(f"Stage 4A audit missing: {audit_path}")
    else:
        try:
            audit = json.loads(audit_path.read_text(encoding="utf-8"))
            if audit.get("valid") is not True:
                errors.append("Stage 4A audit is not valid")
            if any(value != "PASS" for value in audit.get("checks", {}).values()) or len(audit.get("checks", {})) != 7:
                errors.append("Stage 4A audit checks are not all PASS")
            if audit.get("core_sha256") != EXPECTED_AUDIT_CORE_SHA256:
                errors.append("Stage 4A audit core SHA-256 mismatch")
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"Stage 4A audit parse failed: {exc}")
    network_audit = audit_osm_network(root / "osm.net.xml")
    if not network_audit["valid"]:
        errors.extend(f"network audit: {error}" for error in network_audit["errors"])
    if protection_errors:
        errors.extend(f"protection: {error}" for error in protection_errors)
    if not sumo_binary.is_file():
        errors.append(f"SUMO binary missing: {sumo_binary}")
    if "gui" in sumo_binary.name.lower():
        errors.append("SUMO-GUI is not permitted")
    if output_dir.exists() and any(output_dir.iterdir()):
        errors.append(f"output directory is nonempty: {output_dir}")
    hashes = {}
    for path in (
        root / "osm.net.xml",
        root / "demand_delivery/fixed/600+60.rou.xml",
        root / "control_adapter/osm_control.add.xml",
    ):
        if path.is_file():
            hashes[path.name] = sha256(path)
    return {
        "valid": not errors,
        "errors": errors,
        "protection_errors": protection_errors,
        "audit": audit,
        "hashes": hashes,
    }


def write_runtime_detector_file(source_path: Path, output_path: Path, native_dir: Path) -> str:
    root = ET.parse(source_path).getroot()
    elements = list(root)
    ids = [element.attrib.get("id") for element in elements]
    if len(elements) != len(FROZEN_DETECTOR_IDS) or set(ids) != set(FROZEN_DETECTOR_IDS) or len(set(ids)) != len(ids):
        raise ValueError("runtime detector file must contain exactly the four frozen detector IDs")
    native_dir = native_dir.resolve()
    native_dir.mkdir(parents=True, exist_ok=True)
    for element in elements:
        target = (native_dir / f"{element.attrib['id']}.xml").resolve()
        if target.parent != native_dir:
            raise ValueError("native detector output escaped native directory")
        element.set("file", str(target))
    ET.indent(root, space="    ")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(root).write(output_path, encoding="utf-8", xml_declaration=True)
    return sha256(output_path)


def build_sumo_command(root: Path, output_dir: Path, runtime_additional: Path, sumo_binary: Path) -> list[str]:
    output_dir = output_dir.resolve()
    return [
        str(sumo_binary.resolve()),
        "--net-file", str((root / "osm.net.xml").resolve()),
        "--route-files", str((root / "demand_delivery/fixed/600+60.rou.xml").resolve()),
        "--additional-files", str(runtime_additional.resolve()),
        "--begin", "0",
        "--end", "600",
        "--step-length", "1.0",
        "--seed", "1",
        "--time-to-teleport", "-1",
        "--max-depart-delay", "1800",
        "--collision.action", "warn",
        "--tripinfo-output", str((output_dir / "tripinfo.xml").resolve()),
        "--log", str((output_dir / "sumo.log").resolve()),
        "--error-log", str((output_dir / "sumo_error.log").resolve()),
        "--no-step-log", "true",
        "--duration-log.statistics", "true",
    ]


def perform_tls_roundtrip(conn: object) -> dict[str, object]:
    observed = []
    for state in (OSM_CONTROL_PROFILE.red_state, OSM_CONTROL_PROFILE.green_state):
        conn.trafficlight.setRedYellowGreenState(OSM_CONTROL_PROFILE.tls_id, state)
        observed.append(conn.trafficlight.getRedYellowGreenState(OSM_CONTROL_PROFILE.tls_id))
    return {"valid": observed == ["rr", "GG"], "commanded": ["rr", "GG"], "observed": observed}


def execute_control_loop(conn: object) -> dict[str, object]:
    controller = AlineaController(default_config().alinea)
    action = None
    signal_rows = []
    detector_rows = []
    queue_rows = []
    controller_update_times = []
    state_mismatches = 0
    mainline_departed_ids = set()
    ramp_departed_ids = set()
    departed = 0
    arrived = 0
    teleport = 0
    collision = 0
    detector_read_counts = {detector_id: 0 for detector_id in FROZEN_DETECTOR_IDS}

    for time_s in range(STEPS):
        if time_s % default_config().alinea.cycle_s == 0:
            occupancy = aggregate_mainline_occupancy(conn)
            queue_veh, _ = read_ramp_queue(conn)
            action = controller.update(occupancy, queue_veh)
            controller_update_times.append(time_s)
        commanded = apply_ramp_signal(conn, action, time_s, default_config().alinea.cycle_s)
        observed = conn.trafficlight.getRedYellowGreenState(OSM_CONTROL_PROFILE.tls_id)
        if commanded != observed:
            state_mismatches += 1
        conn.simulationStep()

        raw_occupancies = [
            conn.inductionloop.getLastStepOccupancy(detector_id)
            for detector_id in OSM_CONTROL_PROFILE.mainline_detector_ids
        ]
        mainline_counts = [
            conn.inductionloop.getLastStepVehicleNumber(detector_id)
            for detector_id in OSM_CONTROL_PROFILE.mainline_detector_ids
        ]
        ramp_arrival_count = conn.inductionloop.getLastStepVehicleNumber(OSM_CONTROL_PROFILE.ramp_arrival_detector_id)
        ramp_vehicle_count, ramp_halting_count = read_ramp_queue(conn)
        for detector_id in OSM_CONTROL_PROFILE.mainline_detector_ids:
            detector_read_counts[detector_id] += 1
        detector_read_counts[OSM_CONTROL_PROFILE.ramp_arrival_detector_id] += 1
        detector_read_counts[OSM_CONTROL_PROFILE.ramp_queue_detector_id] += 1

        departed_ids = conn.simulation.getDepartedIDList()
        arrived_ids = conn.simulation.getArrivedIDList()
        mainline_departed_ids.update(item for item in departed_ids if item.startswith("mainline_"))
        ramp_departed_ids.update(item for item in departed_ids if item.startswith("ramp_"))
        departed += len(departed_ids)
        arrived += len(arrived_ids)
        step_teleport = conn.simulation.getStartingTeleportNumber()
        step_collision = conn.simulation.getCollidingVehiclesNumber()
        teleport += step_teleport
        collision += step_collision

        signal_rows.append({
            "time_s": time_s,
            "commanded_state": commanded,
            "observed_state": observed,
            "requested_rate_vph": action.requested_rate_vph,
            "applied_rate_vph": action.applied_rate_vph,
            "green_s": action.green_s,
            "red_s": action.red_s,
        })
        detector_rows.append({
            "time_s": time_s,
            "main_down_1_occupancy_pct": raw_occupancies[0],
            "main_down_2_occupancy_pct": raw_occupancies[1],
            "occupancy": (raw_occupancies[0] + raw_occupancies[1]) / 200.0,
            "main_down_1_vehicle_count": mainline_counts[0],
            "main_down_2_vehicle_count": mainline_counts[1],
            "ramp_arrival_vehicle_count": ramp_arrival_count,
            "departed": departed,
            "arrived": arrived,
            "teleport": teleport,
            "collision": collision,
        })
        queue_rows.append({
            "time_s": time_s,
            "ramp_vehicle_count": ramp_vehicle_count,
            "ramp_halting_count": ramp_halting_count,
        })

    return {
        "signal_rows": signal_rows,
        "detector_rows": detector_rows,
        "queue_rows": queue_rows,
        "controller_update_times": controller_update_times,
        "state_mismatches": state_mismatches,
        "mainline_departed": len(mainline_departed_ids),
        "ramp_departed": len(ramp_departed_ids),
        "arrived": arrived,
        "teleport": teleport,
        "collision": collision,
        "steps": STEPS,
        "detector_read_counts": detector_read_counts,
    }


def write_csv(path: Path, rows: list[dict[str, object]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def scan_logs(output_dir: Path) -> dict[str, int]:
    counts = {"fatal": 0, "emergency_braking": 0, "warning": 0}
    for name in ("sumo.log", "sumo_error.log"):
        path = output_dir / name
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        counts["fatal"] += len(re.findall(r"fatal", text, flags=re.IGNORECASE))
        counts["emergency_braking"] += len(re.findall(r"emergency\s+braking", text, flags=re.IGNORECASE))
        counts["warning"] += len(re.findall(r"(?im)^\s*warning\b", text))
    return counts


def collect_output_hashes(output_dir: Path) -> dict[str, str]:
    files = sorted(path for path in output_dir.rglob("*") if path.is_file() and path.name != "run_manifest.json")
    return {path.relative_to(output_dir).as_posix(): sha256(path) for path in files}


def _parse_csv(path: Path, fields: list[str]) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != fields:
            raise ValueError(f"{path.name} fields differ from fixed schema")
        return list(reader)


def validate_run_outputs(output_dir: Path, runtime: dict[str, object], log_counts: dict[str, int], root: Path = ROOT) -> list[str]:
    errors: list[str] = []
    required = (
        "runtime_osm_control.add.xml", "tripinfo.xml", "sumo.log", "sumo_error.log",
        "signal_timeline.csv", "detector_timeline.csv", "ramp_queue_timeline.csv",
    )
    for name in required:
        if not (output_dir / name).is_file():
            errors.append(f"missing required output: {name}")
    native = output_dir / "native"
    expected_native = {f"{detector_id}.xml" for detector_id in FROZEN_DETECTOR_IDS}
    actual_native = {path.name for path in native.glob("*.xml")} if native.is_dir() else set()
    if actual_native != expected_native:
        errors.append("native detector filenames differ from frozen detector IDs")
    for path in [output_dir / "tripinfo.xml", output_dir / "runtime_osm_control.add.xml"] + sorted(native.glob("*.xml")):
        if path.is_file():
            try:
                ET.parse(path)
            except ET.ParseError as exc:
                errors.append(f"parse failed for {path.name}: {exc}")
    rows_by_name = {}
    for name, fields in (
        ("signal_timeline.csv", SIGNAL_FIELDS),
        ("detector_timeline.csv", DETECTOR_FIELDS),
        ("ramp_queue_timeline.csv", QUEUE_FIELDS),
    ):
        path = output_dir / name
        if path.is_file():
            try:
                rows_by_name[name] = _parse_csv(path, fields)
                times = [int(row["time_s"]) for row in rows_by_name[name]]
                if times != list(range(STEPS)):
                    errors.append(f"{name} time axis is not 0..599")
            except (OSError, ValueError) as exc:
                errors.append(f"parse failed for {name}: {exc}")
    if any(len(rows_by_name.get(name, [])) != STEPS for name in ("signal_timeline.csv", "detector_timeline.csv", "ramp_queue_timeline.csv")):
        errors.append("CSV data rows are not exactly 600")
    if runtime.get("steps") != STEPS:
        errors.append("runtime steps are not 600")
    if runtime.get("state_mismatches", 0) != 0:
        errors.append(f"state mismatch count is {runtime.get('state_mismatches')}")
    detector_rows = runtime.get("detector_rows", rows_by_name.get("detector_timeline.csv", []))
    if any(not 0.0 <= float(row["occupancy"]) <= 1.0 for row in detector_rows):
        errors.append("occupancy is outside [0,1]")
    if runtime.get("mainline_departed", 0) <= 0:
        errors.append("mainline departed count is not positive")
    if runtime.get("ramp_departed", 0) <= 0:
        errors.append("ramp departed count is not positive")
    if runtime.get("arrived", 0) <= 0:
        errors.append("arrived count is not positive")
    if runtime.get("collision", 0) != 0:
        errors.append("collision count is not zero")
    if runtime.get("teleport", 0) != 0:
        errors.append("teleport count is not zero")
    detector_counts = runtime.get("detector_read_counts", {})
    for detector_id in FROZEN_DETECTOR_IDS:
        if detector_counts.get(detector_id) != STEPS:
            errors.append(f"detector read count is not 600: {detector_id}")
    if log_counts.get("fatal", 0) != 0:
        errors.append("fatal log count is not zero")
    if log_counts.get("emergency_braking", 0) != 0:
        errors.append("emergency braking log count is not zero")
    errors.extend(_validate_frozen_hashes(root))
    errors.extend(f"protection: {error}" for error in check_protection_manifest(root))
    return errors


def _import_traci():
    sumo_home = os.environ.get("SUMO_HOME")
    if sumo_home:
        tools_path = str(Path(sumo_home) / "tools")
        if tools_path not in sys.path:
            sys.path.append(tools_path)
    return importlib.import_module("traci")


def _sumo_version_text(sumo_binary: Path) -> str:
    completed = subprocess.run([str(sumo_binary), "--version"], capture_output=True, text=True, check=False)
    return (completed.stdout or "") + (completed.stderr or "")


def _base_manifest(preflight: dict[str, object], environment: dict[str, object] | None = None) -> dict[str, object]:
    now = datetime.now(timezone.utc).isoformat()
    return {
        "schema_version": "stage4b-osm-sumo-smoke-v1",
        "scenario": "600+60",
        "seed": SEED,
        "controller": "alinea",
        "begin_s": 0,
        "end_s": STEPS,
        "step_length_s": 1.0,
        "valid": False,
        "errors": list(preflight.get("errors", [])),
        "failure_stage": "preflight",
        "environment": environment or {},
        "input_sha256": preflight.get("hashes", {}),
        "j5_roundtrip": {},
        "runtime": {},
        "log_counts": {},
        "detector_read_counts": {},
        "output_sha256": {},
        "protection_errors_before": list(preflight.get("protection_errors", [])),
        "protection_errors_after": [],
        "started_at": now,
        "finished_at": now,
    }


def _write_manifest(output_dir: Path, manifest: dict[str, object]) -> None:
    (output_dir / "run_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run_smoke(root: Path = ROOT, output_dir: Path = FORMAL_OUTPUT, sumo_binary: Path = SUMO_BINARY) -> dict[str, object]:
    preflight = validate_preconditions(root, output_dir, sumo_binary)
    if not preflight["valid"]:
        return _base_manifest(preflight)
    output_dir.mkdir(parents=True, exist_ok=True)
    native_dir = output_dir / "native"
    manifest = _base_manifest(preflight)
    manifest["started_at"] = datetime.now(timezone.utc).isoformat()
    connection = None
    runtime = {}
    failure_stage = "runtime_detector"
    try:
        runtime_additional = output_dir / "runtime_osm_control.add.xml"
        write_runtime_detector_file(root / "control_adapter/osm_control.add.xml", runtime_additional, native_dir)
        failure_stage = "traci_start"
        traci = _import_traci()
        manifest["environment"] = {
            "python": sys.version,
            "sumo_binary": str(sumo_binary.resolve()),
            "sumo_version": _sumo_version_text(sumo_binary),
            "traci_module": str(Path(traci.__file__).resolve()),
            "traci_protocol_version": getattr(traci.constants, "TRACI_VERSION", None),
            "SUMO_HOME": os.environ.get("SUMO_HOME"),
        }
        command = build_sumo_command(root, output_dir, runtime_additional, sumo_binary)
        traci.start(command, label="stage4b_osm_smoke")
        connection = traci.getConnection("stage4b_osm_smoke")
        failure_stage = "j5_roundtrip"
        roundtrip = perform_tls_roundtrip(connection)
        manifest["j5_roundtrip"] = roundtrip
        if not roundtrip["valid"]:
            raise RuntimeError(f"J5 round trip failed: {roundtrip['observed']}")
        failure_stage = "run_loop"
        runtime = execute_control_loop(connection)
        manifest["runtime"] = {key: value for key, value in runtime.items() if key not in {"signal_rows", "detector_rows", "queue_rows"}}
    except Exception as exc:
        manifest["failure_stage"] = failure_stage
        manifest["errors"].append(f"{type(exc).__name__}: {exc}")
    finally:
        if connection is not None:
            try:
                connection.close()
                manifest["runtime"]["close_success"] = True
            except Exception as exc:
                manifest["runtime"]["close_success"] = False
                manifest["errors"].append(f"close failed: {type(exc).__name__}: {exc}")

    if manifest["failure_stage"] == "preflight":
        manifest["failure_stage"] = failure_stage
    if not manifest["errors"] and runtime:
        manifest["failure_stage"] = "completed"
        write_csv(output_dir / "signal_timeline.csv", runtime["signal_rows"], SIGNAL_FIELDS)
        write_csv(output_dir / "detector_timeline.csv", runtime["detector_rows"], DETECTOR_FIELDS)
        write_csv(output_dir / "ramp_queue_timeline.csv", runtime["queue_rows"], QUEUE_FIELDS)
        manifest["protection_errors_after"] = check_protection_manifest(root)
        manifest["log_counts"] = scan_logs(output_dir)
        manifest["detector_read_counts"] = runtime["detector_read_counts"]
        manifest["errors"].extend(validate_run_outputs(output_dir, runtime, manifest["log_counts"], root))
    else:
        manifest["protection_errors_after"] = check_protection_manifest(root)
        manifest["log_counts"] = scan_logs(output_dir)
        manifest["detector_read_counts"] = runtime.get("detector_read_counts", {}) if runtime else {}
    manifest["output_sha256"] = collect_output_hashes(output_dir)
    manifest["valid"] = not manifest["errors"]
    manifest["finished_at"] = datetime.now(timezone.utc).isoformat()
    _write_manifest(output_dir, manifest)
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the fixed Stage 4B OSM SUMO smoke test")
    parser.add_argument("--preflight-only", action="store_true")
    args = parser.parse_args(argv)
    if args.preflight_only:
        result = validate_preconditions(ROOT, FORMAL_OUTPUT, SUMO_BINARY)
        if result["valid"]:
            try:
                traci = _import_traci()
                _sumo_version_text(SUMO_BINARY)
                if getattr(traci.constants, "TRACI_VERSION", None) != 22:
                    result["errors"].append("unexpected TraCI protocol version")
            except Exception as exc:
                result["errors"].append(f"TraCI preflight failed: {type(exc).__name__}: {exc}")
            result["valid"] = not result["errors"]
        print(f"PRECHECK_PASS={result['valid']}")
        for error in result["errors"]:
            print(f"ERROR={error}")
        return 0 if result["valid"] else 2
    result = run_smoke()
    print(f"MANIFEST_PATH={FORMAL_OUTPUT.resolve() / 'run_manifest.json'}")
    print(f"VALIDATION_PASS={result['valid']}")
    for error in result["errors"]:
        print(f"ERROR={error}")
    return 0 if result["valid"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
