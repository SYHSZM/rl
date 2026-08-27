from __future__ import annotations

import hashlib
import json
import sys
import xml.etree.ElementTree as ET
from dataclasses import asdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from build_network import NETWORK_SOURCE_FILENAMES, preflight_network, verify_frozen_network
from env import run_experiment
from experiment_config import DemandPhase, DemandPoint, ExperimentConfig


OUT = ROOT / "outputs" / "stage_1"
NETWORK = ROOT / "network"
PROBES = (
    ("low", DemandPoint(3000, 300)),
    ("medium", DemandPoint(4500, 600)),
    ("high", DemandPoint(6000, 1200)),
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def source_hashes() -> dict[str, str]:
    return {name: sha256(NETWORK / name) for name in NETWORK_SOURCE_FILENAMES}


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def static_snapshot(net_path: Path) -> dict[str, object]:
    source_nodes = ET.parse(NETWORK / "merge.nod.xml").getroot()
    source_edges = ET.parse(NETWORK / "merge.edg.xml").getroot()
    source_connections = ET.parse(NETWORK / "merge.con.xml").getroot()
    source_tls = ET.parse(NETWORK / "merge.tll.xml").getroot()
    detectors = ET.parse(NETWORK / "merge.add.xml").getroot()
    generated = ET.parse(net_path).getroot()

    generated_edges = []
    for edge in generated.findall("edge"):
        if edge.get("function") == "internal":
            kind = "internal"
        else:
            kind = "external"
        lanes = edge.findall("lane")
        generated_edges.append(
            {
                "id": edge.get("id"),
                "kind": kind,
                "from": edge.get("from", ""),
                "to": edge.get("to", ""),
                "lanes": [
                    {
                        "id": lane.get("id"),
                        "speed_mps": float(lane.get("speed", "0")),
                        "length_m": float(lane.get("length", "0")),
                    }
                    for lane in lanes
                ],
            }
        )

    return {
        "source_nodes": [dict(node.attrib) for node in source_nodes],
        "source_edges": [dict(edge.attrib) for edge in source_edges],
        "source_connections": [dict(item.attrib) for item in source_connections],
        "source_signal_programs": [
            {
                "id": logic.get("id"),
                "type": logic.get("type"),
                "programID": logic.get("programID"),
                "phases": [dict(phase.attrib) for phase in logic.findall("phase")],
            }
            for logic in source_tls
        ],
        "detectors": [
            {"type": local_name(item.tag), **dict(item.attrib)} for item in detectors
        ],
        "generated_edges": generated_edges,
        "generated_signal_connections": [
            dict(item.attrib)
            for item in generated.findall("connection")
            if item.get("tl") == "m4"
        ],
        "generated_signal_programs": [
            {
                "id": logic.get("id"),
                "type": logic.get("type"),
                "programID": logic.get("programID"),
                "phases": [dict(phase.attrib) for phase in logic.findall("phase")],
            }
            for logic in generated.findall("tlLogic")
        ],
    }


def warning_hits(run_dir: Path) -> list[str]:
    hits = []
    needles = ("collision", "teleport", "error", "emergency braking")
    for name in ("sumo.log", "sumo_error.log"):
        path = run_dir / name
        if not path.is_file():
            hits.append(f"missing:{name}")
            continue
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if any(needle in line.lower() for needle in needles):
                hits.append(f"{name}:{line.strip()}")
    return hits


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    before = source_hashes()
    frozen = preflight_network(OUT / "sol_preflight" / "merge.net.xml")
    verify_frozen_network(frozen)
    snapshot = static_snapshot(frozen.net_path)
    (OUT / "sol_scene_static_snapshot.json").write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
    )

    config = ExperimentConfig(
        simulation_duration_s=360,
        metrics_interval_s=30,
        seeds=(0,),
        demand_phases=[DemandPhase(0, 360, 1.0)],
    )
    rows = []
    for level, demand in PROBES:
        result = run_experiment(
            config,
            demand,
            "none",
            0,
            output_root=OUT / "sol_short_probes",
            frozen_network=frozen,
        )
        native = sorted((result.output_dir / "native").glob("*.xml"))
        rows.append(
            {
                "level": level,
                "demand": asdict(demand),
                "experiment_id": result.experiment_id,
                "output_dir": str(result.output_dir.relative_to(ROOT)),
                "valid": result.valid,
                "failure_reason": result.failure_reason,
                "window_count": len(result.window_records),
                "native_detector_file_count": len(native),
                "warnings": warning_hits(result.output_dir),
                "summary": asdict(result.summary),
            }
        )

    after = source_hashes()
    report = {
        "purpose": "Sol scene integration precheck; not formal research evidence",
        "short_probe_duration_s": 360,
        "short_probe_demand_multiplier": 1.0,
        "seed": 0,
        "net_path": str(frozen.net_path.relative_to(ROOT)),
        "net_sha256": frozen.net_sha256,
        "source_sha256_before": before,
        "source_sha256_after": after,
        "source_unchanged": before == after,
        "runs": rows,
    }
    (OUT / "sol_short_probe_summary.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    if before != after or any(not row["valid"] or row["warnings"] for row in rows):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
