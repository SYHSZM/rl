import csv

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
