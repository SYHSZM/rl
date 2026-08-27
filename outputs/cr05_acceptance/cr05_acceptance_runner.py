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
sys.path.insert(0, str(PROJECT_ROOT))

from build_network import NETWORK_SOURCE_FILENAMES, preflight_network, verify_frozen_network
from env import run_experiment
from experiment_config import DemandPhase, DemandPoint, default_config


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def network_snapshot() -> dict[str, str]:
    return {
        str(path.relative_to(PROJECT_ROOT)).replace("\\", "/"): sha256(path)
        for path in sorted((PROJECT_ROOT / "network").glob("*.xml"))
    }


def file_manifest(root: Path) -> list[dict[str, object]]:
    return [
        {
            "path": str(path.relative_to(root)).replace("\\", "/"),
            "bytes": path.stat().st_size,
            "sha256": sha256(path),
        }
        for path in sorted(root.rglob("*"))
        if path.is_file()
    ]


def main() -> int:
    before = network_snapshot()
    config = default_config()
    config.simulation_duration_s = 60
    config.metrics_interval_s = 30
    config.seeds = (0,)
    config.demand_phases = [DemandPhase(0, 60, 1.0)]
    demand = DemandPoint(1200, 120)
    frozen = preflight_network(EVIDENCE_DIR / "frozen" / "merge.net.xml")
    first = run_experiment(config, demand, "none", 0, output_root=EVIDENCE_DIR / "short_run_root", frozen_network=frozen)
    second = run_experiment(config, demand, "none", 0, output_root=EVIDENCE_DIR / "isolation_second_root", frozen_network=frozen)
    verify_frozen_network(frozen)
    after = network_snapshot()
    changed = sorted(path for path in set(before) | set(after) if before.get(path) != after.get(path))
    guard = {
        "captured_utc": datetime.now(timezone.utc).isoformat(),
        "before": before,
        "after": after,
        "changed_paths": changed,
        "unchanged": before == after,
        "frozen_net_path": str(frozen.net_path),
        "frozen_net_sha256": frozen.net_sha256,
        "five_source_sha256_before": frozen.source_sha256,
        "five_source_sha256_after": {
            name: sha256(frozen.source_dir / name) for name in NETWORK_SOURCE_FILENAMES
        },
    }
    (EVIDENCE_DIR / "real_workspace_network_source_hash_guard.json").write_text(
        json.dumps(guard, indent=2, sort_keys=True), encoding="utf-8"
    )
    first_files = {str(path.resolve()) for path in first.output_dir.rglob("*") if path.is_file()}
    second_files = {str(path.resolve()) for path in second.output_dir.rglob("*") if path.is_file()}
    isolation = {
        "first_output_dir": str(first.output_dir),
        "second_output_dir": str(second.output_dir),
        "first_valid": first.valid,
        "second_valid": second.valid,
        "first_native_xml_count": len(list((first.output_dir / "native").glob("*.xml"))),
        "second_native_xml_count": len(list((second.output_dir / "native").glob("*.xml"))),
        "shared_generated_files": sorted(first_files & second_files),
        "isolated": not (first_files & second_files),
        "note": "the frozen network is a shared immutable input outside both generated-output sets",
    }
    (EVIDENCE_DIR / "two_run_isolation_check.json").write_text(
        json.dumps(isolation, indent=2, sort_keys=True), encoding="utf-8"
    )
    invocation = {
        "captured_utc": datetime.now(timezone.utc).isoformat(),
        "command": [sys.executable, str(Path(__file__).resolve())],
        "cwd": str(PROJECT_ROOT),
        "SUMO_HOME": os.environ.get("SUMO_HOME", ""),
        "config": asdict(config),
        "demand": asdict(demand),
        "controller": "none",
        "seed": 0,
        "frozen_network": {
            "path": str(frozen.net_path),
            "sha256": frozen.net_sha256,
            "source_sha256": frozen.source_sha256,
        },
        "first_result": {"valid": first.valid, "output_dir": str(first.output_dir), "windows": len(first.window_records)},
        "second_result": {"valid": second.valid, "output_dir": str(second.output_dir), "windows": len(second.window_records)},
    }
    (EVIDENCE_DIR / "short_run_command_config.json").write_text(
        json.dumps(invocation, indent=2, sort_keys=True), encoding="utf-8"
    )
    manifest = {
        "root": str(first.output_dir),
        "files": file_manifest(first.output_dir),
    }
    (EVIDENCE_DIR / "short_run_output_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
    )
    checks = {
        "first_valid": first.valid,
        "second_valid": second.valid,
        "first_windows": len(first.window_records),
        "second_windows": len(second.window_records),
        "network_source_unchanged": guard["unchanged"],
        "two_run_isolated": isolation["isolated"],
        "native_counts": [isolation["first_native_xml_count"], isolation["second_native_xml_count"]],
    }
    (EVIDENCE_DIR / "acceptance_result.json").write_text(
        json.dumps(checks, indent=2, sort_keys=True), encoding="utf-8"
    )
    print(json.dumps(checks, indent=2, sort_keys=True))
    return 0 if first.valid and second.valid and guard["unchanged"] and isolation["isolated"] and isolation["first_native_xml_count"] == isolation["second_native_xml_count"] == 8 else 1


if __name__ == "__main__":
    raise SystemExit(main())
