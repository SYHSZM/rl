from __future__ import annotations

import argparse
import csv
import json
from dataclasses import asdict, fields
from datetime import datetime, timezone
from pathlib import Path

from build_network import FrozenNetworkMismatchError, preflight_network
from classify import DemandClassification, classify_demand_pair
from env import _invalid_summary, _retryable_failure, run_experiment
from experiment_config import DemandPoint, ExperimentConfig, default_config, experiment_id, iter_demand_grid, validate_config
from metrics import EpisodeSummary


_UTC_DATETIME = datetime


def planned_runs(config: ExperimentConfig, controllers: tuple[str, ...]) -> list[tuple[DemandPoint, str, int]]:
    validate_config(config)
    return [(demand, controller, seed) for demand in iter_demand_grid(config) for seed in config.seeds for controller in controllers]


def scan_demand(
    config: ExperimentConfig | None = None,
    output_root: str | Path = "outputs/scan",
    controllers: tuple[str, ...] = ("none", "alinea"),
    resume: bool = True,
    use_gui: bool = False,
) -> Path:
    config = config or default_config()
    validate_config(config)
    scan_dir = _allocate_scan_dir(output_root, datetime.now().strftime("%Y%m%d_%H%M%S"))
    index_rows: list[dict[str, object]] = []
    summaries: list[EpisodeSummary] = []
    try:
        frozen_network = preflight_network(scan_dir / "preflight" / "merge.net.xml")
    except Exception as exc:
        _append_failure_ledger(
            scan_dir / "failure_ledger.csv",
            _failure_ledger_row(None, "", 0, "network_preflight", exc),
        )
        raise

    for demand, controller, seed in planned_runs(config, controllers):
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
                attempt = _safe_attempt_metadata(Path(str(context["output_dir"])))
                index_rows.append(_failure_index_row(demand, controller, seed, context, ledger_row, attempt))
                _write_index(scan_dir / "run_index.csv", index_rows)
            raise
        except Exception as exc:
            context = _exception_context(exc, "run_experiment")
            ledger_row = _failure_ledger_row(demand, controller, seed, context["failure_stage"], exc, context)
            _append_failure_ledger(scan_dir / "failure_ledger.csv", ledger_row)
            attempt = _safe_attempt_metadata(Path(str(context["output_dir"]))) if context["output_dir"] else {}
            index_rows.append(_failure_index_row(demand, controller, seed, context, ledger_row, attempt))
            summaries.append(
                _invalid_summary(
                    str(context["experiment_id"] or experiment_id(config, demand, controller, seed)),
                    controller,
                    seed,
                    demand,
                    str(context["failure_message"]),
                )
            )
            _write_index(scan_dir / "run_index.csv", index_rows)
            continue

        try:
            attempt = _read_attempt_record(result.output_dir, expected_experiment_id=result.experiment_id)
        except Exception as exc:
            context = _exception_context(
                exc,
                "output_parse",
                output_dir=result.output_dir,
                experiment=result.experiment_id,
            )
            ledger_row = _failure_ledger_row(demand, controller, seed, "output_parse", exc, context)
            _append_failure_ledger(scan_dir / "failure_ledger.csv", ledger_row)
            index_rows.append(_failure_index_row(demand, controller, seed, context, ledger_row))
            summaries.append(
                _invalid_summary(
                    experiment_id(config, demand, controller, seed),
                    controller,
                    seed,
                    demand,
                    str(context["failure_message"]),
                )
            )
            _write_index(scan_dir / "run_index.csv", index_rows)
            continue
        index_rows.append(
            {
                "experiment_id": result.experiment_id,
                "mainline_vph": demand.mainline_vph,
                "ramp_vph": demand.ramp_vph,
                "controller": controller,
                "seed": seed,
                "status": "valid" if result.valid else "invalid",
                "attempt_status": attempt.get("status", "success" if result.valid else "failed"),
                "valid": result.valid,
                "output_dir": str(result.output_dir),
                "failure_reason": result.failure_reason,
                "failure_stage": attempt.get("failure_stage", ""),
                "failure_type": attempt.get("failure_type", ""),
                "failure_message": attempt.get("failure_message", result.failure_reason),
                "retryable": attempt.get("retryable", False),
                "attempt": attempt.get("attempt", ""),
                "started_at": attempt.get("started_at", ""),
                "finished_at": attempt.get("finished_at", ""),
            }
        )
        summaries.append(result.summary)
        _write_index(scan_dir / "run_index.csv", index_rows)

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
    parser.add_argument("--mainline-start", type=int)
    parser.add_argument("--mainline-end", type=int, default=6000)
    parser.add_argument("--mainline-step", type=int, default=300)
    parser.add_argument("--ramp-start", type=int)
    parser.add_argument("--ramp-end", type=int, default=1200)
    parser.add_argument("--ramp-step", type=int, default=150)
    parser.add_argument("--seeds", default="")
    args = parser.parse_args()
    scan_dir = scan_demand(_config_from_args(args), output_root=args.output_root)
    print(scan_dir)


if __name__ == "__main__":
    main()
