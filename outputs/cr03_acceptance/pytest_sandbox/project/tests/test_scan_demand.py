import csv
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import build_network as build_network_module
from experiment_config import default_config
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
    monkeypatch.setattr(scan_demand_module, "preflight_network", lambda path: object())

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


def test_scan_records_ordinary_run_failure_and_continues_planned_runs(tmp_path, monkeypatch):
    config = default_config()
    config.mainline_range = (1200, 1200, 1)
    config.ramp_range = (120, 120, 1)
    config.seeds = (0,)
    calls = []
    monkeypatch.setattr(scan_demand_module, "preflight_network", lambda path: object())

    def fake_run(config, demand, controller, seed, output_root, use_gui, frozen_network=None):
        calls.append(controller)
        if len(calls) == 1:
            raise RuntimeError("injected ordinary run failure")
        summary = _summary(demand, controller, seed)
        output_dir = Path(output_root) / "fake" / "attempt_0001"
        _write_attempt(output_dir)
        return SimpleNamespace(
            experiment_id="fake", valid=True, output_dir=output_dir,
            failure_reason="", summary=summary,
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
    monkeypatch.setattr(scan_demand_module, "preflight_network", lambda path: object())
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
    monkeypatch.setattr(scan_demand_module, "preflight_network", lambda path: object())

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


def test_malformed_attempt_record_is_output_parse_failure_and_scan_continues(tmp_path, monkeypatch):
    config = default_config()
    config.mainline_range = (1200, 1200, 1)
    config.ramp_range = (120, 120, 1)
    config.seeds = (0,)
    calls = []
    monkeypatch.setattr(scan_demand_module, "preflight_network", lambda path: object())

    def fake_run(config, demand, controller, seed, output_root, use_gui, frozen_network=None):
        calls.append(controller)
        output_dir = Path(output_root) / f"fake_{controller}" / "attempt_0001"
        output_dir.mkdir(parents=True, exist_ok=True)
        if controller == "none":
            (output_dir / "attempt.json").write_text("{malformed", encoding="utf-8")
        else:
            _write_attempt(output_dir)
        summary = _summary(demand, controller, seed)
        return SimpleNamespace(
            experiment_id=f"fake_{controller}", valid=True, output_dir=output_dir,
            failure_reason="", summary=summary,
        )

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
    monkeypatch.setattr(scan_demand_module, "preflight_network", lambda path: object())

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
    monkeypatch.setattr(scan_demand_module, "preflight_network", lambda path: object())

    def fake_run(config, demand, controller, seed, output_root, use_gui, frozen_network=None):
        calls.append(controller)
        result_experiment_id = f"strict_{controller}"
        output_dir = Path(output_root) / result_experiment_id / "attempt_0001"
        output_dir.mkdir(parents=True, exist_ok=True)
        if controller == "none" and corruption == "empty_object":
            (output_dir / "attempt.json").write_text("{}", encoding="utf-8")
        else:
            _write_attempt(output_dir, overrides=overrides if controller == "none" else None)
        return SimpleNamespace(
            experiment_id=result_experiment_id, valid=True, output_dir=output_dir,
            failure_reason="", summary=_summary(demand, controller, seed),
        )

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
