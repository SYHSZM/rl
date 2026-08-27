import csv
import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import pytest

import build_network as build_network_module
from experiment_config import default_config, experiment_id
from metrics import EpisodeSummary
import scan_demand as scan_demand_module
from scan_demand import planned_runs


def _summary(demand, controller, seed, *, valid=True, failure_reason=""):
    return EpisodeSummary(
        experiment_id=f"fake_{controller}_{seed}", controller=controller, seed=seed,
        mainline_vph=demand.mainline_vph, ramp_vph=demand.ramp_vph,
        valid=valid, failure_reason=failure_reason, total_time_spent_s=0.0,
        mean_speed_mps=30.0 if valid else 0.0, bottleneck_throughput_veh=0,
        max_ramp_queue_veh=0, breakdown_start_s=0,
        congestion_duration_s=0, recovered=valid, teleports=0,
    )


def _write_attempt(
    output_dir, *, status="success", valid=True, stage="", failure_type="", message="",
    overrides=None,
):
    output_dir.mkdir(parents=True, exist_ok=True)
    record = {
        "schema_version": "cr03-attempt-v1", "experiment_id": output_dir.parent.name,
        "status": status, "valid": valid, "failure_stage": stage,
        "failure_type": failure_type, "failure_message": message,
        "retryable": stage in {"run_loop", "output_write", "output_parse"},
        "attempt": 1, "started_at": "2026-08-07T00:00:00+00:00",
        "finished_at": "2026-08-07T00:00:01+00:00", "output_dir": str(output_dir),
    }
    record.update(overrides or {})
    (output_dir / "attempt.json").write_text(json.dumps(record), encoding="utf-8")


def _contextual_error(message, stage, *, output_dir=None, error_type="RuntimeError"):
    exc = RuntimeError(message)
    exc.failure_context = {
        "failure_stage": stage, "failure_type": error_type,
        "failure_message": message,
        "retryable": stage in {"prepare_output", "run_loop", "output_write", "output_parse"},
        "output_dir": str(output_dir) if output_dir else "", "attempt": 1 if output_dir else "",
        "experiment_id": output_dir.parent.name if output_dir else "",
    }
    return exc


def _sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _fake_preflight(tmp_path, calls):
    source_dir = tmp_path / "network_source"
    source_dir.mkdir(parents=True, exist_ok=True)
    for name in build_network_module.NETWORK_SOURCE_FILENAMES:
        (source_dir / name).write_text(f"<{name}/>", encoding="utf-8")

    def preflight(path):
        path = Path(path)
        calls.append(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("<net/>", encoding="utf-8")
        return build_network_module.FrozenNetwork(
            net_path=path.resolve(),
            net_sha256=_sha256(path),
            source_dir=source_dir.resolve(),
            source_sha256={name: _sha256(source_dir / name) for name in build_network_module.NETWORK_SOURCE_FILENAMES},
        )

    return preflight


def _allocate_fake_attempt(output_root, exp_id):
    experiment_root = Path(output_root) / exp_id
    experiment_root.mkdir(parents=True, exist_ok=True)
    attempt = 1
    while (experiment_root / f"attempt_{attempt:04d}").exists():
        attempt += 1
    output_dir = experiment_root / f"attempt_{attempt:04d}"
    output_dir.mkdir()
    return output_dir, attempt


def _write_complete_attempt(
    config,
    demand,
    controller,
    seed,
    output_root,
    frozen_network,
    *,
    valid=True,
    omit_tripinfo=False,
    foreign_config=False,
    window_times=None,
    summary_overrides=None,
):
    exp_id = experiment_id(config, demand, controller, seed)
    output_dir, attempt_number = _allocate_fake_attempt(output_root, exp_id)
    summary_payload = dict(
        experiment_id=exp_id,
        controller=controller,
        seed=seed,
        mainline_vph=demand.mainline_vph,
        ramp_vph=demand.ramp_vph,
        valid=valid,
        failure_reason="" if valid else "injected invalid attempt",
        total_time_spent_s=100.0,
        mean_speed_mps=30.0 if valid else 0.0,
        bottleneck_throughput_veh=10,
        max_ramp_queue_veh=2,
        breakdown_start_s=0,
        congestion_duration_s=0,
        recovered=valid,
        teleports=0,
    )
    summary_payload.update(summary_overrides or {})
    summary = EpisodeSummary(**summary_payload)
    _write_attempt(
        output_dir,
        status="success" if valid else "failed",
        valid=valid,
        stage="" if valid else "run_loop",
        failure_type="" if valid else "RuntimeError",
        message="" if valid else "injected invalid attempt",
        overrides={
            "attempt": attempt_number,
            "started_at": f"2026-08-07T00:00:{attempt_number:02d}+00:00",
            "finished_at": f"2026-08-07T00:01:{attempt_number:02d}+00:00",
            "output_dir": str(output_dir.resolve()),
        },
    )
    config_snapshot = {
        "config": asdict(config),
        "demand": asdict(demand),
        "controller": controller,
        "seed": seed,
    }
    if foreign_config:
        config_snapshot["config"]["free_flow_speed_mps"] = 99.0
    (output_dir / "config.json").write_text(json.dumps(config_snapshot, sort_keys=True), encoding="utf-8")
    (output_dir / "network_snapshot.json").write_text(
        json.dumps(
            {
                "net_path": str(frozen_network.net_path),
                "net_sha256": frozen_network.net_sha256,
                "source_dir": str(frozen_network.source_dir),
                "source_sha256": frozen_network.source_sha256,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    with (output_dir / "summary.csv").open("w", newline="", encoding="utf-8") as handle:
        row = asdict(summary)
        writer = csv.DictWriter(handle, fieldnames=list(row))
        writer.writeheader()
        writer.writerow(row)
    if window_times is None:
        window_times = range(
            config.metrics_interval_s,
            config.simulation_duration_s + 1,
            config.metrics_interval_s,
        )
    with (output_dir / "windows.csv").open("w", newline="", encoding="utf-8") as handle:
        rows = [{
            "experiment_id": exp_id,
            "controller": controller,
            "seed": seed,
            "time_s": time_s,
            "mainline_vph": demand.mainline_vph,
            "ramp_vph": demand.ramp_vph,
            "metric_schema_version": "cr02-window-v1",
        } for time_s in window_times]
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    (output_dir / "system_exposure.json").write_text(
        json.dumps({"schema_version": "cr02-system-exposure-v1"}), encoding="utf-8"
    )
    for name in ("merge.rou.xml", "run.add.xml", "sumo.log", "sumo_error.log"):
        (output_dir / name).write_text(f"fixture {name}", encoding="utf-8")
    if not omit_tripinfo:
        (output_dir / "tripinfo.xml").write_text("<tripinfos/>", encoding="utf-8")
    native_dir = output_dir / "native"
    native_dir.mkdir()
    for detector_id in sorted(build_network_module.required_detector_ids()):
        (native_dir / f"{detector_id}.xml").write_text("<detector/>", encoding="utf-8")
    return SimpleNamespace(
        experiment_id=exp_id,
        valid=valid,
        output_dir=output_dir,
        failure_reason=summary.failure_reason,
        summary=summary,
        window_records=[],
    )


def _complete_runner(scenarios, calls):
    invocation_counts = {}

    def run(config, demand, controller, seed, output_root, use_gui, frozen_network=None):
        key = (controller, seed)
        invocation_counts[key] = invocation_counts.get(key, 0) + 1
        calls.append(key)
        scenario = scenarios.get(key, {}) if invocation_counts[key] == 1 else {}
        return _write_complete_attempt(
            config,
            demand,
            controller,
            seed,
            output_root,
            frozen_network,
            **scenario,
        )

    return run


def test_planned_runs_pairs_controllers_by_demand_and_seed():
    config = default_config()
    config.mainline_range = (3000, 3000, 300)
    config.ramp_range = (300, 450, 150)
    config.seeds = (0, 1)
    runs = planned_runs(config, ("none", "alinea"))
    assert len(runs) == 8
    assert runs[0][1:] == ("none", 0)
    assert runs[1][1:] == ("alinea", 0)


def test_resume_skips_only_complete_matching_attempts_and_rebuilds_stable_index(tmp_path, monkeypatch):
    config = default_config()
    config.mainline_range = (1200, 1200, 1)
    config.ramp_range = (120, 120, 1)
    config.seeds = (0, 1, 2, 3)
    preflight_calls = []
    run_calls = []
    monkeypatch.setattr(scan_demand_module, "preflight_network", _fake_preflight(tmp_path, preflight_calls))
    monkeypatch.setattr(
        scan_demand_module,
        "run_experiment",
        _complete_runner(
            {
                ("none", 1): {"valid": False},
                ("none", 2): {"omit_tripinfo": True},
                ("none", 3): {"foreign_config": True},
            },
            run_calls,
        ),
    )

    scan_dir = scan_demand_module.scan_demand(
        config,
        tmp_path / "scan",
        controllers=("none",),
        resume=False,
        scan_id="resume_case",
    )
    assert run_calls == [("none", 0), ("none", 1), ("none", 2), ("none", 3)]
    run_calls.clear()

    resumed = scan_demand_module.scan_demand(
        config,
        tmp_path / "scan",
        controllers=("none",),
        resume=True,
        scan_id="resume_case",
    )
    assert resumed == scan_dir
    assert run_calls == [("none", 1), ("none", 2), ("none", 3)]
    assert len(preflight_calls) == 1
    with (scan_dir / "run_index.csv").open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 7
    seed_one = [row for row in rows if row["seed"] == "1"]
    assert [row["attempt_status"] for row in seed_one] == ["failed", "success"]
    assert [row["status"] for row in seed_one] == ["invalid", "valid"]
    with (scan_dir / "resume_decisions.csv").open(newline="", encoding="utf-8") as handle:
        decisions = list(csv.DictReader(handle))
    assert [row["decision"] for row in decisions] == ["skip", "retry", "retry", "retry"]

    stable_index = (scan_dir / "run_index.csv").read_bytes()
    run_calls.clear()
    scan_demand_module.scan_demand(
        config,
        tmp_path / "scan",
        controllers=("none",),
        resume=True,
        scan_id="resume_case",
    )
    assert run_calls == []
    assert len(preflight_calls) == 1
    assert (scan_dir / "run_index.csv").read_bytes() == stable_index
    assert len(list((scan_dir / "runs").glob("*/attempt_*"))) == 7


def test_automatic_scan_identity_changes_with_config(tmp_path, monkeypatch):
    preflight_calls = []
    run_calls = []
    monkeypatch.setattr(scan_demand_module, "preflight_network", _fake_preflight(tmp_path, preflight_calls))
    monkeypatch.setattr(scan_demand_module, "run_experiment", _complete_runner({}, run_calls))
    first_config = default_config()
    first_config.mainline_range = (1200, 1200, 1)
    first_config.ramp_range = (120, 120, 1)
    first_config.seeds = (0,)
    second_config = default_config()
    second_config.mainline_range = (1200, 1200, 1)
    second_config.ramp_range = (120, 120, 1)
    second_config.seeds = (0,)
    second_config.free_flow_speed_mps = 31.0

    first = scan_demand_module.scan_demand(first_config, tmp_path / "scan", controllers=("none",), resume=True)
    second = scan_demand_module.scan_demand(second_config, tmp_path / "scan", controllers=("none",), resume=True)

    assert first != second
    assert first.name.startswith("scan_") and second.name.startswith("scan_")
    assert len(preflight_calls) == 2


def test_explicit_scan_id_rejects_identity_mismatch_without_mutating_manifest(tmp_path, monkeypatch):
    preflight_calls = []
    monkeypatch.setattr(scan_demand_module, "preflight_network", _fake_preflight(tmp_path, preflight_calls))
    monkeypatch.setattr(scan_demand_module, "run_experiment", _complete_runner({}, []))
    first_config = default_config()
    first_config.mainline_range = (1200, 1200, 1)
    first_config.ramp_range = (120, 120, 1)
    first_config.seeds = (0,)
    second_config = default_config()
    second_config.mainline_range = (1200, 1200, 1)
    second_config.ramp_range = (120, 120, 1)
    second_config.seeds = (0,)
    second_config.free_flow_speed_mps = 31.0

    scan_dir = scan_demand_module.scan_demand(
        first_config, tmp_path / "scan", controllers=("none",), resume=True, scan_id="fixed_scan",
    )
    manifest_path = scan_dir / "scan_manifest.json"
    before = manifest_path.read_bytes()
    with pytest.raises(ValueError, match="identity"):
        scan_demand_module.scan_demand(
            second_config, tmp_path / "scan", controllers=("none",), resume=True, scan_id="fixed_scan",
        )

    assert manifest_path.read_bytes() == before
    assert len(preflight_calls) == 1


def test_existing_resume_reuses_frozen_network_without_preflight(tmp_path, monkeypatch):
    preflight_calls = []
    run_calls = []
    monkeypatch.setattr(scan_demand_module, "preflight_network", _fake_preflight(tmp_path, preflight_calls))
    monkeypatch.setattr(scan_demand_module, "run_experiment", _complete_runner({}, run_calls))
    config = default_config()
    config.mainline_range = (1200, 1200, 1)
    config.ramp_range = (120, 120, 1)
    config.seeds = (0,)
    first = scan_demand_module.scan_demand(
        config, tmp_path / "scan", controllers=("none",), resume=True, scan_id="reuse_frozen",
    )
    monkeypatch.setattr(
        scan_demand_module,
        "preflight_network",
        lambda path: (_ for _ in ()).throw(AssertionError("resume must not preflight")),
    )
    run_calls.clear()

    second = scan_demand_module.scan_demand(
        config, tmp_path / "scan", controllers=("none",), resume=True, scan_id="reuse_frozen",
    )

    assert second == first
    assert run_calls == []
    assert len(preflight_calls) == 1


def test_scan_and_artifact_manifests_are_complete_and_atomically_written(tmp_path, monkeypatch):
    monkeypatch.setattr(scan_demand_module, "preflight_network", _fake_preflight(tmp_path, []))
    monkeypatch.setattr(scan_demand_module, "run_experiment", _complete_runner({}, []))
    config = default_config()
    config.mainline_range = (1200, 1200, 1)
    config.ramp_range = (120, 120, 1)
    config.seeds = (0,)

    scan_dir = scan_demand_module.scan_demand(
        config, tmp_path / "scan", controllers=("none",), resume=True, scan_id="manifest_case",
    )

    scan_manifest = json.loads((scan_dir / "scan_manifest.json").read_text(encoding="utf-8"))
    assert scan_manifest["schema_version"] == "cr04-scan-manifest-v1"
    assert scan_manifest["scan_id"] == "manifest_case"
    assert scan_manifest["identity_sha256"] == hashlib.sha256(
        json.dumps(scan_manifest["identity"], sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    assert set(scan_manifest["identity"]["code"]["files"]) == {
        "build_network.py", "classify.py", "controllers.py", "env.py",
        "experiment_config.py", "metrics.py", "rou_generate.py", "scan_demand.py",
    }
    assert scan_manifest["identity"]["protocol"]["version"] == "1.0"
    assert scan_manifest["identity"]["schemas"] == {
        "artifact_manifest": "cr04-artifact-manifest-v1",
        "attempt": "cr03-attempt-v1",
        "scan_manifest": "cr04-scan-manifest-v1",
        "summary": "cr02-summary-v1",
        "system_exposure": "cr02-system-exposure-v1",
        "window": "cr02-window-v1",
    }
    attempt_dir = next((scan_dir / "runs").glob("*/attempt_0001"))
    artifact = json.loads((attempt_dir / "artifact_manifest.json").read_text(encoding="utf-8"))
    assert artifact["schema_version"] == "cr04-artifact-manifest-v1"
    assert artifact["planned_identity"]["scan_id"] == "manifest_case"
    assert artifact["planned_identity"]["seed"] == 0
    paths = [row["path"] for row in artifact["files"]]
    assert "artifact_manifest.json" not in paths
    assert {"attempt.json", "summary.csv", "tripinfo.xml", "native/det_main_0.xml"}.issubset(paths)
    assert not list(scan_dir.rglob("*.tmp"))


def test_unmanifested_attempt_file_is_invalid_and_retried_on_resume(tmp_path, monkeypatch):
    config = default_config()
    config.mainline_range = (1200, 1200, 1)
    config.ramp_range = (120, 120, 1)
    config.seeds = (0,)
    preflight_calls = []
    run_calls = []
    monkeypatch.setattr(scan_demand_module, "preflight_network", _fake_preflight(tmp_path, preflight_calls))
    monkeypatch.setattr(scan_demand_module, "run_experiment", _complete_runner({}, run_calls))
    scan_dir = scan_demand_module.scan_demand(
        config, tmp_path / "scan", controllers=("none",), resume=False, scan_id="extra_file",
    )
    first_attempt = next((scan_dir / "runs").glob("*/attempt_0001"))
    (first_attempt / "unmanifested.txt").write_text("late mutation", encoding="utf-8")
    run_calls.clear()

    scan_demand_module.scan_demand(
        config, tmp_path / "scan", controllers=("none",), resume=True, scan_id="extra_file",
    )

    assert run_calls == [("none", 0)]
    assert len(preflight_calls) == 1
    assert len(list((scan_dir / "runs").glob("*/attempt_*"))) == 2


def test_scan_demand_builds_one_batch_preflight_and_reuses_it(tmp_path, monkeypatch):
    config = default_config()
    config.mainline_range = (1200, 1200, 1)
    config.ramp_range = (120, 120, 1)
    config.seeds = (0,)
    preflight_paths = []
    received_frozen = []
    frozen_values = []
    real_fake_preflight = _fake_preflight(tmp_path, preflight_paths)

    def fake_preflight(path):
        frozen = real_fake_preflight(path)
        frozen_values.append(frozen)
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
    assert received_frozen == [frozen_values[0], frozen_values[0]]


def test_resume_false_allocates_collision_safe_suffix_for_same_identity(tmp_path, monkeypatch):
    config = default_config()
    config.mainline_range = (1200, 1200, 1)
    config.ramp_range = (120, 120, 1)
    config.seeds = (0,)
    frozen = build_network_module.FrozenNetwork(
        net_path=tmp_path / "net.xml", net_sha256="fake",
        source_dir=tmp_path, source_sha256={},
    )

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

    monkeypatch.setattr(scan_demand_module, "preflight_network", lambda path: frozen)
    monkeypatch.setattr(scan_demand_module, "run_experiment", fake_run)
    first = scan_demand_module.scan_demand(
        config, output_root=tmp_path / "scan", controllers=("none",), resume=False, scan_id="fixed",
    )
    second = scan_demand_module.scan_demand(
        config, output_root=tmp_path / "scan", controllers=("none",), resume=False, scan_id="fixed",
    )

    assert first.name == "fixed"
    assert second.name == "fixed_0001"
    assert first != second


def test_scan_aborts_batch_immediately_on_frozen_mismatch(tmp_path, monkeypatch):
    config = default_config()
    config.mainline_range = (1200, 1200, 1)
    config.ramp_range = (120, 120, 1)
    config.seeds = (0,)
    calls = []
    monkeypatch.setattr(scan_demand_module, "preflight_network", _fake_preflight(tmp_path, []))

    def mismatch(*args, **kwargs):
        calls.append((args, kwargs))
        raise build_network_module.FrozenNetworkMismatchError("stop batch")

    monkeypatch.setattr(scan_demand_module, "run_experiment", mismatch)
    with pytest.raises(build_network_module.FrozenNetworkMismatchError, match="stop batch"):
        scan_demand_module.scan_demand(config, output_root=tmp_path / "scan", controllers=("none", "alinea"), resume=False)

    assert len(calls) == 1
    scan_dir = next((tmp_path / "scan").iterdir())
    with (scan_dir / "failure_ledger.csv").open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 1
    assert rows[0]["failure_stage"] == "network_preflight"
    assert rows[0]["failure_type"] == "FrozenNetworkMismatchError"
    assert not (scan_dir / "run_index.csv").exists()


def test_scan_ledgers_and_indexes_post_run_frozen_mismatch_before_abort(tmp_path, monkeypatch):
    config = default_config()
    config.mainline_range = (1200, 1200, 1)
    config.ramp_range = (120, 120, 1)
    config.seeds = (0,)
    monkeypatch.setattr(scan_demand_module, "preflight_network", _fake_preflight(tmp_path, []))

    def mismatch(config, demand, controller, seed, output_root, use_gui, frozen_network=None):
        output_dir = Path(output_root) / f"fake_{controller}_{seed}" / "attempt_0001"
        _write_attempt(
            output_dir, status="failed", valid=False, stage="post_run_verify",
            failure_type="FrozenNetworkMismatchError", message="post-run mismatch",
        )
        exc = build_network_module.FrozenNetworkMismatchError("post-run mismatch")
        exc.failure_context = {
            "failure_stage": "post_run_verify", "failure_type": "FrozenNetworkMismatchError",
            "failure_message": "post-run mismatch", "retryable": False,
            "output_dir": str(output_dir), "attempt": 1,
            "experiment_id": f"fake_{controller}_{seed}",
        }
        raise exc

    monkeypatch.setattr(scan_demand_module, "run_experiment", mismatch)
    with pytest.raises(build_network_module.FrozenNetworkMismatchError, match="post-run mismatch"):
        scan_demand_module.scan_demand(config, tmp_path / "scan", controllers=("none", "alinea"), resume=False)

    scan_dir = next((tmp_path / "scan").iterdir())
    with (scan_dir / "failure_ledger.csv").open(newline="", encoding="utf-8") as handle:
        ledger = list(csv.DictReader(handle))
    with (scan_dir / "run_index.csv").open(newline="", encoding="utf-8") as handle:
        index = list(csv.DictReader(handle))
    assert len(ledger) == len(index) == 1
    assert ledger[0]["failure_stage"] == "post_run_verify"
    assert index[0]["status"] == "invalid"
    assert index[0]["attempt_status"] == "failed"
    assert index[0]["failure_type"] == "FrozenNetworkMismatchError"


def test_artifact_manifest_write_failure_does_not_mask_frozen_mismatch(tmp_path, monkeypatch):
    config = default_config()
    config.mainline_range = (1200, 1200, 1)
    config.ramp_range = (120, 120, 1)
    config.seeds = (0,)
    monkeypatch.setattr(scan_demand_module, "preflight_network", _fake_preflight(tmp_path, []))

    def mismatch(config, demand, controller, seed, output_root, use_gui, frozen_network=None):
        output_dir, _ = _allocate_fake_attempt(output_root, experiment_id(config, demand, controller, seed))
        _write_attempt(
            output_dir, status="failed", valid=False, stage="post_run_verify",
            failure_type="FrozenNetworkMismatchError", message="original frozen mismatch",
        )
        exc = build_network_module.FrozenNetworkMismatchError("original frozen mismatch")
        exc.failure_context = {
            "failure_stage": "post_run_verify",
            "failure_type": "FrozenNetworkMismatchError",
            "failure_message": "original frozen mismatch",
            "retryable": False,
            "output_dir": str(output_dir),
            "attempt": 1,
            "experiment_id": output_dir.parent.name,
        }
        raise exc

    monkeypatch.setattr(scan_demand_module, "run_experiment", mismatch)
    monkeypatch.setattr(
        scan_demand_module, "_write_artifact_manifest",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("manifest disk full")),
    )

    with pytest.raises(build_network_module.FrozenNetworkMismatchError, match="original frozen mismatch"):
        scan_demand_module.scan_demand(
            config, tmp_path / "scan", controllers=("none",), resume=False,
        )


def test_artifact_manifest_write_failure_does_not_mask_ordinary_run_failure(tmp_path, monkeypatch):
    config = default_config()
    config.mainline_range = (1200, 1200, 1)
    config.ramp_range = (120, 120, 1)
    config.seeds = (0,)
    monkeypatch.setattr(scan_demand_module, "preflight_network", _fake_preflight(tmp_path, []))

    def fail_run(config, demand, controller, seed, output_root, use_gui, frozen_network=None):
        output_dir, _ = _allocate_fake_attempt(output_root, experiment_id(config, demand, controller, seed))
        _write_attempt(
            output_dir, status="failed", valid=False, stage="run_loop",
            failure_type="RuntimeError", message="original run failure",
        )
        raise _contextual_error("original run failure", "run_loop", output_dir=output_dir)

    monkeypatch.setattr(scan_demand_module, "run_experiment", fail_run)
    monkeypatch.setattr(
        scan_demand_module, "_write_artifact_manifest",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("manifest disk full")),
    )

    scan_dir = scan_demand_module.scan_demand(
        config, tmp_path / "scan", controllers=("none",), resume=False,
    )
    with (scan_dir / "failure_ledger.csv").open(newline="", encoding="utf-8") as handle:
        ledger = list(csv.DictReader(handle))
    assert ledger[0]["failure_type"] == "RuntimeError"
    assert ledger[0]["failure_message"] == "original run failure"


def test_scan_records_ordinary_run_failure_and_continues_planned_runs(tmp_path, monkeypatch):
    config = default_config()
    config.mainline_range = (1200, 1200, 1)
    config.ramp_range = (120, 120, 1)
    config.seeds = (0,)
    calls = []
    monkeypatch.setattr(scan_demand_module, "preflight_network", _fake_preflight(tmp_path, []))

    def fake_run(config, demand, controller, seed, output_root, use_gui, frozen_network=None):
        calls.append(controller)
        if len(calls) == 1:
            raise RuntimeError("injected ordinary run failure")
        return _write_complete_attempt(
            config, demand, controller, seed, output_root, frozen_network,
        )

    monkeypatch.setattr(scan_demand_module, "run_experiment", fake_run)
    scan_dir = scan_demand_module.scan_demand(config, tmp_path / "scan", controllers=("none", "alinea"), resume=False)

    assert calls == ["none", "alinea"]
    with (scan_dir / "run_index.csv").open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 2
    assert rows[0]["status"] == "invalid"
    assert rows[0]["attempt_status"] == "failed"
    assert rows[0]["failure_stage"] == "run_experiment"
    assert rows[0]["failure_type"] == "RuntimeError"
    assert rows[1]["status"] == "valid"
    assert rows[1]["attempt_status"] == "success"


def test_scan_writes_failure_ledger_when_attempt_directory_cannot_be_created(tmp_path, monkeypatch):
    config = default_config()
    config.mainline_range = (1200, 1200, 1)
    config.ramp_range = (120, 120, 1)
    config.seeds = (0,)
    monkeypatch.setattr(scan_demand_module, "preflight_network", _fake_preflight(tmp_path, []))
    def fail_attempt(*args, **kwargs):
        raise _contextual_error("attempt mkdir denied", "prepare_output", error_type="OSError")

    monkeypatch.setattr(scan_demand_module, "run_experiment", fail_attempt)

    scan_dir = scan_demand_module.scan_demand(config, tmp_path / "scan", controllers=("none", "alinea"), resume=False)

    with (scan_dir / "failure_ledger.csv").open(newline="", encoding="utf-8") as handle:
        ledger = list(csv.DictReader(handle))
    with (scan_dir / "run_index.csv").open(newline="", encoding="utf-8") as handle:
        index = list(csv.DictReader(handle))
    assert len(ledger) == len(index) == 2
    assert {row["failure_stage"] for row in ledger} == {"prepare_output"}
    assert {row["failure_type"] for row in ledger} == {"OSError"}
    assert all(row["retryable"] == "True" for row in ledger)


def test_all_escaped_runs_still_emit_insufficient_valid_demand_classification(tmp_path, monkeypatch):
    config = default_config()
    config.mainline_range = (1200, 1200, 1)
    config.ramp_range = (120, 120, 1)
    config.seeds = (0,)
    monkeypatch.setattr(scan_demand_module, "preflight_network", _fake_preflight(tmp_path, []))

    def fail_run(config, demand, controller, seed, output_root, use_gui, frozen_network=None):
        raise _contextual_error(f"{controller} run escaped", "run_loop")

    monkeypatch.setattr(scan_demand_module, "run_experiment", fail_run)
    scan_dir = scan_demand_module.scan_demand(
        config, tmp_path / "scan", controllers=("none", "alinea"), resume=False,
    )

    with (scan_dir / "run_index.csv").open(newline="", encoding="utf-8") as handle:
        index = list(csv.DictReader(handle))
    with (scan_dir / "demand_classification.csv").open(newline="", encoding="utf-8") as handle:
        classifications = list(csv.DictReader(handle))
    assert len(index) == 2
    assert {row["status"] for row in index} == {"invalid"}
    assert {row["attempt_status"] for row in index} == {"failed"}
    assert len(classifications) == 1
    assert classifications[0]["valid_seed_count"] == "0"
    assert classifications[0]["label"] == "insufficient_valid_runs"
    assert classifications[0]["failure_reason"] == "no_valid_pairs"


def test_incomplete_windows_time_grid_is_invalid_and_retried_on_resume(tmp_path, monkeypatch):
    config = default_config()
    config.mainline_range = (1200, 1200, 1)
    config.ramp_range = (120, 120, 1)
    config.seeds = (0,)
    preflight_calls = []
    monkeypatch.setattr(scan_demand_module, "preflight_network", _fake_preflight(tmp_path, preflight_calls))
    first_calls = []
    incomplete_times = range(
        config.metrics_interval_s,
        config.simulation_duration_s,
        config.metrics_interval_s,
    )
    monkeypatch.setattr(
        scan_demand_module,
        "run_experiment",
        _complete_runner({("none", 0): {"window_times": incomplete_times}}, first_calls),
    )
    scan_dir = scan_demand_module.scan_demand(
        config, tmp_path / "scan", controllers=("none",), resume=False, scan_id="window_grid",
    )
    with (scan_dir / "run_index.csv").open(newline="", encoding="utf-8") as handle:
        first_index = list(csv.DictReader(handle))
    assert first_index[0]["status"] == "invalid"

    resume_calls = []
    monkeypatch.setattr(scan_demand_module, "run_experiment", _complete_runner({}, resume_calls))
    scan_demand_module.scan_demand(
        config, tmp_path / "scan", controllers=("none",), resume=True, scan_id="window_grid",
    )
    assert resume_calls == [("none", 0)]
    assert len(preflight_calls) == 1


def test_resume_preserves_all_cr02_episode_summary_fields(tmp_path, monkeypatch):
    config = default_config()
    config.mainline_range = (1200, 1200, 1)
    config.ramp_range = (120, 120, 1)
    config.seeds = (0,)
    summary_overrides = {
        "loaded_veh": 111,
        "departed_veh": 101,
        "arrived_veh": 91,
        "actual_departure_ratio": 0.91,
        "completion_ratio": 0.82,
        "tts_system_s": 1234.5,
        "tts_pending_s": 234.5,
        "tts_in_network_s": 1000.0,
        "mainline_system_exposure_s": 700.0,
        "ramp_system_exposure_s": 300.0,
        "unknown_system_exposure_s": 5.0,
        "completed_vehicle_exposure_s": 950.0,
        "terminal_in_network_count": 7,
        "terminal_pending_count": 3,
        "terminal_in_network_ids": "veh-a|veh-b",
        "terminal_pending_ids": "veh-c",
        "terminal_in_network_exposure_s": 40.0,
        "terminal_pending_exposure_s": 20.0,
        "terminal_censoring": True,
        "legacy_total_time_spent_s": 999.0,
    }
    monkeypatch.setattr(scan_demand_module, "preflight_network", _fake_preflight(tmp_path, []))

    def first_run(config, demand, controller, seed, output_root, use_gui, frozen_network=None):
        return _write_complete_attempt(
            config, demand, controller, seed, output_root, frozen_network,
            summary_overrides=summary_overrides,
        )

    monkeypatch.setattr(scan_demand_module, "run_experiment", first_run)
    scan_demand_module.scan_demand(
        config, tmp_path / "scan", controllers=("none",), resume=False, scan_id="summary_resume",
    )

    captured = []
    monkeypatch.setattr(
        scan_demand_module, "run_experiment",
        lambda *args, **kwargs: pytest.fail("valid matching attempt must be resumed"),
    )
    monkeypatch.setattr(
        scan_demand_module, "preflight_network",
        lambda *args, **kwargs: pytest.fail("resume must reuse frozen network"),
    )

    def capture_classification(config, summaries):
        captured.extend(summaries)
        return []

    monkeypatch.setattr(scan_demand_module, "_classify_scan", capture_classification)
    scan_demand_module.scan_demand(
        config, tmp_path / "scan", controllers=("none",), resume=True, scan_id="summary_resume",
    )

    assert len(captured) == 1
    restored = asdict(captured[0])
    for field, expected in summary_overrides.items():
        assert restored[field] == expected


def test_malformed_attempt_record_is_output_parse_failure_and_scan_continues(tmp_path, monkeypatch):
    config = default_config()
    config.mainline_range = (1200, 1200, 1)
    config.ramp_range = (120, 120, 1)
    config.seeds = (0,)
    calls = []
    monkeypatch.setattr(scan_demand_module, "preflight_network", _fake_preflight(tmp_path, []))

    def fake_run(config, demand, controller, seed, output_root, use_gui, frozen_network=None):
        calls.append(controller)
        result = _write_complete_attempt(
            config, demand, controller, seed, output_root, frozen_network,
        )
        if controller == "none":
            (result.output_dir / "attempt.json").write_text("{malformed", encoding="utf-8")
        return result

    monkeypatch.setattr(scan_demand_module, "run_experiment", fake_run)
    scan_dir = scan_demand_module.scan_demand(
        config, tmp_path / "scan", controllers=("none", "alinea"), resume=False,
    )

    assert calls == ["none", "alinea"]
    with (scan_dir / "run_index.csv").open(newline="", encoding="utf-8") as handle:
        index = list(csv.DictReader(handle))
    with (scan_dir / "failure_ledger.csv").open(newline="", encoding="utf-8") as handle:
        ledger = list(csv.DictReader(handle))
    with (scan_dir / "demand_classification.csv").open(newline="", encoding="utf-8") as handle:
        classifications = list(csv.DictReader(handle))
    assert len(index) == 2
    assert index[0]["status"] == "invalid"
    assert index[0]["attempt_status"] == "failed"
    assert index[0]["failure_stage"] == "output_parse"
    assert index[0]["failure_type"] == "JSONDecodeError"
    assert index[1]["status"] == "valid"
    assert index[1]["attempt_status"] == "success"
    assert len(ledger) == 1
    assert ledger[0]["failure_stage"] == "output_parse"
    assert ledger[0]["retryable"] == "False"
    assert index[0]["retryable"] == "False"
    assert len(classifications) == 1
    assert classifications[0]["valid_seed_count"] == "0"
    assert classifications[0]["label"] == "insufficient_valid_runs"
    assert classifications[0]["failure_reason"] == "no_valid_pairs"


def test_all_missing_attempt_records_still_classify_without_valid_pairs(tmp_path, monkeypatch):
    config = default_config()
    config.mainline_range = (1200, 1200, 1)
    config.ramp_range = (120, 120, 1)
    config.seeds = (0,)
    monkeypatch.setattr(scan_demand_module, "preflight_network", _fake_preflight(tmp_path, []))

    def fake_run(config, demand, controller, seed, output_root, use_gui, frozen_network=None):
        output_dir = Path(output_root) / f"missing_{controller}" / "attempt_0001"
        output_dir.mkdir(parents=True, exist_ok=True)
        summary = _summary(demand, controller, seed)
        return SimpleNamespace(
            experiment_id=f"missing_{controller}", valid=True, output_dir=output_dir,
            failure_reason="", summary=summary,
        )

    monkeypatch.setattr(scan_demand_module, "run_experiment", fake_run)
    scan_dir = scan_demand_module.scan_demand(
        config, tmp_path / "scan", controllers=("none", "alinea"), resume=False,
    )

    with (scan_dir / "run_index.csv").open(newline="", encoding="utf-8") as handle:
        index = list(csv.DictReader(handle))
    with (scan_dir / "failure_ledger.csv").open(newline="", encoding="utf-8") as handle:
        ledger = list(csv.DictReader(handle))
    with (scan_dir / "demand_classification.csv").open(newline="", encoding="utf-8") as handle:
        classifications = list(csv.DictReader(handle))
    assert len(index) == len(ledger) == 2
    assert {row["failure_stage"] for row in index} == {"output_parse"}
    assert {row["retryable"] for row in index} == {"False"}
    assert {row["retryable"] for row in ledger} == {"False"}
    assert len(classifications) == 1
    assert classifications[0]["mainline_vph"] == "1200"
    assert classifications[0]["ramp_vph"] == "120"
    assert classifications[0]["valid_seed_count"] == "0"
    assert classifications[0]["label"] == "insufficient_valid_runs"
    assert classifications[0]["failure_reason"] == "no_valid_pairs"


@pytest.mark.parametrize(
    ("corruption", "overrides"),
    [
        ("empty_object", None),
        ("wrong_schema", {"schema_version": "cr03-attempt-v0"}),
        ("experiment_mismatch", {"experiment_id": "wrong_experiment"}),
        ("attempt_number_mismatch", {"attempt": 2}),
        ("output_dir_mismatch", {"output_dir": "wrong/output/directory"}),
        ("invalid_status", {"status": "unknown"}),
        ("valid_not_bool", {"valid": "true"}),
        ("retryable_not_bool", {"retryable": 1}),
        ("attempt_is_bool", {"attempt": True}),
        ("naive_started_at", {"started_at": "2026-08-07T00:00:00"}),
        (
            "finished_before_started",
            {
                "started_at": "2026-08-07T00:00:02+00:00",
                "finished_at": "2026-08-07T00:00:01+00:00",
            },
        ),
        ("failed_but_valid", {"status": "failed", "valid": True, "failure_stage": "run_loop", "failure_type": "RuntimeError", "failure_message": "failed"}),
        ("failed_without_metadata", {"status": "failed", "valid": False}),
        ("success_with_failure", {"failure_stage": "run_loop", "failure_type": "RuntimeError", "failure_message": "unexpected"}),
        ("success_retryable", {"retryable": True}),
    ],
)
def test_semantically_invalid_attempt_record_is_hard_output_parse_and_scan_continues(
    tmp_path, monkeypatch, corruption, overrides,
):
    config = default_config()
    config.mainline_range = (1200, 1200, 1)
    config.ramp_range = (120, 120, 1)
    config.seeds = (0,)
    calls = []
    monkeypatch.setattr(scan_demand_module, "preflight_network", _fake_preflight(tmp_path, []))

    def fake_run(config, demand, controller, seed, output_root, use_gui, frozen_network=None):
        calls.append(controller)
        result = _write_complete_attempt(
            config, demand, controller, seed, output_root, frozen_network,
        )
        if controller == "none" and corruption == "empty_object":
            (result.output_dir / "attempt.json").write_text("{}", encoding="utf-8")
        elif controller == "none":
            attempt_path = result.output_dir / "attempt.json"
            record = json.loads(attempt_path.read_text(encoding="utf-8"))
            record.update(overrides)
            attempt_path.write_text(json.dumps(record), encoding="utf-8")
        return result

    monkeypatch.setattr(scan_demand_module, "run_experiment", fake_run)
    scan_dir = scan_demand_module.scan_demand(
        config, tmp_path / "scan", controllers=("none", "alinea"), resume=False,
    )

    assert calls == ["none", "alinea"]
    with (scan_dir / "run_index.csv").open(newline="", encoding="utf-8") as handle:
        index = list(csv.DictReader(handle))
    with (scan_dir / "failure_ledger.csv").open(newline="", encoding="utf-8") as handle:
        ledger = list(csv.DictReader(handle))
    with (scan_dir / "demand_classification.csv").open(newline="", encoding="utf-8") as handle:
        classifications = list(csv.DictReader(handle))
    assert len(index) == 2
    assert index[0]["status"] == "invalid"
    assert index[0]["attempt_status"] == "failed"
    assert index[0]["failure_stage"] == "output_parse"
    assert index[0]["failure_type"] == "ValueError"
    assert index[0]["retryable"] == "False"
    assert index[1]["status"] == "valid"
    assert index[1]["attempt_status"] == "success"
    assert len(ledger) == 1
    assert ledger[0]["failure_stage"] == "output_parse"
    assert ledger[0]["failure_type"] == "ValueError"
    assert ledger[0]["retryable"] == "False"
    assert len(classifications) == 1
    assert classifications[0]["valid_seed_count"] == "0"
    assert classifications[0]["label"] == "insufficient_valid_runs"
    assert classifications[0]["failure_reason"] == "no_valid_pairs"


def test_batch_preflight_failure_writes_scan_ledger_then_propagates(tmp_path, monkeypatch):
    config = default_config()
    config.mainline_range = (1200, 1200, 1)
    config.ramp_range = (120, 120, 1)
    config.seeds = (0,)
    monkeypatch.setattr(
        scan_demand_module,
        "preflight_network",
        lambda path: (_ for _ in ()).throw(RuntimeError("shared preflight failed")),
    )

    with pytest.raises(RuntimeError, match="shared preflight failed"):
        scan_demand_module.scan_demand(config, tmp_path / "scan", controllers=("none",), resume=False)

    scan_dirs = list((tmp_path / "scan").iterdir())
    assert len(scan_dirs) == 1
    with (scan_dirs[0] / "failure_ledger.csv").open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 1
    assert rows[0]["failure_stage"] == "network_preflight"
    assert rows[0]["failure_type"] == "RuntimeError"
    assert rows[0]["retryable"] == "False"
