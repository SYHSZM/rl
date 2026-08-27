from __future__ import annotations

import hashlib
import json
from collections import defaultdict, deque
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
import xml.etree.ElementTree as ET


@dataclass(frozen=True)
class OsmControlProfile:
    tls_id: str
    controlled_link_indices: tuple[int, ...]
    controlled_from_edge: str
    controlled_to_edge: str
    green_state: str
    red_state: str
    ramp_queue_lane_id: str
    ramp_merge_edge: str
    mainline_merge_edge: str
    downstream_edge: str
    downstream_lane_ids: tuple[str, ...]
    mainline_detector_ids: tuple[str, ...]
    ramp_queue_detector_id: str
    ramp_arrival_detector_id: str


OSM_CONTROL_PROFILE = OsmControlProfile(
    tls_id="J5",
    controlled_link_indices=(0, 1),
    controlled_from_edge="712593561#2",
    controlled_to_edge="712593561#2.441",
    green_state="GG",
    red_state="rr",
    ramp_queue_lane_id="712593561#2_1",
    ramp_merge_edge="712593561#2.441",
    mainline_merge_edge="712595827",
    downstream_edge="1060166442#2",
    downstream_lane_ids=("1060166442#2_1", "1060166442#2_2"),
    mainline_detector_ids=("osm_det_main_down_1", "osm_det_main_down_2"),
    ramp_queue_detector_id="osm_det_ramp_queue",
    ramp_arrival_detector_id="osm_det_ramp_arrival",
)


_CHECK_NAMES = (
    "tls_junction",
    "tls_logic",
    "controlled_connections",
    "passenger_lanes",
    "merge_topology",
    "ramp_path_uses_control",
    "mainline_path_avoids_controlled_entry",
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def _profile_dict(profile: OsmControlProfile) -> dict[str, object]:
    return asdict(profile)


def _reachable(adjacency, start, target, controlled_transition, require_control):
    queue = deque([(start, False)])
    visited = set(queue)
    while queue:
        edge, used_control = queue.popleft()
        if edge == target and used_control == require_control:
            return True
        for next_edge, is_control in adjacency.get(edge, ()):
            next_state = used_control or is_control
            state = (next_edge, next_state)
            if state not in visited:
                visited.add(state)
                queue.append(state)
    return False


def audit_osm_network(net_path: str | Path, profile: OsmControlProfile = OSM_CONTROL_PROFILE) -> dict[str, object]:
    path = Path(net_path)
    errors: list[str] = []
    checks = {name: "FAIL" for name in _CHECK_NAMES}
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError) as exc:
        errors.append(f"unable to parse network: {exc}")
        return {
            "valid": False,
            "errors": errors,
            "checks": checks,
            "net_sha256": _sha256(path) if path.exists() else None,
            "profile": _profile_dict(profile),
        }

    edges = {element.attrib.get("id"): element for element in root.findall("edge")}
    lanes = {lane.attrib.get("id"): lane for edge in edges.values() for lane in edge.findall("lane")}
    junctions = {element.attrib.get("id"): element for element in root.findall("junction")}
    tls_logics = {element.attrib.get("id"): element for element in root.findall("tlLogic")}
    connections = []
    adjacency = defaultdict(list)
    for element in root.findall("connection"):
        source = element.attrib.get("from")
        target = element.attrib.get("to")
        link_index = element.attrib.get("linkIndex")
        connection = {
            "from": source,
            "to": target,
            "tl": element.attrib.get("tl"),
            "linkIndex": int(link_index) if link_index is not None and link_index.isdigit() else None,
        }
        connections.append(connection)
        if source and target:
            adjacency[source].append((target, connection["tl"] == profile.tls_id and connection["from"] == profile.controlled_from_edge and connection["to"] == profile.controlled_to_edge and connection["linkIndex"] in profile.controlled_link_indices))

    def fail(name: str, message: str) -> None:
        errors.append(message)

    junction = junctions.get(profile.tls_id)
    if junction is not None and junction.attrib.get("type") == "traffic_light":
        checks["tls_junction"] = "PASS"
    else:
        fail("tls_junction", f"missing traffic_light junction {profile.tls_id}")

    logic = tls_logics.get(profile.tls_id)
    phases = logic.findall("phase") if logic is not None else []
    if logic is not None and any(len(phase.attrib.get("state", "")) == 2 for phase in phases):
        checks["tls_logic"] = "PASS"
    else:
        fail("tls_logic", f"missing two-position tlLogic {profile.tls_id}")

    controlled = [
        c for c in connections
        if c["tl"] == profile.tls_id and c["from"] == profile.controlled_from_edge
    ]
    if (
        len(controlled) == len(profile.controlled_link_indices)
        and {c["linkIndex"] for c in controlled} == set(profile.controlled_link_indices)
        and all(c["to"] == profile.controlled_to_edge for c in controlled)
        and profile.controlled_to_edge in edges
    ):
        checks["controlled_connections"] = "PASS"
    else:
        fail("controlled_connections", "J5 controlled connections do not match the frozen profile")

    passenger_lane_ids = {profile.ramp_queue_lane_id, *profile.downstream_lane_ids}
    if all(lanes.get(lane_id) is not None and "passenger" in lanes[lane_id].attrib.get("allow", "").split() for lane_id in passenger_lane_ids):
        checks["passenger_lanes"] = "PASS"
    else:
        fail("passenger_lanes", "required passenger lanes are missing or not passenger-enabled")

    merge_ok = (
        profile.ramp_merge_edge in edges
        and profile.mainline_merge_edge in edges
        and profile.downstream_edge in edges
        and any(c["from"] == profile.ramp_merge_edge and c["to"] == profile.downstream_edge for c in connections)
        and any(c["from"] == profile.mainline_merge_edge and c["to"] == profile.downstream_edge for c in connections)
    )
    if merge_ok:
        checks["merge_topology"] = "PASS"
    else:
        fail("merge_topology", "ramp/mainline merge topology does not match the frozen profile")

    controlled_transition = (profile.controlled_from_edge, profile.controlled_to_edge)
    ramp_path_ok = (
        profile.controlled_from_edge in edges
        and _reachable(adjacency, "E2", "E1", controlled_transition, True)
    )
    if ramp_path_ok:
        checks["ramp_path_uses_control"] = "PASS"
    else:
        fail("ramp_path_uses_control", "E2 to E1 path does not use the J5 controlled transition")

    mainline_path_ok = _reachable(adjacency, "776458555", "E1", controlled_transition, False)
    if mainline_path_ok:
        checks["mainline_path_avoids_controlled_entry"] = "PASS"
    else:
        fail("mainline_path_avoids_controlled_entry", "mainline path uses the J5 controlled entry or is unreachable")

    return {
        "valid": not errors and all(value == "PASS" for value in checks.values()),
        "errors": errors,
        "checks": checks,
        "net_sha256": _sha256(path),
        "profile": _profile_dict(profile),
    }


def build_detector_tree(profile: OsmControlProfile = OSM_CONTROL_PROFILE) -> ET.ElementTree:
    root = ET.Element("additional")
    ET.SubElement(root, "inductionLoop", {
        "id": profile.mainline_detector_ids[0], "lane": profile.downstream_lane_ids[0],
        "pos": "150", "freq": "30", "file": f"native/{profile.mainline_detector_ids[0]}.xml",
    })
    ET.SubElement(root, "inductionLoop", {
        "id": profile.mainline_detector_ids[1], "lane": profile.downstream_lane_ids[1],
        "pos": "150", "freq": "30", "file": f"native/{profile.mainline_detector_ids[1]}.xml",
    })
    ET.SubElement(root, "laneAreaDetector", {
        "id": profile.ramp_queue_detector_id, "lane": profile.ramp_queue_lane_id,
        "pos": "0", "endPos": "430", "freq": "30", "file": f"native/{profile.ramp_queue_detector_id}.xml",
    })
    ET.SubElement(root, "inductionLoop", {
        "id": profile.ramp_arrival_detector_id, "lane": profile.ramp_queue_lane_id,
        "pos": "20", "freq": "30", "file": f"native/{profile.ramp_arrival_detector_id}.xml",
    })
    ET.indent(root, space="    ")
    return ET.ElementTree(root)


def write_detector_file(output_path: str | Path, profile: OsmControlProfile = OSM_CONTROL_PROFILE) -> str:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    build_detector_tree(profile).write(path, encoding="utf-8", xml_declaration=True)
    return _sha256(path)


def aggregate_mainline_occupancy(conn: object, profile: OsmControlProfile = OSM_CONTROL_PROFILE) -> float:
    readings = [conn.inductionloop.getLastStepOccupancy(detector_id) for detector_id in profile.mainline_detector_ids]
    return sum(readings) / len(readings) / 100.0


def read_ramp_queue(conn: object, profile: OsmControlProfile = OSM_CONTROL_PROFILE) -> tuple[int, int]:
    detector_id = profile.ramp_queue_detector_id
    return (
        conn.lanearea.getLastStepVehicleNumber(detector_id),
        conn.lanearea.getLastStepHaltingNumber(detector_id),
    )


def apply_ramp_signal(conn: object, action: object, current_time: int, cycle_s: int, profile: OsmControlProfile = OSM_CONTROL_PROFILE) -> str:
    in_green = current_time % cycle_s < action.green_s
    state = profile.green_state if in_green else profile.red_state
    conn.trafficlight.setRedYellowGreenState(profile.tls_id, state)
    return state


def build_control_adapter_audit(output_dir: str | Path, net_path: str | Path = "osm.net.xml", profile: OsmControlProfile = OSM_CONTROL_PROFILE) -> dict[str, object]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    audit = audit_osm_network(net_path, profile)
    detector_sha256 = None
    if audit["valid"]:
        detector_sha256 = write_detector_file(output / "osm_control.add.xml", profile)
    core = {
        "schema_version": "stage4a-osm-control-adapter-v1",
        "net_sha256": audit["net_sha256"],
        "profile": audit["profile"],
        "checks": audit["checks"],
        "detector_file": "osm_control.add.xml" if audit["valid"] else None,
        "detector_sha256": detector_sha256 if audit["valid"] else None,
        "errors": audit["errors"],
    }
    core_bytes = json.dumps(core, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    core_sha256 = hashlib.sha256(core_bytes).hexdigest().upper()
    report = {
        **core,
        "generation_timestamp": datetime.now(timezone.utc).isoformat(),
        "valid": bool(audit["valid"]),
        "core_sha256": core_sha256,
    }
    (output / "control_adapter_audit.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {**report, "core": core}
