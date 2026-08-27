from __future__ import annotations

import hashlib
import json
import math
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path


EVIDENCE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = EVIDENCE_DIR.parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from build_network import preflight_network, verify_frozen_network
from env import run_experiment
from experiment_config import DemandPhase, DemandPoint, default_config


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def network_snapshot() -> dict[str, str]:
    return {
        str(path.relative_to(PROJECT_ROOT)).replace("\\", "/"): sha256(path)
        for path in sorted((PROJECT_ROOT / "network").glob("*.xml"))
    }


def main() -> int:
    before = network_snapshot()
    config = default_config()
    config.simulation_duration_s = 360
    config.metrics_interval_s = 30
    config.seeds = (0,)
    config.demand_phases = [DemandPhase(0, 360, 1.0)]
    demand = DemandPoint(1200, 120)
    frozen = preflight_network(EVIDENCE_DIR / "audit_frozen" / "merge.net.xml")
    result = run_experiment(config, demand, "none", 0, EVIDENCE_DIR / "audit_run", frozen_network=frozen)
    verify_frozen_network(frozen)
    after = network_snapshot()
    changed = sorted(path for path in set(before) | set(after) if before.get(path) != after.get(path))
    guard = {
        "captured_utc": datetime.now(timezone.utc).isoformat(),
        "before": before,
        "after": after,
        "changed_paths": changed,
        "unchanged": not changed,
    }
    (EVIDENCE_DIR / "real_workspace_network_hash_guard.json").write_text(
        json.dumps(guard, indent=2, sort_keys=True), encoding="utf-8"
    )

    exposure = json.loads((result.output_dir / "system_exposure.json").read_text(encoding="utf-8"))
    tripinfo_root = ET.parse(result.output_dir / "tripinfo.xml").getroot()
    completed_tripinfo_duration_s = sum(float(element.attrib["duration"]) for element in tripinfo_root.findall("tripinfo"))
    first = result.window_records[0]
    summary = result.summary
    checks = {
        "run_valid": result.valid,
        "output_dir": str(result.output_dir),
        "window_count": len(result.window_records),
        "native_xml_count": len(list((result.output_dir / "native").glob("*.xml"))),
        "tts_system_s": summary.tts_system_s,
        "tts_in_network_s": summary.tts_in_network_s,
        "tts_pending_s": summary.tts_pending_s,
        "total_time_spent_s": summary.total_time_spent_s,
        "legacy_total_time_spent_s": summary.legacy_total_time_spent_s,
        "completed_vehicle_exposure_s": summary.completed_vehicle_exposure_s,
        "completed_tripinfo_duration_s": completed_tripinfo_duration_s,
        "terminal_in_network_count": summary.terminal_in_network_count,
        "terminal_pending_count": summary.terminal_pending_count,
        "terminal_in_network_exposure_s": summary.terminal_in_network_exposure_s,
        "completed_plus_terminal_s": summary.completed_vehicle_exposure_s + summary.terminal_in_network_exposure_s,
        "loaded_departed_arrived": [summary.loaded_veh, summary.departed_veh, summary.arrived_veh],
        "first_window": {
            "mean_speed_mps": first.mean_speed_mps,
            "upstream_speed_mps": first.upstream_speed_mps,
            "bottleneck_flow_veh": first.bottleneck_flow_veh,
            "bottleneck_occupancy": first.bottleneck_occupancy,
            "ramp_vehicle_mean_veh": first.ramp_vehicle_mean_veh,
            "ramp_vehicle_max_veh": first.ramp_vehicle_max_veh,
        },
        "artifact_terminal_matches_summary": (
            exposure["terminal"]["in_network_count"] == summary.terminal_in_network_count
            and exposure["terminal"]["pending_count"] == summary.terminal_pending_count
        ),
        "real_workspace_network_unchanged": guard["unchanged"],
    }
    checks["frozen_reconciliation_passed"] = (
        summary.total_time_spent_s == summary.tts_system_s == 18817.0
        and summary.tts_in_network_s == 18817.0
        and summary.tts_pending_s == 0.0
        and summary.legacy_total_time_spent_s == 19530.0
        and completed_tripinfo_duration_s == 11302.0
        and summary.completed_vehicle_exposure_s == 11302.0
        and summary.terminal_in_network_count == 73
        and summary.terminal_pending_count == 0
        and summary.terminal_in_network_exposure_s == 7515.0
        and summary.completed_vehicle_exposure_s + summary.terminal_in_network_exposure_s == summary.tts_system_s
        and len(result.window_records) == 12
        and checks["native_xml_count"] == 8
        and math.isclose(first.mean_speed_mps or 0.0, 33.1209244658, rel_tol=0.0, abs_tol=1e-9)
        and first.upstream_speed_mps is None
        and guard["unchanged"]
    )
    (EVIDENCE_DIR / "audit_360_check.json").write_text(
        json.dumps(checks, indent=2, sort_keys=True), encoding="utf-8"
    )
    print(json.dumps(checks, indent=2, sort_keys=True))
    return 0 if result.valid and checks["frozen_reconciliation_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
