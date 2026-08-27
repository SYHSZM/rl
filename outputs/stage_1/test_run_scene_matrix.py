from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "outputs" / "stage_1" / "run_scene_matrix.py"
EXPECTED_SEMANTIC_SHA256 = "502e41585b7169e726bba5b1bd19393af2025c1480a7cf483c94002b6452752f"


def test_collect_evidence_preserves_history_and_selects_strict_valid_matrix():
    assert SCRIPT.is_file(), "read-only matrix summary script is missing"
    spec = importlib.util.spec_from_file_location("stage1_matrix_summary", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    evidence = module.collect_evidence()

    assert len(evidence["run_index_rows"]) == 16
    assert sum(row["selected"] for row in evidence["run_index_rows"]) == 15
    assert len(evidence["quality_rows"]) == 15
    assert all(row["window_count"] == 120 for row in evidence["quality_rows"])
    assert all(row["native_detector_count"] == 8 for row in evidence["quality_rows"])
    assert all(row["hard_invalid_count"] == 0 for row in evidence["quality_rows"])
    assert all(row["source_sha256_match"] for row in evidence["quality_rows"])
    assert all(row["raw_net_sha256_match"] for row in evidence["quality_rows"])
    assert all(row["semantic_net_sha256_match"] for row in evidence["quality_rows"])
    assert sum(row["quality_flag_count"] for row in evidence["quality_rows"]) == 1
    assert sum(row["emergency_event_count"] for row in evidence["quality_rows"]) == 1
    assert sum(row["collision_event_count"] for row in evidence["quality_rows"]) == 0
    assert evidence["event_rows"] == [
        {
            "level": "high",
            "seed": 2,
            "output_dir": evidence["event_rows"][0]["output_dir"],
            "type": "emergency_braking",
            "vehicle": "main_1.1653",
            "lane": "main_3_0",
            "time_s": "2581.00",
            "first_log": "sumo.log",
            "line": evidence["event_rows"][0]["line"],
        }
    ]
    assert {row["semantic_net_sha256"] for row in evidence["quality_rows"]} == {
        EXPECTED_SEMANTIC_SHA256
    }
    assert len(evidence["timeseries_rows"]) == 3 * 120

    report = module._task_report(evidence)
    assert (
        "protocol 8.1 不构成硬无效，作为 Stage 1 safety quality flag 保留，并转交内容3"
        in report
    )
    assert "protocol 8.2 quality" not in report
    assert "按 protocol 8.2" not in report
