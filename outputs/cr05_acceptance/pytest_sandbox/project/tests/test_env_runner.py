import csv
import hashlib
import xml.etree.ElementTree as ET
from pathlib import Path

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
        _short_config(),
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
    first = env_module.run_experiment(_short_config(), DemandPoint(1200, 120), "none", 0, output_root=tmp_path / "first", frozen_network=frozen)
    second = env_module.run_experiment(_short_config(), DemandPoint(1200, 120), "none", 0, output_root=tmp_path / "second", frozen_network=frozen)

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
    with pytest.raises(build_network_module.FrozenNetworkMismatchError, match="post-run"):
        env_module.run_experiment(_short_config(), DemandPoint(1200, 120), "none", 0, output_root=tmp_path / "runs", frozen_network=frozen)

    summaries = list((tmp_path / "runs").rglob("summary.csv"))
    assert len(summaries) == 1
    with summaries[0].open(encoding="utf-8", newline="") as handle:
        row = next(csv.DictReader(handle))
    assert row["valid"] == "False"
    assert "post-run frozen mismatch" in row["failure_reason"]


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
