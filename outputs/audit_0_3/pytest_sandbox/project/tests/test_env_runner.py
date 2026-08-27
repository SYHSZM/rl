from experiment_config import DemandPoint, default_config
from env import run_experiment


def test_run_experiment_rejects_unknown_controller(tmp_path):
    config = default_config()
    try:
        run_experiment(config, DemandPoint(3000, 300), "bad", 0, output_root=tmp_path)
    except ValueError as exc:
        assert "controller" in str(exc)
    else:
        raise AssertionError("unknown controller should be rejected before SUMO starts")


def test_short_sumo_run_produces_windows(tmp_path):
    config = default_config()
    config.simulation_duration_s = 180
    config.demand_phases = config.demand_phases[:1]
    config.demand_phases[0].begin_s = 0
    config.demand_phases[0].end_s = 180
    result = run_experiment(config, DemandPoint(1200, 120), "none", 0, output_root=tmp_path)
    assert result.valid is True
    assert len(result.window_records) >= 5
    assert (result.output_dir / "windows.csv").exists()
