from __future__ import annotations

from pathlib import Path
import xml.etree.ElementTree as ET

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


if __name__ == "__main__":
    info = generate_rou_file()
    print("Generated:", info["output_path"])
    print("mainline_vph:", info["mainline_vph"])
    print("ramp_vph:", info["ramp_vph"])
    print("seed:", info["seed"])
