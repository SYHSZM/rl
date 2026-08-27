"""Deterministic Stage 2 calibrated demand builders and validators."""

from __future__ import annotations

import csv
import hashlib
import json
import random
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ENDPOINT_POLICY = "[0,3600)"
DURATION_S = 3600
MAINLINE_SOURCE = "776458555"
RAMP_SOURCE = "E2"
DESTINATION = "E1"
DEPART_LANE = "best"
DEPART_SPEED = "max"
ARRIVAL_POS = "max"


@dataclass(frozen=True)
class CalibratedScenario:
    name: str
    mainline_vph: int
    ramp_vph: int
    state: str = "calibrated"


DELIVERY_SCENARIOS = (
    CalibratedScenario("400+60", 400, 60, "light_boundary"),
    CalibratedScenario("600+60", 600, 60, "stable_candidate"),
    CalibratedScenario("400+80", 400, 80, "critical_unstable"),
    CalibratedScenario("1000+50", 1000, 50, "critical_unstable"),
    CalibratedScenario("600+90", 600, 90, "oversaturated"),
)

CALIBRATED_SCENARIOS = (
    CalibratedScenario("400+60", 400, 60, "light_boundary"),
    CalibratedScenario("400+70", 400, 70, "stable_candidate"),
    CalibratedScenario("600+60", 600, 60, "stable_candidate"),
    CalibratedScenario("400+80", 400, 80, "critical_unstable"),
    CalibratedScenario("600+70", 600, 70, "critical_unstable"),
    CalibratedScenario("800+50", 800, 50, "critical_unstable"),
    CalibratedScenario("1000+50", 1000, 50, "critical_unstable"),
    CalibratedScenario("600+80", 600, 80, "oversaturated"),
    CalibratedScenario("600+90", 600, 90, "oversaturated"),
)


def _validate_filter_range(name: str, values: tuple[int, int] | None) -> None:
    if values is None:
        return
    minimum, maximum = values
    if minimum < 0 or maximum < 0:
        raise ValueError(f"{name} values must be non-negative")
    if minimum > maximum:
        raise ValueError(f"{name} range minimum must be <= maximum")


def select_scenario(
    *,
    seed: int,
    state: str,
    mainline_range: tuple[int, int] | None = None,
    ramp_range: tuple[int, int] | None = None,
) -> CalibratedScenario:
    states = {scenario.state for scenario in CALIBRATED_SCENARIOS}
    if state not in states:
        raise ValueError(f"invalid state: {state}")
    _validate_filter_range("mainline", mainline_range)
    _validate_filter_range("ramp", ramp_range)
    candidates = [scenario for scenario in CALIBRATED_SCENARIOS if scenario.state == state]
    if mainline_range is not None:
        candidates = [scenario for scenario in candidates if mainline_range[0] <= scenario.mainline_vph <= mainline_range[1]]
    if ramp_range is not None:
        candidates = [scenario for scenario in candidates if ramp_range[0] <= scenario.ramp_vph <= ramp_range[1]]
    if not candidates:
        raise ValueError("no approved scenario matches the requested state and ranges")
    return random.Random(seed).choice(candidates)


def planned_departures(rate_vph: int, duration_s: int = DURATION_S) -> list[float]:
    if rate_vph < 0:
        raise ValueError("rate_vph must be non-negative")
    if duration_s <= 0:
        raise ValueError("duration_s must be positive")
    if rate_vph == 0:
        return []
    total = rate_vph * duration_s
    if total % DURATION_S:
        raise ValueError("rate_vph and duration_s must produce an integral vehicle count")
    count = total // DURATION_S
    interval = duration_s / rate_vph
    return [i * interval for i in range(count)]


def _template_root(template_path: Path) -> ET.Element:
    return ET.parse(template_path).getroot()


def _copy_vtypes(template_root: ET.Element, root: ET.Element) -> None:
    for node in template_root.findall("vType"):
        root.append(ET.fromstring(ET.tostring(node, encoding="unicode")))


def _root(template_path: Path) -> tuple[ET.Element, ET.Element]:
    template = _template_root(template_path)
    root = ET.Element(template.tag, dict(template.attrib))
    _copy_vtypes(template, root)
    return template, root


def _flow_attributes(source: str, rate: int) -> dict[str, str]:
    return {
        "type": "Car",
        "begin": "0",
        "end": "3600",
        "from": source,
        "to": DESTINATION,
        "vehsPerHour": str(rate),
        "departLane": DEPART_LANE,
        "departSpeed": DEPART_SPEED,
        "arrivalPos": ARRIVAL_POS,
    }


def build_flow_tree(template_path: Path, scenario: CalibratedScenario) -> ET.ElementTree:
    _, root = _root(template_path)
    root.append(ET.Element("flow", {"id": "mainline_flow", **_flow_attributes(MAINLINE_SOURCE, scenario.mainline_vph)}))
    root.append(ET.Element("flow", {"id": "ramp_flow", **_flow_attributes(RAMP_SOURCE, scenario.ramp_vph)}))
    return ET.ElementTree(root)


def _format_depart(value: float) -> str:
    return f"{value:.6f}".rstrip("0").rstrip(".") or "0"


def build_fixed_tree(template_path: Path, scenario: CalibratedScenario) -> ET.ElementTree:
    _, root = _root(template_path)
    planned = [
        (depart, 0, "mainline", MAINLINE_SOURCE, scenario.mainline_vph)
        for depart in planned_departures(scenario.mainline_vph)
    ] + [
        (depart, 1, "ramp", RAMP_SOURCE, scenario.ramp_vph)
        for depart in planned_departures(scenario.ramp_vph)
    ]
    planned.sort(key=lambda item: (item[0], item[1]))
    indexes = {"mainline": 0, "ramp": 0}
    for depart, _, source_name, source, _ in planned:
        index = indexes[source_name]
        indexes[source_name] += 1
        root.append(ET.Element("trip", {
            "id": f"{source_name}_{index:06d}",
            "type": "Car",
            "depart": _format_depart(depart),
            "from": source,
            "to": DESTINATION,
            "departLane": DEPART_LANE,
            "departSpeed": DEPART_SPEED,
            "arrivalPos": ARRIVAL_POS,
        }))
    return ET.ElementTree(root)


def build_mainline_fixed_tree(template_path: Path, scenario: CalibratedScenario) -> ET.ElementTree:
    """Build a fixed route containing only the frozen mainline demand."""
    _, root = _root(template_path)
    for index, depart in enumerate(planned_departures(scenario.mainline_vph)):
        root.append(ET.Element("trip", {
            "id": f"mainline_{index:06d}",
            "type": "Car",
            "depart": _format_depart(depart),
            "from": MAINLINE_SOURCE,
            "to": DESTINATION,
            "departLane": DEPART_LANE,
            "departSpeed": DEPART_SPEED,
            "arrivalPos": ARRIVAL_POS,
        }))
    return ET.ElementTree(root)


def write_tree(tree: ET.ElementTree, output_path: Path) -> str:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    ET.indent(tree, space="    ")
    tree.write(output_path, encoding="utf-8", xml_declaration=True)
    return hashlib.sha256(output_path.read_bytes()).hexdigest().upper()


def _attrs_without_id(node: ET.Element) -> dict[str, str]:
    return {key: value for key, value in node.attrib.items() if key != "id"}


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def _failure(errors: list[str], flow_sha256: str | None = None, fixed_sha256: str | None = None) -> dict[str, Any]:
    return {
        "valid": False,
        "errors": errors,
        "mainline_count": 0,
        "ramp_count": 0,
        "endpoint_policy": ENDPOINT_POLICY,
        "flow_sha256": flow_sha256,
        "fixed_sha256": fixed_sha256,
    }


def validate_delivery_pair(flow_path: Path, fixed_path: Path, scenario: CalibratedScenario, template_path: Path) -> dict[str, Any]:
    errors: list[str] = []
    counts = {"mainline": 0, "ramp": 0}
    try:
        flow_root = ET.parse(flow_path).getroot()
        fixed_root = ET.parse(fixed_path).getroot()
        template_root = _template_root(template_path)
    except (ET.ParseError, OSError) as exc:
        return _failure([f"XML parse error: {exc}"])

    template_vtypes = [(node.tag, dict(node.attrib)) for node in template_root.findall("vType")]
    actual_vtypes = [(node.tag, dict(node.attrib)) for node in flow_root.findall("vType")]
    fixed_vtypes = [(node.tag, dict(node.attrib)) for node in fixed_root.findall("vType")]
    if actual_vtypes != template_vtypes or fixed_vtypes != template_vtypes:
        errors.append("vType definitions differ from template")

    expected_flows = [
        {"id": "mainline_flow", **_flow_attributes(MAINLINE_SOURCE, scenario.mainline_vph)},
        {"id": "ramp_flow", **_flow_attributes(RAMP_SOURCE, scenario.ramp_vph)},
    ]
    flow_children = list(flow_root)
    fixed_children = list(fixed_root)
    if any(node.tag not in {"vType", "flow"} for node in flow_children):
        errors.append("flow representation contains unexpected demand elements")
    actual_flows = [node.attrib for node in flow_root.findall("flow")]
    if len(actual_flows) != 2 or len({node.get("id") for node in flow_root.findall("flow")}) != len(actual_flows):
        errors.append("flow representation must contain two unique flows")
    if actual_flows != expected_flows:
        errors.append("flow definitions differ from scenario")
    if any(node.tag not in {"vType", "trip"} for node in fixed_children):
        errors.append("fixed representation contains unexpected demand elements")

    required = {"id", "type", "depart", "from", "to", "departLane", "departSpeed", "arrivalPos"}
    actual_by_source = {"mainline": [], "ramp": []}
    ids: set[str] = set()
    expected_total = scenario.mainline_vph + scenario.ramp_vph
    if len(fixed_root.findall("trip")) != expected_total:
        errors.append("fixed representation trip count differs from scenario")
    for trip in fixed_root.findall("trip"):
        if not required <= set(trip.attrib):
            errors.append(f"trip {trip.attrib.get('id', '<missing>')} lacks required attributes")
            continue
        if trip.attrib["id"] in ids:
            errors.append("fixed vehicle IDs are not unique")
        ids.add(trip.attrib["id"])
        try:
            depart = float(trip.attrib["depart"])
        except ValueError:
            errors.append(f"invalid departure for {trip.attrib['id']}")
            continue
        if not 0.0 <= depart < DURATION_S:
            errors.append(f"departure outside [0,3600) for {trip.attrib['id']}")
        source_name = "mainline" if trip.attrib["from"] == MAINLINE_SOURCE else "ramp" if trip.attrib["from"] == RAMP_SOURCE else None
        if source_name is None:
            errors.append(f"unknown source for {trip.attrib['id']}")
            continue
        expected = {"type": "Car", "to": DESTINATION, "departLane": DEPART_LANE, "departSpeed": DEPART_SPEED, "arrivalPos": ARRIVAL_POS}
        if any(trip.attrib.get(k) != v for k, v in expected.items()):
            errors.append(f"fixed endpoint strategy differs for {trip.attrib['id']}")
        expected_id = f"{source_name}_{len(actual_by_source[source_name]):06d}"
        if trip.attrib["id"] != expected_id:
            errors.append(f"fixed vehicle ID sequence or source prefix differs for {trip.attrib['id']}")
        actual_by_source[source_name].append(depart)

    expected_by_source = {"mainline": planned_departures(scenario.mainline_vph), "ramp": planned_departures(scenario.ramp_vph)}
    for source_name in ("mainline", "ramp"):
        counts[source_name] = len(actual_by_source[source_name])
        if counts[source_name] != len(expected_by_source[source_name]):
            errors.append(f"{source_name} vehicle count differs from scenario")
        if [_format_depart(value) for value in actual_by_source[source_name]] != [_format_depart(value) for value in expected_by_source[source_name]]:
            errors.append(f"{source_name} departure schedule differs from flow departure schedule")

    return {
        "valid": not errors,
        "errors": errors,
        "mainline_count": counts["mainline"],
        "ramp_count": counts["ramp"],
        "endpoint_policy": ENDPOINT_POLICY,
        "flow_sha256": _hash(flow_path),
        "fixed_sha256": _hash(fixed_path),
    }


def _core_manifest(results: list[dict[str, Any]], template_sha256: str) -> dict[str, Any]:
    return {"schema_version": "stage2-calibrated-demand-v1", "template_sha256": template_sha256, "scenarios": results}


def build_delivery(output_dir: Path, template_path: Path) -> dict[str, Any]:
    output_dir = Path(output_dir)
    flow_dir, fixed_dir = output_dir / "flow", output_dir / "fixed"
    flow_dir.mkdir(parents=True, exist_ok=True)
    fixed_dir.mkdir(parents=True, exist_ok=True)
    expected_names = {f"{scenario.name}.rou.xml" for scenario in DELIVERY_SCENARIOS}
    unknown_files = [
        f"flow/{path.name}" for path in flow_dir.glob("*.rou.xml") if path.name not in expected_names
    ] + [
        f"fixed/{path.name}" for path in fixed_dir.glob("*.rou.xml") if path.name not in expected_names
    ]
    results: list[dict[str, Any]] = []
    for scenario in DELIVERY_SCENARIOS:
        flow_path = flow_dir / f"{scenario.name}.rou.xml"
        fixed_path = fixed_dir / f"{scenario.name}.rou.xml"
        write_tree(build_flow_tree(template_path, scenario), flow_path)
        write_tree(build_fixed_tree(template_path, scenario), fixed_path)
        validation = validate_delivery_pair(flow_path, fixed_path, scenario, template_path)
        results.append({
            "scenario": scenario.name,
            "state": scenario.state,
            "mainline_vph": scenario.mainline_vph,
            "ramp_vph": scenario.ramp_vph,
            "duration_s": DURATION_S,
            "endpoint_policy": ENDPOINT_POLICY,
            "flow_file": f"flow/{flow_path.name}",
            "fixed_file": f"fixed/{fixed_path.name}",
            "flow_sha256": validation["flow_sha256"],
            "fixed_sha256": validation["fixed_sha256"],
            "mainline_vehicle_count": validation["mainline_count"],
            "ramp_vehicle_count": validation["ramp_count"],
            "validation_status": "PASS" if validation["valid"] else "FAIL",
            "errors": validation["errors"],
        })
    template_sha256 = _hash(template_path)
    manifest_fields = [
        "scenario", "state", "mainline_vph", "ramp_vph", "duration_s", "endpoint_policy",
        "flow_file", "fixed_file", "flow_sha256", "fixed_sha256", "mainline_vehicle_count",
        "ramp_vehicle_count", "validation_status",
    ]
    with (output_dir / "scenario_manifest.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=manifest_fields)
        writer.writeheader()
        writer.writerows([{field: result[field] for field in manifest_fields} for result in results])
    core = _core_manifest(results, template_sha256)
    core["unknown_files"] = sorted(unknown_files)
    core_bytes = json.dumps(core, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    report = {
        "schema_version": core["schema_version"],
        "template_sha256": template_sha256,
        "generation_timestamp": datetime.now(timezone.utc).isoformat(),
        "valid": not unknown_files and all(result["validation_status"] == "PASS" for result in results),
        "scenarios": results,
        "unknown_files": sorted(unknown_files),
        "core_sha256": hashlib.sha256(core_bytes).hexdigest().upper(),
    }
    (output_dir / "validation_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report
