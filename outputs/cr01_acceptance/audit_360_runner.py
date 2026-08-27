from __future__ import annotations

import hashlib
import json
import math
import sys
from dataclasses import asdict
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
    frozen = preflight_network(EVIDENCE_DIR / "audit_360_frozen" / "merge.net.xml")
    result = run_experiment(
        config,
        demand,
        "none",
        0,
        output_root=EVIDENCE_DIR / "audit_360_run",
        frozen_network=frozen,
    )
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

    first = result.window_records[0]
    checks = {
        "run_valid": result.valid,
        "output_dir": str(result.output_dir),
        "window_count": len(result.window_records),
        "first_window": asdict(first),
        "expected_semantically_comparable": {
            "mean_speed_mps": 33.1209244658,
            "upstream_speed_mps": None,
            "bottleneck_flow_veh": 0,
            "bottleneck_occupancy": 0.0,
            "ramp_vehicle_mean_veh": 14 / 30,
            "ramp_vehicle_max_veh": 1,
        },
        "legacy_tts_expected_s": 19530.0,
        "legacy_tts_actual_s": result.summary.total_time_spent_s,
        "native_xml_count": len(list((result.output_dir / "native").glob("*.xml"))),
        "real_workspace_network_unchanged": guard["unchanged"],
    }
    checks["first_window_matches_task1_trace"] = (
        math.isclose(first.mean_speed_mps or 0.0, 33.1209244658, rel_tol=0.0, abs_tol=1e-9)
        and first.upstream_speed_mps is None
        and first.upstream_speed_vehicle_observations == 0
        and first.bottleneck_flow_veh == 0
        and math.isclose(first.bottleneck_occupancy, 0.0, rel_tol=0.0, abs_tol=1e-12)
        and math.isclose(first.ramp_vehicle_mean_veh, 14 / 30, rel_tol=0.0, abs_tol=1e-12)
        and first.ramp_vehicle_max_veh == 1
    )
    (EVIDENCE_DIR / "audit_360_check.json").write_text(
        json.dumps(checks, indent=2, sort_keys=True), encoding="utf-8"
    )
    print(json.dumps(checks, indent=2, sort_keys=True))
    accepted = (
        result.valid
        and len(result.window_records) == 12
        and checks["first_window_matches_task1_trace"]
        and result.summary.total_time_spent_s == 19530.0
        and checks["native_xml_count"] == 8
        and guard["unchanged"]
    )
    return 0 if accepted else 1


if __name__ == "__main__":
    raise SystemExit(main())
