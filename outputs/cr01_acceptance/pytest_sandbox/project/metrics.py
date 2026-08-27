from __future__ import annotations

import csv
from dataclasses import asdict, dataclass, fields
from pathlib import Path


@dataclass
class WindowRecord:
    experiment_id: str
    controller: str
    seed: int
    time_s: int
    mainline_vph: int
    ramp_vph: int
    mean_speed_mps: float | None
    upstream_speed_mps: float | None
    bottleneck_flow_veh: int
    bottleneck_occupancy: float
    ramp_queue_veh: int
    alinea_requested_rate_vph: float
    alinea_applied_rate_vph: float
    departed_veh: int
    arrived_veh: int
    teleports: int
    metric_schema_version: str = "cr01-window-v1"
    estimator_id: str = "traci-1s-step-complete-window"
    window_step_observations: int = 0
    main_speed_vehicle_observations: int = 0
    upstream_speed_vehicle_observations: int = 0
    ramp_vehicle_mean_veh: float = 0.0
    ramp_vehicle_max_veh: int = 0
    ramp_halting_mean_veh: float = 0.0
    ramp_halting_max_veh: int = 0


@dataclass
class EpisodeSummary:
    experiment_id: str
    controller: str
    seed: int
    mainline_vph: int
    ramp_vph: int
    valid: bool
    failure_reason: str
    total_time_spent_s: float
    mean_speed_mps: float | None
    bottleneck_throughput_veh: int
    max_ramp_queue_veh: int
    breakdown_start_s: int
    congestion_duration_s: int
    recovered: bool
    teleports: int


def summarize_episode(
    records: list[WindowRecord],
    free_flow_speed_mps: float,
    congestion_speed_ratio: float,
    congestion_min_duration_s: int,
) -> EpisodeSummary:
    if not records:
        return EpisodeSummary(
            experiment_id="",
            controller="",
            seed=0,
            mainline_vph=0,
            ramp_vph=0,
            valid=False,
            failure_reason="no_window_records",
            total_time_spent_s=0.0,
            mean_speed_mps=0.0,
            bottleneck_throughput_veh=0,
            max_ramp_queue_veh=0,
            breakdown_start_s=0,
            congestion_duration_s=0,
            recovered=False,
            teleports=0,
        )

    interval_s = _infer_interval_s(records)
    low_speed_threshold = free_flow_speed_mps * congestion_speed_ratio
    min_windows = max(1, congestion_min_duration_s // interval_s)
    low_flags = [
        None if record.upstream_speed_mps is None else record.upstream_speed_mps < low_speed_threshold
        for record in records
    ]
    breakdown_start_s, congestion_duration_s = _qualified_low_runs(records, low_flags, min_windows, interval_s)
    recovery_window_count = max(1, 300 // interval_s)
    recovery_records = records[-recovery_window_count:]
    recovered = all(
        record.upstream_speed_mps is not None and record.upstream_speed_mps >= free_flow_speed_mps * 0.85
        for record in recovery_records
    )
    valid_mean_speeds = [record.mean_speed_mps for record in records if record.mean_speed_mps is not None]
    valid_upstream_speeds = [record.upstream_speed_mps for record in records if record.upstream_speed_mps is not None]
    if not valid_mean_speeds:
        valid = False
        failure_reason = "no_main_speed_observations"
    elif not valid_upstream_speeds:
        valid = False
        failure_reason = "no_upstream_speed_observations"
    else:
        valid = True
        failure_reason = ""

    return EpisodeSummary(
        experiment_id=records[0].experiment_id,
        controller=records[0].controller,
        seed=records[0].seed,
        mainline_vph=records[0].mainline_vph,
        ramp_vph=records[0].ramp_vph,
        valid=valid,
        failure_reason=failure_reason,
        total_time_spent_s=float(sum(max(0, record.departed_veh - record.arrived_veh) * interval_s for record in records)),
        mean_speed_mps=sum(valid_mean_speeds) / len(valid_mean_speeds) if valid_mean_speeds else None,
        bottleneck_throughput_veh=sum(record.bottleneck_flow_veh for record in records),
        max_ramp_queue_veh=max(record.ramp_vehicle_max_veh for record in records),
        breakdown_start_s=breakdown_start_s,
        congestion_duration_s=congestion_duration_s,
        recovered=recovered,
        teleports=max(record.teleports for record in records),
    )


def write_window_csv(path: str | Path, records: list[WindowRecord]) -> None:
    _write_dataclass_csv(path, WindowRecord, records)


def write_summary_csv(path: str | Path, summaries: list[EpisodeSummary]) -> None:
    _write_dataclass_csv(path, EpisodeSummary, summaries)


def _infer_interval_s(records: list[WindowRecord]) -> int:
    if len(records) < 2:
        return 30
    return int(records[1].time_s - records[0].time_s)


def _qualified_low_runs(
    records: list[WindowRecord],
    flags: list[bool | None],
    min_windows: int,
    interval_s: int,
) -> tuple[int, int]:
    qualified_runs: list[tuple[int, int]] = []
    run_start_index: int | None = None
    for index in range(len(flags) + 1):
        flag = flags[index] if index < len(flags) else False
        if flag is True:
            if run_start_index is None:
                run_start_index = index
            continue
        if run_start_index is not None:
            run_length = index - run_start_index
            if run_length >= min_windows:
                qualified_runs.append((run_start_index, run_length))
            run_start_index = None
    if not qualified_runs:
        return 0, 0
    breakdown_start_s = records[qualified_runs[0][0]].time_s
    congestion_duration_s = sum(run_length * interval_s for _, run_length in qualified_runs)
    return breakdown_start_s, congestion_duration_s


def _write_dataclass_csv(path: str | Path, row_type: type, rows: list[object]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    names = [field.name for field in fields(row_type)]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=names)
        writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))
