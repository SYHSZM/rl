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
    mean_speed_mps: float
    upstream_speed_mps: float
    bottleneck_flow_veh: int
    bottleneck_occupancy: float
    ramp_queue_veh: int
    alinea_requested_rate_vph: float
    alinea_applied_rate_vph: float
    departed_veh: int
    arrived_veh: int
    teleports: int


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
    mean_speed_mps: float
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
    low_flags = [record.upstream_speed_mps < low_speed_threshold for record in records]
    breakdown_start_s = _first_run_start(records, low_flags, min_windows)
    congestion_duration_s = sum(interval_s for flag in low_flags if flag) if breakdown_start_s else 0
    recovery_window_count = max(1, 300 // interval_s)
    recovery_records = records[-recovery_window_count:]
    recovered = all(record.upstream_speed_mps >= free_flow_speed_mps * 0.85 for record in recovery_records)

    return EpisodeSummary(
        experiment_id=records[0].experiment_id,
        controller=records[0].controller,
        seed=records[0].seed,
        mainline_vph=records[0].mainline_vph,
        ramp_vph=records[0].ramp_vph,
        valid=True,
        failure_reason="",
        total_time_spent_s=float(sum(max(0, record.departed_veh - record.arrived_veh) * interval_s for record in records)),
        mean_speed_mps=sum(record.mean_speed_mps for record in records) / len(records),
        bottleneck_throughput_veh=sum(record.bottleneck_flow_veh for record in records),
        max_ramp_queue_veh=max(record.ramp_queue_veh for record in records),
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


def _first_run_start(records: list[WindowRecord], flags: list[bool], min_windows: int) -> int:
    run_start = 0
    run_length = 0
    for index, flag in enumerate(flags):
        if flag:
            if run_length == 0:
                run_start = records[index].time_s
            run_length += 1
            if run_length >= min_windows:
                return run_start
        else:
            run_length = 0
    return 0


def _write_dataclass_csv(path: str | Path, row_type: type, rows: list[object]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    names = [field.name for field in fields(row_type)]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=names)
        writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))
