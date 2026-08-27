from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import tempfile
from dataclasses import asdict, fields
from datetime import datetime, timezone
from pathlib import Path

from build_network import (
    NETWORK_DIR,
    NETWORK_SOURCE_FILENAMES,
    FrozenNetwork,
    FrozenNetworkMismatchError,
    preflight_network,
    required_detector_ids,
    verify_frozen_network,
)
from classify import DemandClassification, classify_demand_pair
from env import _invalid_summary, _retryable_failure, run_experiment
from experiment_config import DemandPoint, ExperimentConfig, default_config, experiment_id, iter_demand_grid, validate_config
from metrics import EpisodeSummary


_UTC_DATETIME = datetime
_PROJECT_ROOT = Path(__file__).resolve().parent
_PROTOCOL_PATH = _PROJECT_ROOT / "docs" / "experiment_protocol_v1.0.md"
_PROTOCOL_VERSION = "1.0"
_CORE_CODE_FILENAMES = (
    "build_network.py",
    "classify.py",
    "controllers.py",
    "env.py",
    "experiment_config.py",
    "metrics.py",
    "rou_generate.py",
    "scan_demand.py",
)
_SCHEMAS = {
    "artifact_manifest": "cr04-artifact-manifest-v1",
    "attempt": "cr03-attempt-v1",
    "scan_manifest": "cr04-scan-manifest-v1",
    "summary": "cr02-summary-v1",
    "system_exposure": "cr02-system-exposure-v1",
    "window": "cr02-window-v1",
}
_SCAN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_ARTIFACT_MANIFEST_NAME = "artifact_manifest.json"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(_canonical_json_bytes(value)).hexdigest()


def _scan_identity(config: ExperimentConfig, controllers: tuple[str, ...]) -> dict[str, object]:
    code_files = {name: _sha256_file(_PROJECT_ROOT / name) for name in _CORE_CODE_FILENAMES}
    scene_files = {name: _sha256_file(NETWORK_DIR / name) for name in NETWORK_SOURCE_FILENAMES}
    return {
        "config": asdict(config),
        "controllers": list(controllers),
        "scene": {
            "files": scene_files,
            "sha256": _canonical_sha256(scene_files),
        },
        "code": {
            "files": code_files,
            "sha256": _canonical_sha256(code_files),
        },
        "protocol": {
            "path": "docs/experiment_protocol_v1.0.md",
            "version": _PROTOCOL_VERSION,
            "sha256": _sha256_file(_PROTOCOL_PATH),
        },
        "schemas": dict(_SCHEMAS),
    }


def _atomic_write_json(path: Path, payload: object) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise


def _validate_scan_id(scan_id: str) -> None:
    if not _SCAN_ID_PATTERN.fullmatch(scan_id) or scan_id in {".", ".."}:
        raise ValueError("scan_id must contain only letters, digits, '.', '_', or '-' and must not traverse paths")


def _allocate_or_resume_scan_dir(
    output_root: str | Path,
    base_scan_id: str,
    resume: bool,
) -> tuple[Path, bool]:
    output_root = Path(output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    if not resume:
        return _allocate_scan_dir(output_root, base_scan_id), True
    candidate = output_root / base_scan_id
    try:
        candidate.mkdir(exist_ok=False)
    except FileExistsError:
        return candidate, False
    return candidate, True


def _frozen_network_payload(frozen: FrozenNetwork) -> dict[str, object]:
    return {
        "net_path": str(frozen.net_path.resolve()),
        "net_sha256": frozen.net_sha256,
        "source_dir": str(frozen.source_dir.resolve()),
        "source_sha256": dict(frozen.source_sha256),
    }


def _new_scan_manifest(
    scan_dir: Path,
    identity: dict[str, object],
    identity_sha256: str,
    frozen_network: FrozenNetwork,
) -> dict[str, object]:
    return {
        "schema_version": _SCHEMAS["scan_manifest"],
        "scan_id": scan_dir.name,
        "identity_sha256": identity_sha256,
        "identity": identity,
        "frozen_network": _frozen_network_payload(frozen_network),
        "created_at": _UTC_DATETIME.now(timezone.utc).isoformat(),
    }


def _read_scan_manifest(
    scan_dir: Path,
    expected_identity: dict[str, object],
    expected_identity_sha256: str,
) -> tuple[dict[str, object], FrozenNetwork]:
    path = scan_dir / "scan_manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or manifest.get("schema_version") != _SCHEMAS["scan_manifest"]:
        raise ValueError("scan manifest schema mismatch")
    if manifest.get("scan_id") != scan_dir.name:
        raise ValueError("scan manifest scan_id mismatch")
    if (
        manifest.get("identity_sha256") != expected_identity_sha256
        or _canonical_json_bytes(manifest.get("identity")) != _canonical_json_bytes(expected_identity)
    ):
        raise ValueError("scan manifest identity mismatch")
    if _canonical_sha256(manifest["identity"]) != manifest["identity_sha256"]:
        raise ValueError("scan manifest identity hash mismatch")
    frozen_payload = manifest.get("frozen_network")
    required = {"net_path", "net_sha256", "source_dir", "source_sha256"}
    if not isinstance(frozen_payload, dict) or required - set(frozen_payload):
        raise ValueError("scan manifest frozen network is incomplete")
    frozen_network = FrozenNetwork(
        net_path=Path(str(frozen_payload["net_path"])),
        net_sha256=str(frozen_payload["net_sha256"]),
        source_dir=Path(str(frozen_payload["source_dir"])),
        source_sha256=dict(frozen_payload["source_sha256"]),
    )
    verify_frozen_network(frozen_network)
    return manifest, frozen_network


def planned_runs(config: ExperimentConfig, controllers: tuple[str, ...]) -> list[tuple[DemandPoint, str, int]]:
    validate_config(config)
    return [(demand, controller, seed) for demand in iter_demand_grid(config) for seed in config.seeds for controller in controllers]


def _planned_identity(
    scan_manifest: dict[str, object],
    config: ExperimentConfig,
    demand: DemandPoint,
    controller: str,
    seed: int,
) -> dict[str, object]:
    identity = scan_manifest["identity"]
    config_payload = asdict(config)
    return {
        "scan_id": scan_manifest["scan_id"],
        "scan_identity_sha256": scan_manifest["identity_sha256"],
        "config": config_payload,
        "config_sha256": _canonical_sha256(config_payload),
        "scene_sha256": identity["scene"]["sha256"],
        "code_sha256": identity["code"]["sha256"],
        "protocol": dict(identity["protocol"]),
        "controller": controller,
        "demand": asdict(demand),
        "seed": seed,
        "experiment_id": experiment_id(config, demand, controller, seed),
        "schemas": dict(identity["schemas"]),
    }


def _artifact_inventory(attempt_dir: Path) -> list[dict[str, object]]:
    rows = []
    for path in sorted(Path(attempt_dir).rglob("*")):
        if not path.is_file() or path.name == _ARTIFACT_MANIFEST_NAME:
            continue
        relative = path.relative_to(attempt_dir).as_posix()
        rows.append({"path": relative, "bytes": path.stat().st_size, "sha256": _sha256_file(path)})
    return rows


def _write_artifact_manifest(attempt_dir: Path, planned_identity: dict[str, object]) -> dict[str, object]:
    manifest = {
        "schema_version": _SCHEMAS["artifact_manifest"],
        "planned_identity": planned_identity,
        "files": _artifact_inventory(attempt_dir),
    }
    _atomic_write_json(Path(attempt_dir) / _ARTIFACT_MANIFEST_NAME, manifest)
    return manifest


def _write_artifact_manifest_without_masking(
    attempt_dir: Path,
    planned_identity: dict[str, object],
    original_error: BaseException,
) -> bool:
    try:
        _write_artifact_manifest(attempt_dir, planned_identity)
    except Exception as artifact_error:
        original_error.add_note(
            f"secondary artifact manifest failure: {type(artifact_error).__name__}: {artifact_error}"
        )
        return False
    return True


def _required_artifact_paths() -> set[str]:
    paths = {
        "attempt.json",
        "config.json",
        "network_snapshot.json",
        "windows.csv",
        "summary.csv",
        "system_exposure.json",
        "merge.rou.xml",
        "run.add.xml",
        "tripinfo.xml",
        "sumo.log",
        "sumo_error.log",
    }
    paths.update(f"native/{detector_id}.xml" for detector_id in required_detector_ids())
    return paths


def _strict_bool(value: str, field: str) -> bool:
    if value == "True":
        return True
    if value == "False":
        return False
    raise ValueError(f"summary {field} must be True or False")


def _read_matching_summary(
    path: Path,
    planned_identity: dict[str, object],
) -> EpisodeSummary:
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    if len(rows) != 1:
        raise ValueError("summary.csv must contain exactly one record")
    row = rows[0]
    required = {field.name for field in fields(EpisodeSummary)}
    if required - set(row):
        raise ValueError("summary.csv is missing required fields")
    demand = planned_identity["demand"]
    if (
        row["experiment_id"] != planned_identity["experiment_id"]
        or row["controller"] != planned_identity["controller"]
        or int(row["seed"]) != planned_identity["seed"]
        or int(row["mainline_vph"]) != demand["mainline_vph"]
        or int(row["ramp_vph"]) != demand["ramp_vph"]
    ):
        raise ValueError("summary.csv planned identity mismatch")
    if row["summary_schema_version"] != _SCHEMAS["summary"]:
        raise ValueError("summary.csv schema mismatch")
    if _strict_bool(row["valid"], "valid") is not True:
        raise ValueError("summary.csv must have valid=true")
    int_fields = {
        "seed", "mainline_vph", "ramp_vph", "bottleneck_throughput_veh",
        "max_ramp_queue_veh", "breakdown_start_s", "congestion_duration_s", "teleports",
        "loaded_veh", "departed_veh", "arrived_veh", "terminal_in_network_count",
        "terminal_pending_count",
    }
    bool_fields = {"valid", "recovered", "terminal_censoring"}
    optional_float_fields = {"mean_speed_mps", "actual_departure_ratio", "completion_ratio"}
    float_fields = {
        "total_time_spent_s", "tts_system_s", "tts_pending_s", "tts_in_network_s",
        "mainline_system_exposure_s", "ramp_system_exposure_s", "unknown_system_exposure_s",
        "completed_vehicle_exposure_s", "terminal_in_network_exposure_s",
        "terminal_pending_exposure_s", "legacy_total_time_spent_s",
    }
    payload: dict[str, object] = {}
    for field in fields(EpisodeSummary):
        name = field.name
        value = row[name]
        if name in int_fields:
            payload[name] = int(value)
        elif name in bool_fields:
            payload[name] = _strict_bool(value, name)
        elif name in optional_float_fields:
            payload[name] = None if value == "" else float(value)
        elif name in float_fields:
            payload[name] = float(value)
        else:
            payload[name] = value
    return EpisodeSummary(**payload)


def _validate_windows(path: Path, planned_identity: dict[str, object]) -> None:
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise ValueError("windows.csv must contain at least one record")
    demand = planned_identity["demand"]
    config = planned_identity["config"]
    interval_s = int(config["metrics_interval_s"])
    duration_s = int(config["simulation_duration_s"])
    expected_times = list(range(interval_s, duration_s + 1, interval_s))
    try:
        actual_times = [int(row["time_s"]) for row in rows]
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("windows.csv time_s values are malformed") from exc
    if actual_times != expected_times:
        raise ValueError(
            "windows.csv time grid must contain exactly one record at each metrics interval "
            "through simulation_duration_s"
        )
    for row in rows:
        if (
            row.get("metric_schema_version") != _SCHEMAS["window"]
            or row.get("experiment_id") != planned_identity["experiment_id"]
            or row.get("controller") != planned_identity["controller"]
            or int(row.get("seed", -1)) != planned_identity["seed"]
            or int(row.get("mainline_vph", -1)) != demand["mainline_vph"]
            or int(row.get("ramp_vph", -1)) != demand["ramp_vph"]
        ):
            raise ValueError("windows.csv schema or planned identity mismatch")


def _validate_attempt_artifacts_or_raise(
    attempt_dir: Path,
    planned_identity: dict[str, object],
    frozen_network: FrozenNetwork,
) -> EpisodeSummary:
    attempt_dir = Path(attempt_dir)
    artifact_path = attempt_dir / _ARTIFACT_MANIFEST_NAME
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    if not isinstance(artifact, dict) or artifact.get("schema_version") != _SCHEMAS["artifact_manifest"]:
        raise ValueError("artifact manifest schema mismatch")
    if _canonical_json_bytes(artifact.get("planned_identity")) != _canonical_json_bytes(planned_identity):
        raise ValueError("artifact manifest planned identity mismatch")
    file_rows = artifact.get("files")
    if not isinstance(file_rows, list):
        raise ValueError("artifact manifest files must be a list")
    inventory: dict[str, dict[str, object]] = {}
    for row in file_rows:
        if not isinstance(row, dict) or set(row) != {"path", "bytes", "sha256"}:
            raise ValueError("artifact manifest file entry is malformed")
        relative = Path(str(row["path"]))
        if relative.is_absolute() or ".." in relative.parts or relative.as_posix() == _ARTIFACT_MANIFEST_NAME:
            raise ValueError("artifact manifest contains an unsafe path")
        key = relative.as_posix()
        if key in inventory:
            raise ValueError("artifact manifest contains duplicate paths")
        inventory[key] = row
        file_path = attempt_dir / relative
        if (
            not file_path.is_file()
            or isinstance(row["bytes"], bool)
            or not isinstance(row["bytes"], int)
            or file_path.stat().st_size != row["bytes"]
            or _sha256_file(file_path) != row["sha256"]
        ):
            raise ValueError(f"artifact file size/hash mismatch: {key}")
    current_paths = {
        path.relative_to(attempt_dir).as_posix()
        for path in attempt_dir.rglob("*")
        if path.is_file() and path.name != _ARTIFACT_MANIFEST_NAME
    }
    if current_paths != set(inventory):
        added = sorted(current_paths - set(inventory))
        removed = sorted(set(inventory) - current_paths)
        details = []
        if added:
            details.append(f"unmanifested files: {', '.join(added)}")
        if removed:
            details.append(f"missing files: {', '.join(removed)}")
        raise ValueError(f"artifact manifest inventory mismatch ({'; '.join(details)})")
    attempt = _read_attempt_record(attempt_dir, expected_experiment_id=str(planned_identity["experiment_id"]))
    if attempt["status"] != "success" or attempt["valid"] is not True:
        raise ValueError("attempt is not a successful valid candidate")
    missing = sorted(_required_artifact_paths() - set(inventory))
    if missing:
        raise ValueError(f"artifact manifest missing required files: {', '.join(missing)}")
    expected_config = {
        "config": planned_identity["config"],
        "demand": planned_identity["demand"],
        "controller": planned_identity["controller"],
        "seed": planned_identity["seed"],
    }
    if _canonical_json_bytes(json.loads((attempt_dir / "config.json").read_text(encoding="utf-8"))) != _canonical_json_bytes(expected_config):
        raise ValueError("config.json planned identity mismatch")
    if json.loads((attempt_dir / "network_snapshot.json").read_text(encoding="utf-8")) != _frozen_network_payload(frozen_network):
        raise ValueError("network_snapshot.json scene mismatch")
    _validate_windows(attempt_dir / "windows.csv", planned_identity)
    exposure = json.loads((attempt_dir / "system_exposure.json").read_text(encoding="utf-8"))
    if not isinstance(exposure, dict) or exposure.get("schema_version") != _SCHEMAS["system_exposure"]:
        raise ValueError("system_exposure.json schema mismatch")
    return _read_matching_summary(attempt_dir / "summary.csv", planned_identity)


def _validate_attempt_artifacts(
    attempt_dir: Path,
    planned_identity: dict[str, object],
    frozen_network: FrozenNetwork,
) -> tuple[EpisodeSummary | None, BaseException | None]:
    try:
        return _validate_attempt_artifacts_or_raise(attempt_dir, planned_identity, frozen_network), None
    except Exception as exc:
        return None, exc


def _validation_reason(exc: BaseException | None) -> str:
    return "" if exc is None else f"{type(exc).__name__}: {exc}"


def _attempt_directories(scan_dir: Path, exp_id: str) -> list[Path]:
    experiment_root = scan_dir / "runs" / exp_id
    return sorted(
        (path for path in experiment_root.glob("attempt_*") if path.is_dir()),
        key=lambda path: path.name,
    )


def _latest_valid_attempt(
    scan_dir: Path,
    planned_identity: dict[str, object],
    frozen_network: FrozenNetwork,
) -> tuple[EpisodeSummary | None, Path | None, str]:
    attempts = _attempt_directories(scan_dir, str(planned_identity["experiment_id"]))
    latest_reason = "no_existing_attempt"
    for attempt_dir in reversed(attempts):
        summary, error = _validate_attempt_artifacts(attempt_dir, planned_identity, frozen_network)
        if summary is not None:
            return summary, attempt_dir, "complete_matching_attempt"
        if latest_reason == "no_existing_attempt":
            latest_reason = _validation_reason(error)
    return None, None, latest_reason


def _index_row_from_attempt(
    attempt_dir: Path,
    demand: DemandPoint,
    controller: str,
    seed: int,
    planned_identity: dict[str, object],
    frozen_network: FrozenNetwork,
) -> dict[str, object]:
    attempt = _safe_attempt_metadata(attempt_dir)
    summary, validation_error = _validate_attempt_artifacts(attempt_dir, planned_identity, frozen_network)
    is_valid = summary is not None
    validation_message = _validation_reason(validation_error)
    if is_valid:
        failure_stage = failure_type = failure_message = ""
        retryable = False
    elif attempt.get("status") == "failed":
        failure_stage = attempt.get("failure_stage", "artifact_validation")
        failure_type = attempt.get("failure_type", "ValueError")
        failure_message = attempt.get("failure_message", validation_message)
        retryable = attempt.get("retryable", False)
    else:
        failure_stage = "output_parse"
        failure_type = type(validation_error).__name__ if validation_error is not None else "ValueError"
        failure_message = str(validation_error) if validation_error is not None else "invalid artifact"
        retryable = False
    suffix = attempt_dir.name.removeprefix("attempt_")
    parsed_attempt = int(suffix) if suffix.isdigit() else ""
    return {
        "experiment_id": planned_identity["experiment_id"],
        "mainline_vph": demand.mainline_vph,
        "ramp_vph": demand.ramp_vph,
        "controller": controller,
        "seed": seed,
        "status": "valid" if is_valid else "invalid",
        "attempt_status": "success" if is_valid else "failed",
        "valid": is_valid,
        "output_dir": str(attempt_dir.resolve()),
        "failure_reason": failure_message,
        "failure_stage": failure_stage,
        "failure_type": failure_type,
        "failure_message": failure_message,
        "retryable": retryable,
        "attempt": attempt.get("attempt", parsed_attempt),
        "started_at": attempt.get("started_at", ""),
        "finished_at": attempt.get("finished_at", ""),
    }


def _rebuild_run_index(
    scan_dir: Path,
    planned_items: list[tuple[DemandPoint, str, int, dict[str, object]]],
    frozen_network: FrozenNetwork,
    extra_rows: list[dict[str, object]] | None = None,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    order: dict[tuple[int, int, str, int], int] = {}
    planned_by_experiment: dict[str, tuple[DemandPoint, str, int, dict[str, object]]] = {}
    planned_by_identity: dict[str, tuple[DemandPoint, str, int, dict[str, object]]] = {}
    for position, (demand, controller, seed, planned_identity) in enumerate(planned_items):
        order[(demand.mainline_vph, demand.ramp_vph, controller, seed)] = position
        item = (demand, controller, seed, planned_identity)
        planned_by_experiment[str(planned_identity["experiment_id"])] = item
        planned_by_identity[_canonical_sha256(planned_identity)] = item
    attempt_dirs = sorted(
        (path for path in (scan_dir / "runs").glob("*/attempt_*") if path.is_dir()),
        key=lambda path: (path.parent.name, path.name),
    )
    for attempt_dir in attempt_dirs:
        item = planned_by_experiment.get(attempt_dir.parent.name)
        try:
            artifact = json.loads((attempt_dir / _ARTIFACT_MANIFEST_NAME).read_text(encoding="utf-8"))
            artifact_identity = artifact.get("planned_identity") if isinstance(artifact, dict) else None
            if isinstance(artifact_identity, dict):
                item = planned_by_identity.get(_canonical_sha256(artifact_identity), item)
        except Exception:
            pass
        if item is None:
            continue
        demand, controller, seed, planned_identity = item
        rows.append(
            _index_row_from_attempt(
                attempt_dir, demand, controller, seed, planned_identity, frozen_network
            )
        )
    rows.extend(extra_rows or [])
    rows.sort(
        key=lambda row: (
            order.get(
                (
                    int(row.get("mainline_vph") or -1),
                    int(row.get("ramp_vph") or -1),
                    str(row.get("controller") or ""),
                    int(row.get("seed") or 0),
                ),
                len(order),
            ),
            int(row.get("attempt") or 0),
            str(row.get("output_dir") or ""),
        )
    )
    _write_index(scan_dir / "run_index.csv", rows)
    return rows


def _write_resume_decisions(path: Path, rows: list[dict[str, object]]) -> None:
    fieldnames = [
        "experiment_id", "mainline_vph", "ramp_vph", "controller", "seed",
        "decision", "reason", "selected_output_dir",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def scan_demand(
    config: ExperimentConfig | None = None,
    output_root: str | Path = "outputs/scan",
    controllers: tuple[str, ...] = ("none", "alinea"),
    resume: bool = True,
    use_gui: bool = False,
    scan_id: str | None = None,
) -> Path:
    config = config or default_config()
    validate_config(config)
    identity = _scan_identity(config, controllers)
    identity_sha256 = _canonical_sha256(identity)
    # Keep content-addressed scan directories usable on Windows where deeply
    # nested attempt artifacts can otherwise exceed the legacy path limit.
    # The complete digest remains authoritative in scan_manifest.json.
    base_scan_id = scan_id or f"scan_{identity_sha256[:16]}"
    _validate_scan_id(base_scan_id)
    scan_dir, created = _allocate_or_resume_scan_dir(output_root, base_scan_id, resume)
    if created:
        try:
            frozen_network = preflight_network(scan_dir / "preflight" / "merge.net.xml")
        except Exception as exc:
            _append_failure_ledger(
                scan_dir / "failure_ledger.csv",
                _failure_ledger_row(None, "", 0, "network_preflight", exc),
            )
            raise
        scan_manifest = _new_scan_manifest(scan_dir, identity, identity_sha256, frozen_network)
        _atomic_write_json(scan_dir / "scan_manifest.json", scan_manifest)
    else:
        scan_manifest, frozen_network = _read_scan_manifest(scan_dir, identity, identity_sha256)

    planned_items = [
        (demand, controller, seed, _planned_identity(scan_manifest, config, demand, controller, seed))
        for demand, controller, seed in planned_runs(config, controllers)
    ]
    extra_index_rows: list[dict[str, object]] = []
    decisions: list[dict[str, object]] = []
    summaries: list[EpisodeSummary] = []

    for demand, controller, seed, planned_identity in planned_items:
        existing_summary, existing_dir, existing_reason = _latest_valid_attempt(
            scan_dir, planned_identity, frozen_network
        )
        decision = {
            "experiment_id": planned_identity["experiment_id"],
            "mainline_vph": demand.mainline_vph,
            "ramp_vph": demand.ramp_vph,
            "controller": controller,
            "seed": seed,
            "decision": "skip" if resume and existing_summary is not None else (
                "retry" if _attempt_directories(scan_dir, str(planned_identity["experiment_id"])) else "run"
            ),
            "reason": existing_reason,
            "selected_output_dir": str(existing_dir.resolve()) if existing_dir is not None else "",
        }
        decisions.append(decision)
        _write_resume_decisions(scan_dir / "resume_decisions.csv", decisions)
        if resume and existing_summary is not None:
            summaries.append(existing_summary)
            continue

        try:
            result = run_experiment(
                config, demand, controller, seed,
                output_root=scan_dir / "runs", use_gui=use_gui, frozen_network=frozen_network,
            )
        except FrozenNetworkMismatchError as exc:
            context = _exception_context(exc, "network_preflight")
            ledger_row = _failure_ledger_row(demand, controller, seed, context["failure_stage"], exc, context)
            _append_failure_ledger(scan_dir / "failure_ledger.csv", ledger_row)
            if context["output_dir"]:
                attempt_dir = Path(str(context["output_dir"]))
                if attempt_dir.is_dir():
                    _write_artifact_manifest_without_masking(attempt_dir, planned_identity, exc)
                    _rebuild_run_index(scan_dir, planned_items, frozen_network, extra_index_rows)
            raise
        except Exception as exc:
            context = _exception_context(exc, "run_experiment")
            ledger_row = _failure_ledger_row(demand, controller, seed, context["failure_stage"], exc, context)
            _append_failure_ledger(scan_dir / "failure_ledger.csv", ledger_row)
            if context["output_dir"] and Path(str(context["output_dir"])).is_dir():
                _write_artifact_manifest_without_masking(
                    Path(str(context["output_dir"])), planned_identity, exc
                )
            else:
                extra_index_rows.append(_failure_index_row(demand, controller, seed, context, ledger_row))
            summaries.append(
                _invalid_summary(
                    str(context["experiment_id"] or experiment_id(config, demand, controller, seed)),
                    controller,
                    seed,
                    demand,
                    str(context["failure_message"]),
                )
            )
            _rebuild_run_index(scan_dir, planned_items, frozen_network, extra_index_rows)
            continue

        validation_error: BaseException | None = None
        try:
            _write_artifact_manifest(result.output_dir, planned_identity)
            selected_summary, validation_error = _validate_attempt_artifacts(
                result.output_dir, planned_identity, frozen_network
            )
            if selected_summary is None:
                raise validation_error or ValueError("invalid attempt artifacts")
        except Exception as exc:
            context = _exception_context(
                exc,
                "output_parse",
                output_dir=result.output_dir,
                experiment=result.experiment_id,
            )
            ledger_row = _failure_ledger_row(demand, controller, seed, "output_parse", exc, context)
            _append_failure_ledger(scan_dir / "failure_ledger.csv", ledger_row)
            summaries.append(
                _invalid_summary(
                    experiment_id(config, demand, controller, seed),
                    controller,
                    seed,
                    demand,
                    str(context["failure_message"]),
                )
            )
            _rebuild_run_index(scan_dir, planned_items, frozen_network, extra_index_rows)
            continue
        summaries.append(selected_summary)
        decision["selected_output_dir"] = str(Path(result.output_dir).resolve())
        _write_resume_decisions(scan_dir / "resume_decisions.csv", decisions)
        _rebuild_run_index(scan_dir, planned_items, frozen_network, extra_index_rows)

    _rebuild_run_index(scan_dir, planned_items, frozen_network, extra_index_rows)
    _write_resume_decisions(scan_dir / "resume_decisions.csv", decisions)
    classifications = _classify_scan(config, summaries)
    _write_classifications(scan_dir / "demand_classification.csv", classifications)
    return scan_dir


def _allocate_scan_dir(output_root: str | Path, timestamp: str) -> Path:
    output_root = Path(output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    suffix = 0
    while True:
        name = timestamp if suffix == 0 else f"{timestamp}_{suffix:04d}"
        candidate = output_root / name
        try:
            candidate.mkdir(exist_ok=False)
        except FileExistsError:
            suffix += 1
        else:
            return candidate


def _classify_scan(config: ExperimentConfig, summaries: list[EpisodeSummary]) -> list[DemandClassification]:
    classifications = []
    for demand in iter_demand_grid(config):
        no_control = [
            summary
            for summary in summaries
            if summary.controller == "none" and summary.mainline_vph == demand.mainline_vph and summary.ramp_vph == demand.ramp_vph
        ]
        alinea = [
            summary
            for summary in summaries
            if summary.controller == "alinea" and summary.mainline_vph == demand.mainline_vph and summary.ramp_vph == demand.ramp_vph
        ]
        if no_control or alinea:
            classifications.append(
                classify_demand_pair(
                    no_control,
                    alinea,
                    config.free_flow_speed_mps,
                    config.controllable_improvement_threshold,
                    config.no_control_congestion_threshold,
                    config.alinea.ramp_storage_veh,
                )
            )
    return classifications


def _write_index(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "experiment_id", "mainline_vph", "ramp_vph", "controller", "seed", "status", "attempt_status", "valid",
        "output_dir", "failure_reason", "failure_stage", "failure_type", "failure_message",
        "retryable", "attempt", "started_at", "finished_at",
    ]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


_ATTEMPT_REQUIRED_FIELDS = {
    "schema_version", "experiment_id", "status", "valid", "failure_stage",
    "failure_type", "failure_message", "retryable", "attempt", "started_at",
    "finished_at", "output_dir",
}


def _read_attempt_record(
    output_dir: Path,
    expected_experiment_id: str | None = None,
) -> dict[str, object]:
    output_dir = Path(output_dir)
    path = output_dir / "attempt.json"
    record = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(record, dict):
        raise ValueError("attempt record must be a JSON object")

    missing = sorted(_ATTEMPT_REQUIRED_FIELDS - set(record))
    if missing:
        raise ValueError(f"attempt record missing required fields: {', '.join(missing)}")
    if record["schema_version"] != "cr03-attempt-v1":
        raise ValueError("attempt record schema_version must be 'cr03-attempt-v1'")

    string_fields = (
        "experiment_id", "failure_stage", "failure_type", "failure_message",
        "started_at", "finished_at", "output_dir",
    )
    for field in string_fields:
        if not isinstance(record[field], str):
            raise ValueError(f"attempt record {field} must be a string")
    if not record["experiment_id"]:
        raise ValueError("attempt record experiment_id must not be empty")
    if expected_experiment_id is not None and record["experiment_id"] != expected_experiment_id:
        raise ValueError("attempt record experiment_id does not match the run result")

    if not isinstance(record["status"], str) or record["status"] not in {"success", "failed"}:
        raise ValueError("attempt record status must be 'success' or 'failed'")
    for field in ("valid", "retryable"):
        if not isinstance(record[field], bool):
            raise ValueError(f"attempt record {field} must be a boolean")
    if isinstance(record["attempt"], bool) or not isinstance(record["attempt"], int) or record["attempt"] <= 0:
        raise ValueError("attempt record attempt must be a positive integer")

    attempt_suffix = output_dir.name.removeprefix("attempt_")
    if (
        not output_dir.name.startswith("attempt_")
        or len(attempt_suffix) != 4
        or not attempt_suffix.isdigit()
        or int(attempt_suffix) != record["attempt"]
    ):
        raise ValueError("attempt record attempt does not match the attempt_XXXX directory")
    if not record["output_dir"] or Path(record["output_dir"]).resolve() != output_dir.resolve():
        raise ValueError("attempt record output_dir does not match the actual directory")

    try:
        started_at = _UTC_DATETIME.fromisoformat(record["started_at"])
        finished_at = _UTC_DATETIME.fromisoformat(record["finished_at"])
    except (TypeError, ValueError) as exc:
        raise ValueError("attempt record timestamps must be ISO-8601") from exc
    if started_at.utcoffset() is None or finished_at.utcoffset() is None:
        raise ValueError("attempt record timestamps must include a timezone")
    if finished_at < started_at:
        raise ValueError("attempt record finished_at must not precede started_at")

    if record["status"] == "failed":
        if record["valid"] is not False:
            raise ValueError("failed attempt record must have valid=false")
        if not all(record[field] for field in ("failure_stage", "failure_type", "failure_message")):
            raise ValueError("failed attempt record must include failure stage, type, and message")
    else:
        if any(record[field] for field in ("failure_stage", "failure_type", "failure_message")):
            raise ValueError("success attempt record must have empty failure fields")
        if record["retryable"] is not False:
            raise ValueError("success attempt record must have retryable=false")
    return record


def _safe_attempt_metadata(output_dir: Path) -> dict[str, object]:
    try:
        return _read_attempt_record(output_dir)
    except Exception:
        return {}


def _exception_context(
    exc: BaseException,
    default_stage: str,
    *,
    output_dir: Path | None = None,
    experiment: str = "",
) -> dict[str, object]:
    attached = getattr(exc, "failure_context", None)
    if isinstance(attached, dict):
        context = dict(attached)
    else:
        context = {}
    stage = str(context.get("failure_stage") or default_stage)
    resolved_output = str(context.get("output_dir") or (Path(output_dir) if output_dir is not None else ""))
    attempt = context.get("attempt", "")
    if attempt == "" and resolved_output:
        name = Path(resolved_output).name
        if name.startswith("attempt_") and name[len("attempt_"):].isdigit():
            attempt = int(name[len("attempt_"):])
    return {
        "failure_stage": stage,
        "failure_type": str(context.get("failure_type") or type(exc).__name__),
        "failure_message": str(context.get("failure_message") or str(exc)),
        "retryable": context.get("retryable", _retryable_failure(stage, exc)),
        "output_dir": resolved_output,
        "attempt": attempt,
        "experiment_id": str(context.get("experiment_id") or experiment),
    }


def _failure_index_row(
    demand: DemandPoint,
    controller: str,
    seed: int,
    context: dict[str, object],
    ledger_row: dict[str, object],
    attempt: dict[str, object] | None = None,
) -> dict[str, object]:
    attempt = attempt or {}
    return {
        "experiment_id": context["experiment_id"],
        "mainline_vph": demand.mainline_vph,
        "ramp_vph": demand.ramp_vph,
        "controller": controller,
        "seed": seed,
        "status": "invalid",
        "attempt_status": "failed",
        "valid": False,
        "output_dir": context["output_dir"],
        "failure_reason": context["failure_message"],
        "failure_stage": context["failure_stage"],
        "failure_type": context["failure_type"],
        "failure_message": context["failure_message"],
        "retryable": context["retryable"],
        "attempt": attempt.get("attempt", context["attempt"]),
        "started_at": attempt.get("started_at", ""),
        "finished_at": attempt.get("finished_at", ledger_row["timestamp"]),
    }


def _failure_ledger_row(
    demand: DemandPoint | None,
    controller: str,
    seed: int,
    stage: str,
    exc: BaseException,
    context: dict[str, object] | None = None,
) -> dict[str, object]:
    context = context or _exception_context(exc, stage)
    return {
        "mainline_vph": demand.mainline_vph if demand else "",
        "ramp_vph": demand.ramp_vph if demand else "",
        "controller": controller,
        "seed": seed if demand else "",
        "failure_stage": context["failure_stage"],
        "failure_type": context["failure_type"],
        "failure_message": context["failure_message"],
        "retryable": context["retryable"],
        "timestamp": _UTC_DATETIME.now(timezone.utc).isoformat(),
    }


def _append_failure_ledger(path: Path, row: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "mainline_vph", "ramp_vph", "controller", "seed", "failure_stage",
        "failure_type", "failure_message", "retryable", "timestamp",
    ]
    exists = path.exists()
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        if not exists:
            writer.writeheader()
        writer.writerow(row)


def _write_classifications(path: Path, rows: list[DemandClassification]) -> None:
    names = [field.name for field in fields(DemandClassification)]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=names)
        writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))


def _config_from_args(args: argparse.Namespace) -> ExperimentConfig:
    config = default_config()
    if args.smoke:
        config.simulation_duration_s = 180
        config.metrics_interval_s = 30
        config.demand_phases = config.demand_phases[:1]
        config.demand_phases[0].begin_s = 0
        config.demand_phases[0].end_s = 180
        config.mainline_range = (1200, 3600, 2400)
        config.ramp_range = (120, 600, 480)
        config.seeds = (0,)
    if args.mainline_start is not None:
        config.mainline_range = (args.mainline_start, args.mainline_end, args.mainline_step)
    if args.ramp_start is not None:
        config.ramp_range = (args.ramp_start, args.ramp_end, args.ramp_step)
    if args.seeds:
        config.seeds = tuple(int(seed.strip()) for seed in args.seeds.split(",") if seed.strip())
    return config


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--full", action="store_true")
    parser.add_argument("--output-root", default="outputs/scan")
    parser.add_argument("--scan-id")
    parser.add_argument("--mainline-start", type=int)
    parser.add_argument("--mainline-end", type=int, default=6000)
    parser.add_argument("--mainline-step", type=int, default=300)
    parser.add_argument("--ramp-start", type=int)
    parser.add_argument("--ramp-end", type=int, default=1200)
    parser.add_argument("--ramp-step", type=int, default=150)
    parser.add_argument("--seeds", default="")
    args = parser.parse_args()
    scan_dir = scan_demand(_config_from_args(args), output_root=args.output_root, scan_id=args.scan_id)
    print(scan_dir)


if __name__ == "__main__":
    main()
