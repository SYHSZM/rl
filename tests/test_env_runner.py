import csv
import hashlib
import json
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

import build_network as build_network_module
import env as env_module
from experiment_config import DemandPoint, default_config
from env import run_experiment


def _short_config(duration_s=30):
    config = default_config()
    config.simulation_duration_s = duration_s
    config.metrics_interval_s = 30
    config.demand_phases = config.demand_phases[:1]
    config.demand_phases[0].begin_s = 0
    config.demand_phases[0].end_s = duration_s
    return config


def _fake_frozen(tmp_path, monkeypatch):
    frozen = SimpleNamespace(
        net_path=tmp_path / "frozen.net.xml",
        net_sha256="frozen",
        source_dir=build_network_module.NETWORK_DIR,
        source_sha256={},
    )
    monkeypatch.setattr(env_module, "verify_frozen_network", lambda value: value.net_path)
    return frozen


def _read_failure_artifacts(output_root):
    attempts = list(Path(output_root).rglob("attempt.json"))
    summaries = list(Path(output_root).rglob("summary.csv"))
    assert len(attempts) == len(summaries) == 1
    attempt = json.loads(attempts[0].read_text(encoding="utf-8"))
    with summaries[0].open(newline="", encoding="utf-8") as handle:
        summary = next(csv.DictReader(handle))
    return attempt, summary


def _assert_failed_attempt(attempt, summary, stage, retryable):
    assert attempt["schema_version"] == "cr03-attempt-v1"
    assert attempt["status"] == "failed"
    assert attempt["valid"] is False
    assert attempt["failure_stage"] == stage
    assert attempt["failure_type"] == "RuntimeError"
    assert attempt["failure_message"] == f"injected {stage}"
    assert attempt["retryable"] is retryable
    assert attempt["attempt"] == 1
    assert Path(attempt["output_dir"]).name == "attempt_0001"
    assert datetime.fromisoformat(attempt["started_at"]).utcoffset() is not None
    assert datetime.fromisoformat(attempt["finished_at"]).utcoffset() is not None
    assert summary["valid"] == "False"
    assert summary["failure_reason"] == f"injected {stage}"


def test_run_experiment_rejects_unknown_controller(tmp_path):
    config = default_config()
    try:
        run_experiment(config, DemandPoint(3000, 300), "bad", 0, output_root=tmp_path)
    except ValueError as exc:
        assert "controller" in str(exc)
    else:
        raise AssertionError("unknown controller should be rejected before SUMO starts")


def test_short_sumo_run_produces_windows(tmp_path):
    preflight_network = getattr(build_network_module, "preflight_network", None)
    assert preflight_network is not None, "CR-05 requires explicit frozen-network preflight"
    frozen = preflight_network(tmp_path / "preflight" / "merge.net.xml")
    result = run_experiment(_short_config(180), DemandPoint(1200, 120), "none", 0, output_root=tmp_path / "runs", frozen_network=frozen)
    assert result.valid is True
    assert len(result.window_records) >= 5
    assert (result.output_dir / "windows.csv").exists()
    first = result.window_records[0]
    assert first.metric_schema_version == "cr02-window-v1"
    assert first.window_step_observations == 30
    assert first.estimator_id == "traci-1s-step-complete-window"
    assert first.main_speed_vehicle_observations > 0
    assert first.ramp_vehicle_mean_veh <= first.ramp_vehicle_max_veh
    assert first.ramp_halting_mean_veh <= first.ramp_halting_max_veh
    assert len(list((result.output_dir / "native").glob("*.xml"))) == 8
    assert first.loaded_veh >= first.departed_veh >= first.arrived_veh
    assert first.pending_veh >= 0 and first.in_network_veh >= 0
    exposure_path = result.output_dir / "system_exposure.json"
    assert exposure_path.exists()
    exposure = json.loads(exposure_path.read_text(encoding="utf-8"))
    assert exposure["schema_version"] == "cr02-system-exposure-v1"
    assert exposure["terminal"]["in_network_ids"] == sorted(exposure["terminal"]["in_network_ids"])
    with (result.output_dir / "summary.csv").open(newline="", encoding="utf-8") as handle:
        summary_header = next(csv.reader(handle))
    assert "tts_system_s" in summary_header
    assert "legacy_total_time_spent_s" in summary_header
    attempt = json.loads((result.output_dir / "attempt.json").read_text(encoding="utf-8"))
    assert attempt["schema_version"] == "cr03-attempt-v1"
    assert attempt["status"] == "success"
    assert attempt["valid"] is True
    assert attempt["failure_stage"] == ""
    assert attempt["failure_type"] == ""
    assert attempt["failure_message"] == ""
    assert attempt["retryable"] is False


def test_network_preflight_failure_writes_invalid_attempt(tmp_path, monkeypatch):
    monkeypatch.setattr(
        env_module,
        "preflight_network",
        lambda path: (_ for _ in ()).throw(RuntimeError("injected network_preflight")),
    )

    result = env_module.run_experiment(_short_config(), DemandPoint(1200, 120), "none", 0, tmp_path / "runs")

    assert result.valid is False
    attempt, summary = _read_failure_artifacts(tmp_path / "runs")
    _assert_failed_attempt(attempt, summary, "network_preflight", False)


def test_traci_import_failure_writes_invalid_attempt(tmp_path, monkeypatch):
    frozen = _fake_frozen(tmp_path, monkeypatch)
    monkeypatch.setattr(
        env_module,
        "_import_traci",
        lambda: (_ for _ in ()).throw(RuntimeError("injected traci_import")),
    )

    result = env_module.run_experiment(_short_config(), DemandPoint(1200, 120), "none", 0, tmp_path / "runs", frozen_network=frozen)

    assert result.valid is False
    attempt, summary = _read_failure_artifacts(tmp_path / "runs")
    _assert_failed_attempt(attempt, summary, "traci_import", False)


def test_sumo_start_failure_writes_attempt_without_global_close(tmp_path, monkeypatch):
    frozen = _fake_frozen(tmp_path, monkeypatch)
    fake_traci = SimpleNamespace(
        start=lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("injected sumo_start")),
        close=lambda: (_ for _ in ()).throw(AssertionError("global traci.close must not be called")),
    )
    monkeypatch.setattr(env_module, "_import_traci", lambda: fake_traci)

    result = env_module.run_experiment(_short_config(), DemandPoint(1200, 120), "none", 0, tmp_path / "runs", frozen_network=frozen)

    assert result.valid is False
    attempt, summary = _read_failure_artifacts(tmp_path / "runs")
    _assert_failed_attempt(attempt, summary, "sumo_start", True)


def test_run_loop_failure_closes_only_acquired_connection(tmp_path, monkeypatch):
    frozen = _fake_frozen(tmp_path, monkeypatch)
    closed = []

    class FakeConnection:
        def simulationStep(self):
            raise RuntimeError("injected run_loop")

        def close(self):
            closed.append("connection")

    connection = FakeConnection()
    fake_traci = SimpleNamespace(
        start=lambda *args, **kwargs: None,
        getConnection=lambda label: connection,
        close=lambda: (_ for _ in ()).throw(AssertionError("global traci.close must not be called")),
    )
    monkeypatch.setattr(env_module, "_import_traci", lambda: fake_traci)
    monkeypatch.setattr(env_module, "_detector_occupancy", lambda conn, detector_id: 0.0)
    monkeypatch.setattr(env_module, "_ramp_queue", lambda conn: 0)
    monkeypatch.setattr(env_module, "_apply_ramp_signal", lambda *args: None)

    result = env_module.run_experiment(_short_config(), DemandPoint(1200, 120), "none", 0, tmp_path / "runs", frozen_network=frozen)

    assert result.valid is False
    assert closed == ["connection"]
    attempt, summary = _read_failure_artifacts(tmp_path / "runs")
    _assert_failed_attempt(attempt, summary, "run_loop", True)


def test_permanent_attempt_record_failure_preserves_original_output_context(tmp_path, monkeypatch):
    frozen = build_network_module.preflight_network(tmp_path / "preflight" / "merge.net.xml")
    monkeypatch.setattr(
        env_module,
        "write_window_csv",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("original output write failed")),
    )
    monkeypatch.setattr(
        env_module,
        "_write_attempt_record",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("attempt record permanently unavailable")),
    )

    with pytest.raises(Exception) as caught:
        env_module.run_experiment(
            _short_config(), DemandPoint(1200, 120), "none", 0,
            tmp_path / "runs", frozen_network=frozen,
        )

    context = getattr(caught.value, "failure_context", None)
    assert context is not None
    assert context["failure_stage"] == "output_write"
    assert context["failure_type"] == "RuntimeError"
    assert context["failure_message"] == "original output write failed"
    assert context["retryable"] is True
    assert Path(context["output_dir"]).name == "attempt_0001"


def test_complete_window_accumulator_uses_all_thirty_post_step_samples():
    accumulator_type = getattr(env_module, "_WindowAccumulator", None)
    assert accumulator_type is not None, "CR-01 requires a complete-window accumulator"
    accumulator = accumulator_type()
    detector_ids = [f"det_main_{index}" for index in range(5)]

    for step in range(30):
        mainline = {detector_id: (0, -1.0) for detector_id in detector_ids}
        if step == 4:
            mainline["det_main_0"] = (2, 10.0)
        if step == 17:
            mainline["det_main_3"] = (3, 20.0)
        if step == 8:
            mainline["det_main_2"] = (4, -1.0)
        accumulator.add_step(
            mainline_measurements=mainline,
            bottleneck_count=2 if step == 3 else 3 if step == 20 else 0,
            bottleneck_occupancy=0.0 if step < 15 else 0.2,
            ramp_vehicle_count=step // 10,
            ramp_halting_count=0 if step < 15 else 1,
        )

    record = accumulator.to_record(
        experiment_id="synthetic",
        controller="none",
        seed=0,
        time_s=30,
        demand=DemandPoint(1200, 120),
        action=env_module.ControlAction(900.0, 900.0, 30, 7.5),
        departed_total=11,
        arrived_total=1,
        teleports_total=2,
    )

    assert record.window_step_observations == 30
    assert record.main_speed_vehicle_observations == 5
    assert record.upstream_speed_vehicle_observations == 3
    assert record.mean_speed_mps == pytest.approx(16.0)
    assert record.upstream_speed_mps == pytest.approx(20.0)
    assert record.bottleneck_flow_veh == 5
    assert record.bottleneck_occupancy == pytest.approx(0.1)
    assert record.ramp_vehicle_mean_veh == pytest.approx(1.0)
    assert record.ramp_vehicle_max_veh == 2
    assert record.ramp_halting_mean_veh == pytest.approx(0.5)
    assert record.ramp_halting_max_veh == 1
    assert record.ramp_queue_veh == record.ramp_vehicle_max_veh
    assert (record.departed_veh, record.arrived_veh, record.teleports) == (11, 1, 2)


def test_empty_accumulator_window_writes_blank_speeds_with_zero_observations(tmp_path):
    accumulator = env_module._WindowAccumulator()
    exposure_tracker_type = getattr(env_module, "_SystemExposureTracker", None)
    assert exposure_tracker_type is not None, "CR-02 requires ID-level system exposure tracking"
    exposure_tracker = exposure_tracker_type()
    empty_mainline = {f"det_main_{index}": (0, -1.0) for index in range(5)}
    for _ in range(30):
        accumulator.add_step(
            mainline_measurements=empty_mainline,
            bottleneck_count=0,
            bottleneck_occupancy=0.0,
            ramp_vehicle_count=0,
            ramp_halting_count=0,
        )
        exposure_tracker.add_step(loaded_ids=(), departed_ids=(), arrived_ids=(), dt=1.0)
    record = accumulator.to_record(
        experiment_id="empty",
        controller="none",
        seed=0,
        time_s=30,
        demand=DemandPoint(1200, 120),
        action=env_module.ControlAction(900.0, 900.0, 30, 7.5),
        departed_total=0,
        arrived_total=0,
        teleports_total=0,
        system_exposure=exposure_tracker.snapshot(),
    )
    path = tmp_path / "windows.csv"

    env_module.write_window_csv(path, [record])

    with path.open(newline="", encoding="utf-8") as handle:
        row = next(csv.DictReader(handle))
    assert record.mean_speed_mps is None
    assert record.upstream_speed_mps is None
    assert record.main_speed_vehicle_observations == 0
    assert record.upstream_speed_vehicle_observations == 0
    assert row["mean_speed_mps"] == ""
    assert row["upstream_speed_mps"] == ""
    assert row["main_speed_vehicle_observations"] == "0"
    assert row["upstream_speed_vehicle_observations"] == "0"
    assert record.loaded_veh == record.pending_veh == record.in_network_veh == 0
    assert record.actual_departure_ratio is None
    assert record.completion_ratio is None
    assert row["actual_departure_ratio"] == ""
    assert row["completion_ratio"] == ""


def test_six_step_id_trajectory_integrates_system_exposure_exactly():
    tracker_type = getattr(env_module, "_SystemExposureTracker", None)
    assert tracker_type is not None, "CR-02 requires ID-level system exposure tracking"
    tracker = tracker_type()
    trajectory = [
        (("main_A", "ramp_B"), ("main_A",), ()),
        ((), (), ()),
        (("main_C",), ("ramp_B",), ("main_A",)),
        ((), ("main_C",), ()),
        ((), (), ("ramp_B",)),
        ((), (), ()),
    ]
    for loaded, departed, arrived in trajectory:
        tracker.add_step(loaded_ids=loaded, departed_ids=departed, arrived_ids=arrived, dt=1.0)

    exposure = tracker.snapshot()

    assert exposure["counts"] == {"loaded": 3, "departed": 3, "arrived": 2}
    assert exposure["ratios"]["actual_departure"] == 1.0
    assert exposure["ratios"]["completion"] == pytest.approx(2 / 3)
    assert exposure["tts"] == {
        "system_s": 10.0,
        "pending_s": 3.0,
        "in_network_s": 7.0,
        "mainline_s": 6.0,
        "ramp_s": 4.0,
        "unknown_s": 0.0,
        "completed_vehicle_s": 6.0,
    }
    assert exposure["per_id"]["main_A"] == {"origin": "mainline", "pending_s": 0.0, "in_network_s": 2.0, "system_s": 2.0}
    assert exposure["per_id"]["ramp_B"] == {"origin": "ramp", "pending_s": 2.0, "in_network_s": 2.0, "system_s": 4.0}
    assert exposure["per_id"]["main_C"] == {"origin": "mainline", "pending_s": 1.0, "in_network_s": 3.0, "system_s": 4.0}
    assert exposure["terminal"] == {
        "in_network_ids": ["main_C"],
        "pending_ids": [],
        "in_network_count": 1,
        "pending_count": 0,
        "in_network_exposure_s": 4.0,
        "pending_exposure_s": 0.0,
        "censoring": True,
    }


def test_unknown_vehicle_prefix_is_reported_separately():
    tracker_type = getattr(env_module, "_SystemExposureTracker", None)
    assert tracker_type is not None
    tracker = tracker_type()
    tracker.add_step(loaded_ids=("other_1",), departed_ids=("other_1",), arrived_ids=(), dt=1.0)

    exposure = tracker.snapshot()

    assert exposure["tts"]["unknown_s"] == 1.0
    assert exposure["unknown_ids"] == ["other_1"]
    assert exposure["per_id"]["other_1"]["origin"] == "unknown"


def test_run_local_additional_preserves_detector_layout_and_redirects_outputs(tmp_path):
    create_run_additional = getattr(env_module, "create_run_additional", None)
    assert create_run_additional is not None, "CR-05 requires run-local additional XML"
    run_dir = tmp_path / "run"

    additional_path = create_run_additional(run_dir)

    root = ET.parse(additional_path).getroot()
    expected = {
        "det_main_0": ("main_0_1", "500", "30"),
        "det_main_1": ("main_1_1", "500", "30"),
        "det_main_2": ("main_2_1", "500", "30"),
        "det_main_3": ("main_3_1", "500", "30"),
        "det_main_4": ("main_4_1", "500", "30"),
        "det_bottleneck_down": ("main_4_1", "150", "30"),
        "det_ramp_queue": ("ramp_0_0", "0", "30"),
        "det_ramp_arrival": ("ramp_0_0", "40", "30"),
    }
    detectors = {element.attrib["id"]: element for element in root if "id" in element.attrib}
    assert set(detectors) == set(expected)
    for detector_id, (lane, pos, frequency) in expected.items():
        element = detectors[detector_id]
        assert (element.attrib["lane"], element.attrib["pos"], element.attrib["freq"]) == (lane, pos, frequency)
        configured_path = Path(element.attrib["file"])
        output_path = (additional_path.parent / configured_path).resolve() if not configured_path.is_absolute() else configured_path.resolve()
        assert output_path.parent == (run_dir / "native").resolve()


def test_run_experiment_with_frozen_network_never_rebuilds_shared_network(tmp_path, monkeypatch):
    preflight_network = getattr(build_network_module, "preflight_network", None)
    assert preflight_network is not None
    frozen = preflight_network(tmp_path / "preflight" / "merge.net.xml")

    def forbidden_rebuild(*args, **kwargs):
        raise AssertionError("run_experiment must not rebuild a frozen network")

    monkeypatch.setattr(env_module, "build_network", forbidden_rebuild)
    result = env_module.run_experiment(
        _short_config(180),
        DemandPoint(1200, 120),
        "none",
        0,
        output_root=tmp_path / "runs",
        frozen_network=frozen,
    )
    assert result.valid is True


def test_two_output_roots_have_disjoint_generated_files(tmp_path):
    preflight_network = getattr(build_network_module, "preflight_network", None)
    assert preflight_network is not None
    frozen = preflight_network(tmp_path / "preflight" / "merge.net.xml")
    source_hashes_before = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in build_network_module.NETWORK_DIR.glob("*.xml")
    }
    first = env_module.run_experiment(_short_config(180), DemandPoint(1200, 120), "none", 0, output_root=tmp_path / "first", frozen_network=frozen)
    second = env_module.run_experiment(_short_config(180), DemandPoint(1200, 120), "none", 0, output_root=tmp_path / "second", frozen_network=frozen)

    assert first.valid is True and second.valid is True
    first_native = {path.resolve() for path in (first.output_dir / "native").glob("*.xml")}
    second_native = {path.resolve() for path in (second.output_dir / "native").glob("*.xml")}
    assert len(first_native) == len(second_native) == 8
    assert first_native.isdisjoint(second_native)
    for result in (first, second):
        assert {"config.json", "merge.rou.xml", "run.add.xml", "tripinfo.xml", "sumo.log", "sumo_error.log", "windows.csv", "summary.csv"}.issubset(
            {path.name for path in result.output_dir.iterdir() if path.is_file()}
        )
    source_hashes_after = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in build_network_module.NETWORK_DIR.glob("*.xml")
    }
    assert source_hashes_after == source_hashes_before


def test_same_root_same_id_runs_allocate_disjoint_attempts_without_overwrite(tmp_path):
    frozen = build_network_module.preflight_network(tmp_path / "preflight" / "merge.net.xml")
    output_root = tmp_path / "runs"
    first = env_module.run_experiment(_short_config(), DemandPoint(1200, 120), "none", 0, output_root=output_root, frozen_network=frozen)
    first_hashes = {
        str(path.relative_to(first.output_dir)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in first.output_dir.rglob("*")
        if path.is_file()
    }

    second = env_module.run_experiment(_short_config(), DemandPoint(1200, 120), "none", 0, output_root=output_root, frozen_network=frozen)

    assert first.output_dir.name == "attempt_0001"
    assert second.output_dir.name == "attempt_0002"
    assert first.output_dir.parent == second.output_dir.parent
    assert first.output_dir != second.output_dir
    assert first_hashes == {
        str(path.relative_to(first.output_dir)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in first.output_dir.rglob("*")
        if path.is_file()
    }


def test_attempt_allocator_retries_an_injected_atomic_collision(tmp_path, monkeypatch):
    allocator = getattr(env_module, "_allocate_attempt_dir", None)
    assert allocator is not None, "CR-05 requires atomic attempt allocation"
    real_mkdir = Path.mkdir
    injected = {"done": False}

    def colliding_mkdir(path, *args, **kwargs):
        if path.name == "attempt_0001" and kwargs.get("exist_ok") is False and not injected["done"]:
            injected["done"] = True
            raise FileExistsError("injected concurrent allocation collision")
        return real_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", colliding_mkdir)
    allocated = allocator(tmp_path / "runs", "experiment")

    assert injected["done"] is True
    assert allocated.name == "attempt_0002"
    assert allocated.is_dir()


def test_frozen_mismatch_before_run_stops_before_sumo_or_attempt(tmp_path, monkeypatch):
    frozen = build_network_module.preflight_network(tmp_path / "preflight" / "merge.net.xml")
    frozen.net_path.write_bytes(frozen.net_path.read_bytes() + b"\n<!-- pre-run mismatch -->\n")
    monkeypatch.setattr(env_module, "_import_traci", lambda: (_ for _ in ()).throw(AssertionError("SUMO must not start")))

    with pytest.raises(build_network_module.FrozenNetworkMismatchError):
        env_module.run_experiment(_short_config(), DemandPoint(1200, 120), "none", 0, output_root=tmp_path / "runs", frozen_network=frozen)

    assert not (tmp_path / "runs").exists()


def test_frozen_mismatch_after_run_writes_invalid_summary_then_propagates(tmp_path, monkeypatch):
    frozen = build_network_module.preflight_network(tmp_path / "preflight" / "merge.net.xml")
    real_verify = env_module.verify_frozen_network
    calls = {"count": 0}

    def fail_second_verification(value):
        calls["count"] += 1
        if calls["count"] == 2:
            raise build_network_module.FrozenNetworkMismatchError("injected post-run frozen mismatch")
        return real_verify(value)

    monkeypatch.setattr(env_module, "verify_frozen_network", fail_second_verification)
    with pytest.raises(build_network_module.FrozenNetworkMismatchError, match="post-run") as caught:
        env_module.run_experiment(_short_config(), DemandPoint(1200, 120), "none", 0, output_root=tmp_path / "runs", frozen_network=frozen)

    summaries = list((tmp_path / "runs").rglob("summary.csv"))
    assert len(summaries) == 1
    with summaries[0].open(encoding="utf-8", newline="") as handle:
        row = next(csv.DictReader(handle))
    assert row["valid"] == "False"
    assert "post-run frozen mismatch" in row["failure_reason"]
    attempts = list((tmp_path / "runs").rglob("attempt.json"))
    assert len(attempts) == 1
    attempt = json.loads(attempts[0].read_text(encoding="utf-8"))
    assert attempt["failure_stage"] == "post_run_verify"
    assert attempt["failure_type"] == "FrozenNetworkMismatchError"
    assert attempt["retryable"] is False
    context = getattr(caught.value, "failure_context", None)
    assert context is not None
    assert context["failure_stage"] == "post_run_verify"
    assert Path(context["output_dir"]).name == "attempt_0001"


def test_run_additional_rejects_path_traversal_detector_id(tmp_path):
    tree = ET.parse(build_network_module.NETWORK_DIR / "merge.add.xml")
    tree.getroot()[0].set("id", "../escaped_detector")
    malicious_template = tmp_path / "malicious.add.xml"
    tree.write(malicious_template, encoding="utf-8", xml_declaration=True)
    run_dir = tmp_path / "run"

    with pytest.raises(ValueError, match="detector"):
        env_module.create_run_additional(run_dir, template_path=malicious_template)

    assert not (run_dir / "escaped_detector.xml").exists()
    assert not (tmp_path / "escaped_detector.xml").exists()
