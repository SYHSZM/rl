import csv

from classify import classify_episode
from metrics import WindowRecord, summarize_episode, write_window_csv


def make_record(time_s, speed, upstream, queue):
    return WindowRecord(
        experiment_id="x",
        controller="none",
        seed=0,
        time_s=time_s,
        mainline_vph=4200,
        ramp_vph=750,
        mean_speed_mps=speed,
        upstream_speed_mps=upstream,
        bottleneck_flow_veh=20,
        bottleneck_occupancy=0.2,
        ramp_queue_veh=queue,
        alinea_requested_rate_vph=0,
        alinea_applied_rate_vph=0,
        departed_veh=100,
        arrived_veh=90,
        teleports=0,
    )


def test_summarize_episode_detects_five_min_congestion():
    records = [make_record(t, 20, 18, 2) for t in range(30, 301, 30)]
    summary = summarize_episode(records, free_flow_speed_mps=33.33, congestion_speed_ratio=0.65, congestion_min_duration_s=300)
    assert summary.valid is True
    assert summary.breakdown_start_s == 30
    assert summary.congestion_duration_s == 300


def test_write_window_csv_uses_stable_header(tmp_path):
    path = tmp_path / "windows.csv"
    write_window_csv(path, [make_record(30, 30, 30, 0)])
    with path.open(newline="", encoding="utf-8") as f:
        header = next(csv.reader(f))
    assert header[:5] == ["experiment_id", "controller", "seed", "time_s", "mainline_vph"]


def test_summary_ignores_missing_speeds_and_missing_upstream_breaks_sequences():
    records = [
        make_record(30, 10.0, 10.0, 99),
        make_record(60, None, None, 99),
        make_record(90, 30.0, 10.0, 99),
        make_record(120, 20.0, 10.0, 99),
    ]
    for record, explicit_max in zip(records, [3, 7, 4, 5]):
        record.ramp_vehicle_max_veh = explicit_max

    summary = summarize_episode(
        records,
        free_flow_speed_mps=33.33,
        congestion_speed_ratio=0.65,
        congestion_min_duration_s=60,
    )

    assert summary.mean_speed_mps == 20.0
    assert summary.breakdown_start_s == 90
    assert summary.congestion_duration_s == 60
    assert summary.recovered is False
    assert summary.max_ramp_queue_veh == 7


def test_summary_sums_only_separate_qualified_low_speed_runs():
    records = [
        make_record(30, 20.0, 10.0, 0),
        make_record(60, 20.0, 10.0, 0),
        make_record(90, 20.0, 30.0, 0),
        make_record(120, 20.0, 10.0, 0),
        make_record(150, 20.0, 30.0, 0),
        make_record(180, 20.0, 10.0, 0),
        make_record(210, 20.0, 10.0, 0),
    ]

    summary = summarize_episode(records, 33.33, 0.65, 60)

    assert summary.breakdown_start_s == 30
    assert summary.congestion_duration_s == 120


def test_summary_without_main_speed_is_invalid_and_classification_safe():
    records = [make_record(30, None, 30.0, 4), make_record(60, None, 30.0, 5)]
    records[0].ramp_vehicle_max_veh = 4
    records[1].ramp_vehicle_max_veh = 5

    summary = summarize_episode(records, 33.33, 0.65, 60)

    assert summary.valid is False
    assert summary.failure_reason == "no_main_speed_observations"
    assert summary.mean_speed_mps is None
    assert summary.bottleneck_throughput_veh == 40
    assert summary.max_ramp_queue_veh == 5
    assert classify_episode(summary, 33.33) == "invalid"


def test_summary_without_upstream_speed_is_invalid_and_classification_safe():
    records = [make_record(30, 30.0, None, 1), make_record(60, 31.0, None, 2)]

    summary = summarize_episode(records, 33.33, 0.65, 60)

    assert summary.valid is False
    assert summary.failure_reason == "no_upstream_speed_observations"
    assert summary.mean_speed_mps == 30.5
    assert summary.breakdown_start_s == 0
    assert summary.congestion_duration_s == 0
    assert summary.recovered is False
    assert classify_episode(summary, 33.33) == "invalid"
