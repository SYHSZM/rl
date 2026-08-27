from types import SimpleNamespace

import pytest

import build_network as build_network_module
from experiment_config import default_config
from metrics import EpisodeSummary
import scan_demand as scan_demand_module
from scan_demand import planned_runs


def test_planned_runs_pairs_controllers_by_demand_and_seed():
    config = default_config()
    config.mainline_range = (3000, 3000, 300)
    config.ramp_range = (300, 450, 150)
    config.seeds = (0, 1)
    runs = planned_runs(config, ("none", "alinea"))
    assert len(runs) == 8
    assert runs[0][1:] == ("none", 0)
    assert runs[1][1:] == ("alinea", 0)


def test_scan_demand_builds_one_batch_preflight_and_reuses_it(tmp_path, monkeypatch):
    config = default_config()
    config.mainline_range = (1200, 1200, 1)
    config.ramp_range = (120, 120, 1)
    config.seeds = (0,)
    frozen = object()
    preflight_paths = []
    received_frozen = []

    def fake_preflight(path):
        preflight_paths.append(path)
        return frozen

    def fake_run(config, demand, controller, seed, output_root, use_gui, frozen_network=None):
        received_frozen.append(frozen_network)
        summary = EpisodeSummary(
            experiment_id="fake",
            controller=controller,
            seed=seed,
            mainline_vph=demand.mainline_vph,
            ramp_vph=demand.ramp_vph,
            valid=True,
            failure_reason="",
            total_time_spent_s=0.0,
            mean_speed_mps=0.0,
            bottleneck_throughput_veh=0,
            max_ramp_queue_veh=0,
            breakdown_start_s=0,
            congestion_duration_s=0,
            recovered=True,
            teleports=0,
        )
        return SimpleNamespace(experiment_id="fake", valid=True, output_dir=tmp_path / "fake", failure_reason="", summary=summary)

    monkeypatch.setattr(scan_demand_module, "preflight_network", fake_preflight, raising=False)
    monkeypatch.setattr(scan_demand_module, "run_experiment", fake_run)
    scan_dir = scan_demand_module.scan_demand(config, output_root=tmp_path / "scan", controllers=("none", "alinea"), resume=False)

    assert len(preflight_paths) == 1
    assert preflight_paths[0] == scan_dir / "preflight" / "merge.net.xml"
    assert received_frozen == [frozen, frozen]


def test_scan_directory_allocation_is_collision_safe_for_same_timestamp(tmp_path, monkeypatch):
    class FixedNow:
        def strftime(self, _format):
            return "20260807_120000"

    class FixedDatetime:
        @classmethod
        def now(cls):
            return FixedNow()

    config = default_config()
    config.mainline_range = (1200, 1200, 1)
    config.ramp_range = (120, 120, 1)
    config.seeds = (0,)
    frozen = object()

    def fake_run(config, demand, controller, seed, output_root, use_gui, frozen_network=None):
        summary = EpisodeSummary(
            experiment_id="fake", controller=controller, seed=seed,
            mainline_vph=demand.mainline_vph, ramp_vph=demand.ramp_vph,
            valid=True, failure_reason="", total_time_spent_s=0.0,
            mean_speed_mps=0.0, bottleneck_throughput_veh=0,
            max_ramp_queue_veh=0, breakdown_start_s=0,
            congestion_duration_s=0, recovered=True, teleports=0,
        )
        return SimpleNamespace(experiment_id="fake", valid=True, output_dir=Path(output_root) / "fake", failure_reason="", summary=summary)

    from pathlib import Path
    monkeypatch.setattr(scan_demand_module, "datetime", FixedDatetime)
    monkeypatch.setattr(scan_demand_module, "preflight_network", lambda path: frozen)
    monkeypatch.setattr(scan_demand_module, "run_experiment", fake_run)
    first = scan_demand_module.scan_demand(config, output_root=tmp_path / "scan", controllers=("none",), resume=False)
    second = scan_demand_module.scan_demand(config, output_root=tmp_path / "scan", controllers=("none",), resume=False)

    assert first.name == "20260807_120000"
    assert second.name == "20260807_120000_0001"
    assert first != second


def test_scan_aborts_batch_immediately_on_frozen_mismatch(tmp_path, monkeypatch):
    config = default_config()
    config.mainline_range = (1200, 1200, 1)
    config.ramp_range = (120, 120, 1)
    config.seeds = (0,)
    calls = []
    monkeypatch.setattr(scan_demand_module, "preflight_network", lambda path: object())

    def mismatch(*args, **kwargs):
        calls.append((args, kwargs))
        raise build_network_module.FrozenNetworkMismatchError("stop batch")

    monkeypatch.setattr(scan_demand_module, "run_experiment", mismatch)
    with pytest.raises(build_network_module.FrozenNetworkMismatchError, match="stop batch"):
        scan_demand_module.scan_demand(config, output_root=tmp_path / "scan", controllers=("none", "alinea"), resume=False)

    assert len(calls) == 1
