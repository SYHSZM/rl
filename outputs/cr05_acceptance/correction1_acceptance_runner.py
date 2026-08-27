from __future__ import annotations

import hashlib
import json
import os
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path


EVIDENCE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = EVIDENCE_DIR.parents[1]
RUN_ROOT = EVIDENCE_DIR / "correction1_same_root_runs"
sys.path.insert(0, str(PROJECT_ROOT))

from build_network import NETWORK_SOURCE_FILENAMES, preflight_network, verify_frozen_network
from env import run_experiment
from experiment_config import DemandPhase, DemandPoint, default_config


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def manifest(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)).replace("\\", "/"): sha256(path)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def network_snapshot() -> dict[str, str]:
    return {
        str(path.relative_to(PROJECT_ROOT)).replace("\\", "/"): sha256(path)
        for path in sorted((PROJECT_ROOT / "network").glob("*.xml"))
    }


def main() -> int:
    before_network = network_snapshot()
    config = default_config()
    config.simulation_duration_s = 60
    config.metrics_interval_s = 30
    config.seeds = (0,)
    config.demand_phases = [DemandPhase(0, 60, 1.0)]
    demand = DemandPoint(1200, 120)
    frozen = preflight_network(EVIDENCE_DIR / "correction1_frozen" / "merge.net.xml")

    first = run_experiment(config, demand, "none", 0, output_root=RUN_ROOT, frozen_network=frozen)
    first_before_second = manifest(first.output_dir)
    second = run_experiment(config, demand, "none", 0, output_root=RUN_ROOT, frozen_network=frozen)
    first_after_second = manifest(first.output_dir)
    verify_frozen_network(frozen)
    after_network = network_snapshot()

    changed_network = sorted(
        path for path in set(before_network) | set(after_network)
        if before_network.get(path) != after_network.get(path)
    )
    first_files = {str(path.resolve()) for path in first.output_dir.rglob("*") if path.is_file()}
    second_files = {str(path.resolve()) for path in second.output_dir.rglob("*") if path.is_file()}
    same_root_check = {
        "captured_utc": datetime.now(timezone.utc).isoformat(),
        "experiment_id_unchanged": first.experiment_id == second.experiment_id,
        "experiment_id": first.experiment_id,
        "first_output_dir": str(first.output_dir),
        "second_output_dir": str(second.output_dir),
        "first_attempt_name": first.output_dir.name,
        "second_attempt_name": second.output_dir.name,
        "same_experiment_parent": first.output_dir.parent == second.output_dir.parent,
        "first_valid": first.valid,
        "second_valid": second.valid,
        "first_windows": len(first.window_records),
        "second_windows": len(second.window_records),
        "first_native_xml_count": len(list((first.output_dir / "native").glob("*.xml"))),
        "second_native_xml_count": len(list((second.output_dir / "native").glob("*.xml"))),
        "first_manifest_before_second": first_before_second,
        "first_manifest_after_second": first_after_second,
        "first_attempt_unchanged_after_second": first_before_second == first_after_second,
        "shared_generated_files": sorted(first_files & second_files),
    }
    (EVIDENCE_DIR / "correction1_same_root_attempt_check.json").write_text(
        json.dumps(same_root_check, indent=2, sort_keys=True), encoding="utf-8"
    )

    network_guard = {
        "captured_utc": datetime.now(timezone.utc).isoformat(),
        "before": before_network,
        "after": after_network,
        "changed_paths": changed_network,
        "unchanged": not changed_network,
        "frozen_net_path": str(frozen.net_path),
        "frozen_net_sha256": frozen.net_sha256,
        "five_source_sha256_before": frozen.source_sha256,
        "five_source_sha256_after": {
            name: sha256(frozen.source_dir / name) for name in NETWORK_SOURCE_FILENAMES
        },
    }
    (EVIDENCE_DIR / "correction1_real_workspace_hash_guard.json").write_text(
        json.dumps(network_guard, indent=2, sort_keys=True), encoding="utf-8"
    )

    checks = {
        "command": [sys.executable, str(Path(__file__).resolve())],
        "cwd": str(PROJECT_ROOT),
        "SUMO_HOME": os.environ.get("SUMO_HOME", ""),
        "config": asdict(config),
        "demand": asdict(demand),
        "first_valid": first.valid,
        "second_valid": second.valid,
        "attempt_names": [first.output_dir.name, second.output_dir.name],
        "same_experiment_parent": same_root_check["same_experiment_parent"],
        "first_attempt_unchanged_after_second": same_root_check["first_attempt_unchanged_after_second"],
        "native_counts": [same_root_check["first_native_xml_count"], same_root_check["second_native_xml_count"]],
        "shared_generated_files": same_root_check["shared_generated_files"],
        "real_workspace_network_unchanged": network_guard["unchanged"],
    }
    (EVIDENCE_DIR / "correction1_acceptance_result.json").write_text(
        json.dumps(checks, indent=2, sort_keys=True), encoding="utf-8"
    )
    print(json.dumps(checks, indent=2, sort_keys=True))
    accepted = (
        first.valid and second.valid
        and checks["attempt_names"] == ["attempt_0001", "attempt_0002"]
        and checks["same_experiment_parent"]
        and checks["first_attempt_unchanged_after_second"]
        and checks["native_counts"] == [8, 8]
        and not checks["shared_generated_files"]
        and checks["real_workspace_network_unchanged"]
    )
    return 0 if accepted else 1


if __name__ == "__main__":
    raise SystemExit(main())
