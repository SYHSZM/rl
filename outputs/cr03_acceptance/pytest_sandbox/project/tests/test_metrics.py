import csv
from dataclasses import replace

import env as env_module
from classify import classify_demand_pair, classify_episode
from metrics import WindowRecord, summarize_episode, write_summary_csv, write_window_csv


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


def _six_step_exposure():
    tracker_type = getattr(env_module, "_SystemExposureTracker", None)
    assert tracker_type is not None, "CR-02 requires ID-level system exposure tracking"
    tracker = tracker_type()
    for loaded, departed, arrived in [
        (("main_A", "ramp_B"), ("main_A",), ()),
        ((), (), ()),
        (("main_C",), ("ramp_B",), ("main_A",)),
        ((), ("main_C",), ()),
        ((), (), ("ramp_B",)),
        ((), (), ()),
    ]:
        tracker.add_step(loaded_ids=loaded, departed_ids=departed, arrived_ids=arrived, dt=1.0)
    return tracker.snapshot()


def test_summary_uses_corrected_system_tts_and_preserves_legacy_proxy(tmp_path):
    records = [make_record(30, 30.0, 30.0, 1), make_record(60, 30.0, 30.0, 2)]
    records[0].departed_veh, records[0].arrived_veh = 6, 1
    records[1].departed_veh, records[1].arrived_veh = 10, 4

    summary = summarize_episode(records, 33.33, 0.65, 60, system_exposure=_six_step_exposure())

    assert summary.total_time_spent_s == 10.0
    assert summary.tts_system_s == 10.0
    assert summary.tts_pending_s == 3.0
    assert summary.tts_in_network_s == 7.0
    assert summary.legacy_total_time_spent_s == 330.0
    assert (summary.loaded_veh, summary.departed_veh, summary.arrived_veh) == (3, 3, 2)
    assert summary.actual_departure_ratio == 1.0
    assert summary.completion_ratio == 2 / 3
    assert summary.mainline_system_exposure_s == 6.0
    assert summary.ramp_system_exposure_s == 4.0
    assert summary.unknown_system_exposure_s == 0.0
    assert summary.completed_vehicle_exposure_s == 6.0
    assert summary.terminal_in_network_ids == "main_C"
    assert summary.terminal_pending_ids == ""
    assert summary.terminal_in_network_exposure_s == 4.0
    assert summary.terminal_censoring is True

    path = tmp_path / "summary.csv"
    write_summary_csv(path, [summary])
    with path.open(newline="", encoding="utf-8") as handle:
        row = next(csv.DictReader(handle))
    assert row["summary_schema_version"] == "cr02-summary-v1"
    assert row["terminal_in_network_ids"] == "main_C"
    assert row["terminal_pending_ids"] == ""


def test_classifier_uses_corrected_total_time_spent_without_changes():
    records = [make_record(30, 30.0, 30.0, 0)]
    no_control = summarize_episode(records, 33.33, 0.65, 60, system_exposure=_six_step_exposure())
    alinea = replace(no_control, controller="alinea", total_time_spent_s=5.0, tts_system_s=5.0)

    classification = classify_demand_pair([no_control], [alinea], 33.33, 0.05, 0.8, 80)

    assert classification.paired_tts_improvement == 0.5


def test_zero_loaded_summary_ratios_write_blank(tmp_path):
    tracker = env_module._SystemExposureTracker()
    tracker.add_step(loaded_ids=(), departed_ids=(), arrived_ids=(), dt=1.0)
    summary = summarize_episode(
        [make_record(30, 30.0, 30.0, 0)],
        33.33,
        0.65,
        60,
        system_exposure=tracker.snapshot(),
    )
    path = tmp_path / "summary.csv"

    write_summary_csv(path, [summary])

    with path.open(newline="", encoding="utf-8") as handle:
        row = next(csv.DictReader(handle))
    assert summary.actual_departure_ratio is None
    assert summary.completion_ratio is None
    assert row["actual_departure_ratio"] == ""
    assert row["completion_ratio"] == ""
