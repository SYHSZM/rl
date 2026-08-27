from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
import xml.etree.ElementTree as ET

from calibrated_demand import (
    DURATION_S,
    ENDPOINT_POLICY,
    build_fixed_tree,
    build_flow_tree,
    select_scenario,
    validate_delivery_pair,
    write_tree,
)
from experiment_config import DemandPoint, ExperimentConfig, default_config, validate_config


def generate_rou_file(
    output_path: str | Path = "merge.rou.xml",
    demand: DemandPoint | None = None,
    seed: int = 0,
    config: ExperimentConfig | None = None,
) -> dict[str, object]:
    config = config or default_config()
    validate_config(config)
    demand = demand or DemandPoint(mainline_vph=3000, ramp_vph=300)

    routes = ET.Element(
        "routes",
        {
            "xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance",
            "xsi:noNamespaceSchemaLocation": "http://sumo.dlr.de/xsd/routes_file.xsd",
        },
    )
    ET.SubElement(
        routes,
        "vType",
        {
            "id": "Car",
            "vClass": "passenger",
            "carFollowModel": "IDM",
            "accel": "1.5",
            "decel": "4.5",
            "emergencyDecel": "9.0",
            "tau": "1.4",
            "minGap": "2.5",
            "sigma": "0.2",
            "speedDev": "0.1",
            "maxSpeed": "33.33",
            "color": "blue",
        },
    )
    ET.SubElement(routes, "route", {"id": "main_route", "edges": "main_0 main_1 main_2 main_3 main_4 main_5"})
    ET.SubElement(routes, "route", {"id": "ramp_route", "edges": "ramp_0 main_4 main_5"})

    for index, phase in enumerate(config.demand_phases):
        _add_flow(routes, f"main_{index}", "main_route", phase.begin_s, phase.end_s, demand.mainline_vph * phase.multiplier)
        _add_flow(routes, f"ramp_{index}", "ramp_route", phase.begin_s, phase.end_s, demand.ramp_vph * phase.multiplier)

    tree = ET.ElementTree(routes)
    ET.indent(tree, space="    ")
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tree.write(output_path, encoding="utf-8", xml_declaration=True)

    return {
        "mainline_vph": demand.mainline_vph,
        "ramp_vph": demand.ramp_vph,
        "seed": seed,
        "output_path": str(output_path),
        "phase_count": len(config.demand_phases),
    }


def _add_flow(root: ET.Element, flow_id: str, route_id: str, begin_s: int, end_s: int, vehs_per_hour: float) -> None:
    ET.SubElement(
        root,
        "flow",
        {
            "id": flow_id,
            "type": "Car",
            "route": route_id,
            "begin": str(begin_s),
            "end": str(end_s),
            "vehsPerHour": f"{vehs_per_hour:.1f}",
            "departLane": "best",
            "departSpeed": "max",
            "arrivalPos": "max",
        },
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def _range_value(name: str, minimum: int | None, maximum: int | None) -> tuple[int, int] | None:
    if minimum is None and maximum is None:
        return None
    if minimum is None or maximum is None:
        raise ValueError(f"{name} range requires both min and max")
    return minimum, maximum


def generate_calibrated_routes(
    output_dir: str | Path,
    *,
    seed: int,
    state: str,
    mainline_range: tuple[int, int] | None = None,
    ramp_range: tuple[int, int] | None = None,
    output_format: str = "both",
    template_path: str | Path = "osm.rou.xml",
) -> dict[str, object]:
    if output_format not in {"flow", "fixed", "both"}:
        raise ValueError("format must be one of: flow, fixed, both")
    scenario = select_scenario(
        seed=seed,
        state=state,
        mainline_range=mainline_range,
        ramp_range=ramp_range,
    )
    output_dir = Path(output_dir)
    template_path = Path(template_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = f"main{scenario.mainline_vph}_ramp{scenario.ramp_vph}_seed{seed}"
    flow_path = output_dir / f"{stem}_flow.rou.xml"
    fixed_path = output_dir / f"{stem}_fixed.rou.xml"
    metadata_path = output_dir / f"{stem}_metadata.json"
    template_resolved = template_path.resolve()
    if any(path.resolve() == template_resolved for path in (flow_path, fixed_path, metadata_path)):
        raise ValueError("generated output must not overwrite the template")

    flow_tree = build_flow_tree(template_path, scenario)
    fixed_tree = build_fixed_tree(template_path, scenario)
    selected_flow_path = flow_path if output_format in {"flow", "both"} else None
    selected_fixed_path = fixed_path if output_format in {"fixed", "both"} else None
    if selected_flow_path is not None:
        write_tree(flow_tree, selected_flow_path)
    if selected_fixed_path is not None:
        write_tree(fixed_tree, selected_fixed_path)

    if output_format == "both":
        validation = validate_delivery_pair(flow_path, fixed_path, scenario, template_path)
    else:
        with tempfile.TemporaryDirectory() as temp:
            validation_flow = Path(temp) / "flow.rou.xml"
            validation_fixed = Path(temp) / "fixed.rou.xml"
            write_tree(build_flow_tree(template_path, scenario), validation_flow)
            write_tree(build_fixed_tree(template_path, scenario), validation_fixed)
            validation = validate_delivery_pair(validation_flow, validation_fixed, scenario, template_path)
    flow_sha256 = _sha256(flow_path) if selected_flow_path is not None else None
    fixed_sha256 = _sha256(fixed_path) if selected_fixed_path is not None else None
    validation["flow_sha256"] = flow_sha256
    validation["fixed_sha256"] = fixed_sha256
    errors = list(validation["errors"])
    core = {
        "schema_version": "stage3-calibrated-demand-v1",
        "seed": seed,
        "state": state,
        "mainline_range": list(mainline_range) if mainline_range is not None else None,
        "ramp_range": list(ramp_range) if ramp_range is not None else None,
        "selected_scenario": scenario.name,
        "mainline_vph": scenario.mainline_vph,
        "ramp_vph": scenario.ramp_vph,
        "duration_s": DURATION_S,
        "endpoint_policy": ENDPOINT_POLICY,
        "output_format": output_format,
        "flow_file": flow_path.name if selected_flow_path is not None else None,
        "fixed_file": fixed_path.name if selected_fixed_path is not None else None,
        "flow_sha256": flow_sha256,
        "fixed_sha256": fixed_sha256,
        "validation_status": "PASS" if validation["valid"] else "FAIL",
        "errors": errors,
    }
    core_bytes = json.dumps(core, sort_keys=True, separators=(",", ":")).encode("utf-8")
    metadata = {
        **core,
        "flow_path": str(flow_path) if selected_flow_path is not None else None,
        "fixed_path": str(fixed_path) if selected_fixed_path is not None else None,
        "metadata_path": str(metadata_path),
        "validation": validation,
        "generation_timestamp": datetime.now(timezone.utc).isoformat(),
        "core_sha256": hashlib.sha256(core_bytes).hexdigest().upper(),
    }
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return metadata


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        info = generate_rou_file()
        print("Generated:", info["output_path"])
        print("mainline_vph:", info["mainline_vph"])
        print("ramp_vph:", info["ramp_vph"])
        print("seed:", info["seed"])
        return 0

    parser = argparse.ArgumentParser(description="Generate legacy or calibrated route demand.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    calibrated = subparsers.add_parser("calibrated", help="generate an approved calibrated scenario")
    calibrated.add_argument("--output-dir", type=Path, required=True)
    calibrated.add_argument("--seed", type=int, required=True)
    calibrated.add_argument("--state", required=True)
    calibrated.add_argument("--main-min", type=int)
    calibrated.add_argument("--main-max", type=int)
    calibrated.add_argument("--ramp-min", type=int)
    calibrated.add_argument("--ramp-max", type=int)
    calibrated.add_argument("--format", choices=("flow", "fixed", "both"), default="both", dest="output_format")
    calibrated.add_argument("--template", type=Path, default=Path("osm.rou.xml"))
    args = parser.parse_args(argv)
    try:
        mainline_range = _range_value("mainline", args.main_min, args.main_max)
        ramp_range = _range_value("ramp", args.ramp_min, args.ramp_max)
        info = generate_calibrated_routes(
            args.output_dir,
            seed=args.seed,
            state=args.state,
            mainline_range=mainline_range,
            ramp_range=ramp_range,
            output_format=args.output_format,
            template_path=args.template,
        )
    except ValueError as exc:
        parser.error(str(exc))
    print("selected_scenario:", info["selected_scenario"])
    print("mainline_vph:", info["mainline_vph"])
    print("ramp_vph:", info["ramp_vph"])
    print("seed:", info["seed"])
    print("flow_path:", info["flow_path"])
    print("fixed_path:", info["fixed_path"])
    print("metadata_path:", info["metadata_path"])
    print("VALIDATION_PASS=True" if info["validation_status"] == "PASS" else "VALIDATION_PASS=False")
    return 0 if info["validation_status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
