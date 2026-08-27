from __future__ import annotations

"""Read-only Task 5-B evidence consolidation for existing formal scans.

This module never launches SUMO and never mutates formal_scans. It validates
the accepted CR-04 artifacts in place, then writes only top-level Stage 1
summary files.
"""

import argparse
import csv
import hashlib
import json
import math
import os
import re
import sys
import tempfile
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from build_network import NETWORK_DIR, NETWORK_SOURCE_FILENAMES, required_detector_ids
from experiment_config import DemandPhase, DemandPoint, default_config
from scan_demand import (
    _canonical_sha256,
    _planned_identity,
    _read_scan_manifest,
    _scan_identity,
    _validate_attempt_artifacts_or_raise,
)


OUT = ROOT / "outputs" / "stage_1"
FORMAL_SCANS = OUT / "formal_scans"
EXPECTED_SEMANTIC_SHA256 = "502e41585b7169e726bba5b1bd19393af2025c1480a7cf483c94002b6452752f"
LEVELS = (
    ("low", 3000, 300, "stage1_low_formal_v1"),
    ("medium", 4500, 600, "stage1_medium_formal_v1"),
    ("high", 6000, 1200, "stage1_high_formal_v1"),
)
SEEDS = (0, 1, 2, 3, 4)
EXPECTED_TIMES = tuple(range(30, 3601, 30))
EXPECTED_PHASES = ((0, 600, 0.65), (600, 2400, 1.0), (2400, 3600, 0.70))
EXPECTED_DETECTORS = tuple(sorted(required_detector_ids()))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def semantic_net_sha256(path: Path) -> str:
    root = ET.parse(path).getroot()
    return hashlib.sha256(ET.tostring(root, encoding="utf-8")).hexdigest()


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise


def _atomic_write_json(path: Path, payload: object) -> None:
    _atomic_write_text(
        path,
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )


def _atomic_write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    if not rows:
        raise ValueError(f"refusing to write empty CSV: {path.name}")
    fieldnames = list(rows[0])
    for row in rows:
        if list(row) != fieldnames:
            raise ValueError(f"inconsistent CSV columns in {path.name}")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise


def _config(mainline_vph: int, ramp_vph: int):
    config = default_config()
    config.mainline_range = (mainline_vph, mainline_vph, 1)
    config.ramp_range = (ramp_vph, ramp_vph, 1)
    config.seeds = SEEDS
    config.simulation_duration_s = 3600
    config.step_length_s = 1.0
    config.metrics_interval_s = 30
    config.demand_phases = [DemandPhase(*phase) for phase in EXPECTED_PHASES]
    return config


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _to_float(value: str | None) -> float | None:
    if value is None or value == "":
        return None
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"non-finite numeric value: {value}")
    return result


def _source_hashes() -> dict[str, str]:
    return {
        name: sha256_file(NETWORK_DIR / name)
        for name in NETWORK_SOURCE_FILENAMES
    }


def _event_records(attempt_dir: Path) -> tuple[list[dict[str, str]], bool]:
    unique: dict[tuple[str, str, str, str], dict[str, str]] = {}
    sumo_error_nonempty = False
    emergency_pattern = re.compile(
        r"Vehicle '([^']+)' performs emergency braking on lane '([^']+)'"
        r".*?time=([0-9]+(?:\.[0-9]+)?)",
        re.IGNORECASE,
    )
    vehicle_pattern = re.compile(r"Vehicle '([^']+)'", re.IGNORECASE)
    lane_pattern = re.compile(r"lane '([^']+)'", re.IGNORECASE)
    time_pattern = re.compile(r"time[= ]([0-9]+(?:\.[0-9]+)?)", re.IGNORECASE)

    for log_name in ("sumo.log", "sumo_error.log"):
        path = attempt_dir / log_name
        if not path.is_file():
            raise FileNotFoundError(path)
        text = path.read_text(encoding="utf-8", errors="replace")
        if log_name == "sumo_error.log" and text.strip():
            sumo_error_nonempty = True
        for line in text.splitlines():
            emergency = emergency_pattern.search(line)
            if emergency:
                vehicle, lane, time_s = emergency.groups()
                event_type = "emergency_braking"
            elif re.search(r"(?i)warning:.*collision", line):
                vehicle_match = vehicle_pattern.search(line)
                lane_match = lane_pattern.search(line)
                time_match = time_pattern.search(line)
                vehicle = vehicle_match.group(1) if vehicle_match else ""
                lane = lane_match.group(1) if lane_match else ""
                time_s = time_match.group(1) if time_match else ""
                event_type = "collision"
            elif re.search(r"(?i)warning:.*teleport", line):
                vehicle_match = vehicle_pattern.search(line)
                lane_match = lane_pattern.search(line)
                time_match = time_pattern.search(line)
                vehicle = vehicle_match.group(1) if vehicle_match else ""
                lane = lane_match.group(1) if lane_match else ""
                time_s = time_match.group(1) if time_match else ""
                event_type = "teleport"
            elif re.search(r"(?i)(^|\s)(error|fatal):", line):
                vehicle_match = vehicle_pattern.search(line)
                lane_match = lane_pattern.search(line)
                time_match = time_pattern.search(line)
                vehicle = vehicle_match.group(1) if vehicle_match else ""
                lane = lane_match.group(1) if lane_match else ""
                time_s = time_match.group(1) if time_match else ""
                event_type = "sumo_error"
            else:
                continue
            key = (vehicle, lane, time_s, event_type)
            unique.setdefault(
                key,
                {
                    "vehicle": vehicle,
                    "lane": lane,
                    "time_s": time_s,
                    "type": event_type,
                    "first_log": log_name,
                    "line": line.strip(),
                },
            )
    return sorted(unique.values(), key=lambda row: (row["type"], row["time_s"], row["vehicle"])), sumo_error_nonempty


def _strict_selected_attempts(level: str, scan_dir: Path, config, demand: DemandPoint):
    expected_identity = _scan_identity(config, ("none",))
    expected_identity_sha256 = _canonical_sha256(expected_identity)
    scan_manifest, frozen_network = _read_scan_manifest(
        scan_dir, expected_identity, expected_identity_sha256
    )
    raw_net_path = frozen_network.net_path
    raw_net_sha256 = sha256_file(raw_net_path)
    semantic_sha256 = semantic_net_sha256(raw_net_path)
    if raw_net_sha256 != frozen_network.net_sha256:
        raise ValueError(f"{level}: frozen raw network hash mismatch")
    if semantic_sha256 != EXPECTED_SEMANTIC_SHA256:
        raise ValueError(f"{level}: semantic network hash mismatch")

    selected: dict[int, tuple[Path, object, dict[str, object]]] = {}
    for seed in SEEDS:
        planned_identity = _planned_identity(
            scan_manifest, config, demand, "none", seed
        )
        experiment_root = scan_dir / "runs" / str(planned_identity["experiment_id"])
        attempts = sorted(
            (path for path in experiment_root.glob("attempt_*") if path.is_dir()),
            key=lambda path: path.name,
        )
        for attempt_dir in reversed(attempts):
            try:
                summary = _validate_attempt_artifacts_or_raise(
                    attempt_dir, planned_identity, frozen_network
                )
            except Exception:
                continue
            selected[seed] = (attempt_dir, summary, planned_identity)
            break
        if seed not in selected:
            raise ValueError(f"{level} seed {seed}: no strict valid attempt")
    return scan_manifest, frozen_network, raw_net_sha256, semantic_sha256, selected


def _quality_row(
    level: str,
    scan_id: str,
    seed: int,
    attempt_dir: Path,
    summary,
    planned_identity: dict[str, object],
    scan_manifest: dict[str, object],
    raw_net_sha256: str,
    semantic_sha256: str,
    current_source_hashes: dict[str, str],
) -> tuple[dict[str, object], list[dict[str, str]], list[dict[str, str]]]:
    windows = _read_csv(attempt_dir / "windows.csv")
    actual_times = tuple(int(row["time_s"]) for row in windows)
    if actual_times != EXPECTED_TIMES:
        raise ValueError(f"{level} seed {seed}: incomplete window grid")
    native_files = sorted((attempt_dir / "native").glob("*.xml"))
    native_ids = tuple(path.stem for path in native_files)
    if native_ids != EXPECTED_DETECTORS:
        raise ValueError(f"{level} seed {seed}: native detector set mismatch")
    for path in native_files:
        ET.parse(path)

    events, sumo_error_nonempty = _event_records(attempt_dir)
    counts = defaultdict(int)
    for event in events:
        counts[event["type"]] += 1
    summary_payload = asdict(summary)
    if int(summary_payload["teleports"]) != 0:
        counts["teleport"] += int(summary_payload["teleports"])

    source_manifest = dict(scan_manifest["frozen_network"]["source_sha256"])
    scene_sources = dict(scan_manifest["identity"]["scene"]["files"])
    source_match = source_manifest == current_source_hashes == scene_sources
    if not source_match:
        raise ValueError(f"{level} seed {seed}: source hash mismatch")

    hard_invalid_count = (
        counts["collision"] + counts["teleport"] + counts["sumo_error"]
    )
    quality_flag_count = counts["emergency_braking"]
    row = {
        "level": level,
        "scan_id": scan_id,
        "experiment_id": planned_identity["experiment_id"],
        "seed": seed,
        "selected_attempt": int(attempt_dir.name.removeprefix("attempt_")),
        "selected_output_dir": str(attempt_dir.resolve()),
        "artifact_manifest_sha256": sha256_file(attempt_dir / "artifact_manifest.json"),
        "valid": True,
        "window_count": len(windows),
        "window_grid_complete": actual_times == EXPECTED_TIMES,
        "native_detector_count": len(native_files),
        "native_detector_ids": "|".join(native_ids),
        "teleports": int(summary_payload["teleports"]),
        "collision_event_count": counts["collision"],
        "teleport_event_count": counts["teleport"],
        "emergency_event_count": counts["emergency_braking"],
        "sumo_error_event_count": counts["sumo_error"],
        "sumo_error_log_nonempty": sumo_error_nonempty,
        "quality_flag_count": quality_flag_count,
        "hard_invalid_count": hard_invalid_count,
        "source_sha256_match": source_match,
        "raw_net_sha256": raw_net_sha256,
        "raw_net_sha256_match": raw_net_sha256 == scan_manifest["frozen_network"]["net_sha256"],
        "semantic_net_sha256": semantic_sha256,
        "semantic_net_sha256_match": semantic_sha256 == EXPECTED_SEMANTIC_SHA256,
        "config_sha256": planned_identity["config_sha256"],
        "scan_identity_sha256": planned_identity["scan_identity_sha256"],
        "actual_departure_ratio": summary_payload["actual_departure_ratio"],
        "completion_ratio": summary_payload["completion_ratio"],
        "terminal_in_network_count": summary_payload["terminal_in_network_count"],
        "terminal_pending_count": summary_payload["terminal_pending_count"],
        "failure_reason": summary_payload["failure_reason"],
    }
    return row, windows, events


def _aggregate_timeseries(
    windows_by_level: dict[str, list[tuple[Path, list[dict[str, str]]]]]
) -> list[dict[str, object]]:
    metric_fields = (
        "mean_speed_mps",
        "bottleneck_flow_veh",
        "ramp_vehicle_max_veh",
        "actual_departure_ratio",
    )
    rows: list[dict[str, object]] = []
    for level, _, _, _ in LEVELS:
        sources = windows_by_level[level]
        if len(sources) != len(SEEDS):
            raise ValueError(f"{level}: expected five selected window sources")
        source_paths = [str(path.resolve()) for path, _ in sources]
        by_time = {
            int(row["time_s"]): []
            for _, windows in sources
            for row in windows
        }
        for _, windows in sources:
            for row in windows:
                by_time[int(row["time_s"])].append(row)
        for time_s in EXPECTED_TIMES:
            source_rows = by_time.get(time_s, [])
            if len(source_rows) != len(SEEDS):
                raise ValueError(f"{level} at {time_s}: expected five seed rows")
            aggregate: dict[str, object] = {
                "level": level,
                "time_s": time_s,
                "seed_count": len(source_rows),
                "source_output_dirs_json": json.dumps(source_paths, ensure_ascii=False),
            }
            for field in metric_fields:
                values = [
                    value
                    for value in (_to_float(row.get(field)) for row in source_rows)
                    if value is not None
                ]
                aggregate[f"{field}_observed_seed_count"] = len(values)
                aggregate[f"{field}_mean"] = (
                    sum(values) / len(values) if values else ""
                )
                aggregate[f"{field}_min"] = min(values) if values else ""
                aggregate[f"{field}_max"] = max(values) if values else ""
            rows.append(aggregate)
    return rows


def collect_evidence() -> dict[str, object]:
    current_sources = _source_hashes()
    run_index_rows: list[dict[str, object]] = []
    quality_rows: list[dict[str, object]] = []
    event_rows: list[dict[str, object]] = []
    windows_by_level: dict[str, list[tuple[Path, list[dict[str, str]]]]] = defaultdict(list)
    scan_records: list[dict[str, object]] = []

    for level, mainline_vph, ramp_vph, scan_id in LEVELS:
        config = _config(mainline_vph, ramp_vph)
        demand = DemandPoint(mainline_vph, ramp_vph)
        scan_dir = FORMAL_SCANS / scan_id
        (
            scan_manifest,
            frozen_network,
            raw_net_sha256,
            semantic_sha256,
            selected,
        ) = _strict_selected_attempts(level, scan_dir, config, demand)
        selected_paths = {
            str(attempt_dir.resolve()) for attempt_dir, _, _ in selected.values()
        }

        scan_rows = _read_csv(scan_dir / "run_index.csv")
        for original in scan_rows:
            row: dict[str, object] = {
                "level": level,
                "scan_id": scan_id,
                "selected": str(Path(original["output_dir"]).resolve()) in selected_paths,
            }
            row.update(original)
            run_index_rows.append(row)

        for seed in SEEDS:
            attempt_dir, summary, planned_identity = selected[seed]
            quality, windows, events = _quality_row(
                level,
                scan_id,
                seed,
                attempt_dir,
                summary,
                planned_identity,
                scan_manifest,
                raw_net_sha256,
                semantic_sha256,
                current_sources,
            )
            quality_rows.append(quality)
            windows_by_level[level].append((attempt_dir, windows))
            for event in events:
                event_rows.append(
                    {
                        "level": level,
                        "seed": seed,
                        "output_dir": str(attempt_dir.resolve()),
                        **event,
                    }
                )

        net_path = Path(str(frozen_network.net_path))
        raw_prefix = net_path.read_text(encoding="utf-8", errors="replace").split("<net", 1)[0]
        scan_records.append(
            {
                "level": level,
                "scan_id": scan_id,
                "scan_dir": str(scan_dir.resolve()),
                "scan_manifest_sha256": sha256_file(scan_dir / "scan_manifest.json"),
                "scan_identity_sha256": scan_manifest["identity_sha256"],
                "raw_net_path": str(net_path.resolve()),
                "raw_net_sha256": raw_net_sha256,
                "raw_header_sha256": hashlib.sha256(raw_prefix.encode("utf-8")).hexdigest(),
                "semantic_net_sha256": semantic_sha256,
                "source_sha256": current_sources,
            }
        )

    run_index_rows.sort(
        key=lambda row: (
            {"low": 0, "medium": 1, "high": 2}[str(row["level"])],
            int(row["seed"]),
            int(row["attempt"] or 0),
        )
    )
    quality_rows.sort(
        key=lambda row: (
            {"low": 0, "medium": 1, "high": 2}[str(row["level"])],
            int(row["seed"]),
        )
    )
    timeseries_rows = _aggregate_timeseries(windows_by_level)
    if len(run_index_rows) != 16:
        raise ValueError(f"expected 16 attempt history rows, got {len(run_index_rows)}")
    if sum(bool(row["selected"]) for row in run_index_rows) != 15:
        raise ValueError("expected exactly 15 selected attempt rows")
    if len(quality_rows) != 15:
        raise ValueError("expected exactly 15 quality rows")
    return {
        "run_index_rows": run_index_rows,
        "quality_rows": quality_rows,
        "timeseries_rows": timeseries_rows,
        "event_rows": event_rows,
        "scan_records": scan_records,
        "source_sha256": current_sources,
    }


def _font(size: int, bold: bool = False):
    from PIL import ImageFont

    names = (
        "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/segoeuib.ttf" if bold else "C:/Windows/Fonts/segoeui.ttf",
    )
    for name in names:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _draw_plot(timeseries_rows: list[dict[str, object]], output_path: Path) -> None:
    from PIL import Image, ImageDraw

    width, height = 2040, 1260
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    title_font = _font(25, True)
    label_font = _font(18, True)
    tick_font = _font(14)
    colors = {"low": "#2474b5", "medium": "#e08b20", "high": "#b33a3a"}
    metrics = (
        ("mean_speed_mps", "Mean speed", "m/s"),
        ("bottleneck_flow_veh", "Bottleneck flow", "veh / 30 s"),
        ("ramp_vehicle_max_veh", "Ramp max queue", "veh"),
        ("actual_departure_ratio", "Actual departure ratio", "ratio"),
    )
    left_margin, top_margin = 120, 100
    panel_w, panel_h = 450, 330
    col_gap, row_gap = 35, 55
    draw.text((left_margin, 28), "Stage 1 frozen matrix — five-seed time-series aggregates", fill="#202020", font=title_font)
    draw.text((left_margin, 62), "Solid line: mean; light band: seed min–max; vertical lines: 600 s and 2400 s", fill="#555555", font=tick_font)

    grouped: dict[tuple[str, str], list[dict[str, object]]] = defaultdict(list)
    for row in timeseries_rows:
        for metric, _, _ in metrics:
            grouped[(str(row["level"]), metric)].append(row)

    global_ranges: dict[str, tuple[float, float]] = {}
    for metric, _, _ in metrics:
        values = []
        for level, _, _, _ in LEVELS:
            for row in grouped[(level, metric)]:
                for suffix in ("min", "max"):
                    value = row[f"{metric}_{suffix}"]
                    if value != "":
                        values.append(float(value))
        low, high = min(values), max(values)
        padding = (high - low) * 0.08 if high > low else max(1.0, abs(high) * 0.08)
        if metric in {"bottleneck_flow_veh", "ramp_vehicle_max_veh", "actual_departure_ratio"}:
            low = max(0.0, low - padding)
        else:
            low -= padding
        global_ranges[metric] = (low, high + padding)

    for row_index, (level, _, _, _) in enumerate(LEVELS):
        draw.text((25, top_margin + row_index * (panel_h + row_gap) + 10), level.upper(), fill=colors[level], font=label_font)
        for col_index, (metric, title, unit) in enumerate(metrics):
            x0 = left_margin + col_index * (panel_w + col_gap)
            y0 = top_margin + row_index * (panel_h + row_gap)
            x1, y1 = x0 + panel_w, y0 + panel_h
            plot_left, plot_top = x0 + 62, y0 + 42
            plot_right, plot_bottom = x1 - 18, y1 - 48
            draw.rectangle((plot_left, plot_top, plot_right, plot_bottom), outline="#777777", width=1)
            draw.text((x0 + 8, y0 + 8), f"{title} ({unit})", fill="#222222", font=label_font)
            y_min, y_max = global_ranges[metric]
            for fraction in (0.0, 0.5, 1.0):
                y = plot_bottom - fraction * (plot_bottom - plot_top)
                value = y_min + fraction * (y_max - y_min)
                draw.line((plot_left, y, plot_right, y), fill="#e0e0e0", width=1)
                draw.text((x0 + 2, y - 7), f"{value:.2f}", fill="#555555", font=tick_font)
            for time_s in (600, 2400):
                x = plot_left + time_s / 3600 * (plot_right - plot_left)
                draw.line((x, plot_top, x, plot_bottom), fill="#777777", width=2)
            for time_s in (0, 600, 2400, 3600):
                x = plot_left + time_s / 3600 * (plot_right - plot_left)
                draw.text((x - 16, plot_bottom + 10), str(time_s), fill="#555555", font=tick_font)

            source_rows = grouped[(level, metric)]
            band_upper = []
            band_lower = []
            mean_points = []
            for row in source_rows:
                time_s = int(row["time_s"])
                mean = row[f"{metric}_mean"]
                minimum = row[f"{metric}_min"]
                maximum = row[f"{metric}_max"]
                if mean == "" or minimum == "" or maximum == "":
                    continue
                x = plot_left + time_s / 3600 * (plot_right - plot_left)
                scale = (plot_bottom - plot_top) / (y_max - y_min)
                y_mean = plot_bottom - (float(mean) - y_min) * scale
                y_low = plot_bottom - (float(minimum) - y_min) * scale
                y_high = plot_bottom - (float(maximum) - y_min) * scale
                mean_points.append((x, y_mean))
                band_upper.append((x, y_high))
                band_lower.append((x, y_low))
            if band_upper and band_lower:
                draw.polygon(band_upper + list(reversed(band_lower)), fill="#d9e5ef")
            if len(mean_points) > 1:
                draw.line(mean_points, fill=colors[level], width=3)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output_path.stem}.", suffix=".png", dir=output_path.parent
    )
    os.close(descriptor)
    temporary_path = Path(temporary_name)
    try:
        image.save(temporary_path, format="PNG", optimize=True)
        os.replace(temporary_path, output_path)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise


def _task_report(evidence: dict[str, object]) -> str:
    quality_rows = evidence["quality_rows"]
    event_rows = evidence["event_rows"]
    raw_hashes = [record["raw_net_sha256"] for record in evidence["scan_records"]]
    semantic_hashes = [record["semantic_net_sha256"] for record in evidence["scan_records"]]
    flagged = sum(int(row["quality_flag_count"]) > 0 for row in quality_rows)
    hard_invalid = sum(int(row["hard_invalid_count"]) > 0 for row in quality_rows)
    return f"""# 1. 修改了哪些文件

Task 5-B 只在 `outputs/stage_1/` 顶层创建或更新只读汇总脚本、测试、CSV、JSON、PNG 和本报告；未修改 `formal_scans/` 内原始证据，也未修改 production、tests、docs 或 network。commit none。

# 2. 实现/产出了什么

保留三个 CR-04 scan 的全部 16 行 attempt 历史（15 行 selected valid，1 行 medium 超时遗留 invalid），并对 15 个 selected attempt 逐项复用 CR-04 严格 artifact/identity/config/network/attempt/windows/summary 校验。生成 15 行质量汇总、360 行五 seed 聚合时序和低中高对齐核查图。日志事件按 `(vehicle,lane,time,type)` 去重，共 {len(event_rows)} 个唯一事件。

# 3. 运行了哪些测试/验证及原始结果

- attempt 历史：16 行；selected：15；invalid history：1。
- selected 有效性：15/15；每次完整窗口：15/15 × 120；原生 detector XML：15/15 × 8。
- hard invalid：{hard_invalid}；Stage 1 safety quality flagged runs：{flagged}；唯一 emergency braking：1；collision：0；teleport：0；SUMO Error/Fatal：0。
- raw net SHA-256：`{raw_hashes[0]}`、`{raw_hashes[1]}`、`{raw_hashes[2]}`（原样保留）。
- semantic net SHA-256：{len(set(semantic_hashes))} 个唯一值，均为 `{EXPECTED_SEMANTIC_SHA256}`。

# 4. 剩余风险或疑问

三份 raw net 字节哈希不同，因为 netconvert XML 注释头包含生成时间和调用/输出路径；ElementTree 根元素序列化会排除注释，三份语义哈希一致。high seed 2 的一次 emergency braking 按 protocol 8.1 不构成硬无效，作为 Stage 1 safety quality flag 保留，并转交内容3；该事件不作为 collision，也未删 seed、改参数或重跑掩盖。本文不解释自由流、临界或拥堵边界。
"""


def write_outputs(evidence: dict[str, object]) -> dict[str, Path]:
    outputs = {
        "run_index": OUT / "run_index.csv",
        "quality_summary": OUT / "quality_summary.csv",
        "timeseries_data": OUT / "scene_timeseries_data.csv",
        "plot": OUT / "scene_timeseries_overview.png",
        "manifest": OUT / "scene_matrix_manifest.json",
        "task_report": OUT / "task-report.md",
    }
    _atomic_write_csv(outputs["run_index"], evidence["run_index_rows"])
    _atomic_write_csv(outputs["quality_summary"], evidence["quality_rows"])
    _atomic_write_csv(outputs["timeseries_data"], evidence["timeseries_rows"])
    _draw_plot(evidence["timeseries_rows"], outputs["plot"])
    _atomic_write_text(outputs["task_report"], _task_report(evidence))

    quality_rows = evidence["quality_rows"]
    manifest = {
        "schema_version": "stage1-scene-matrix-manifest-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "purpose": "Task 5-B read-only consolidation; no SUMO execution and no traffic-boundary interpretation",
        "matrix": {
            "levels": [
                {
                    "level": level,
                    "mainline_vph": mainline,
                    "ramp_vph": ramp,
                    "scan_id": scan_id,
                }
                for level, mainline, ramp, scan_id in LEVELS
            ],
            "controller": "none",
            "seeds": list(SEEDS),
            "simulation_duration_s": 3600,
            "step_length_s": 1.0,
            "metrics_interval_s": 30,
            "demand_phases": [
                {"begin_s": begin, "end_s": end, "multiplier": multiplier}
                for begin, end, multiplier in EXPECTED_PHASES
            ],
        },
        "validation": {
            "attempt_history_rows": len(evidence["run_index_rows"]),
            "selected_runs": sum(bool(row["selected"]) for row in evidence["run_index_rows"]),
            "invalid_history_rows": sum(str(row["status"]) != "valid" for row in evidence["run_index_rows"]),
            "quality_rows": len(quality_rows),
            "complete_120_window_runs": sum(int(row["window_count"]) == 120 for row in quality_rows),
            "eight_detector_runs": sum(int(row["native_detector_count"]) == 8 for row in quality_rows),
            "hard_invalid_runs": sum(int(row["hard_invalid_count"]) > 0 for row in quality_rows),
            "flagged_runs": sum(int(row["quality_flag_count"]) > 0 for row in quality_rows),
            "unique_emergency_events": sum(int(row["emergency_event_count"]) for row in quality_rows),
            "collision_events": sum(int(row["collision_event_count"]) for row in quality_rows),
            "teleport_events": sum(int(row["teleport_event_count"]) for row in quality_rows),
            "sumo_error_events": sum(int(row["sumo_error_event_count"]) for row in quality_rows),
        },
        "network": {
            "source_sha256": evidence["source_sha256"],
            "raw_net_sha256_by_scan": {
                record["scan_id"]: record["raw_net_sha256"]
                for record in evidence["scan_records"]
            },
            "raw_header_sha256_by_scan": {
                record["scan_id"]: record["raw_header_sha256"]
                for record in evidence["scan_records"]
            },
            "raw_hashes_differ": len({record["raw_net_sha256"] for record in evidence["scan_records"]}) > 1,
            "raw_difference_explanation": "netconvert XML comment headers embed generation timestamps and invocation/output paths; those comments are excluded from ElementTree root serialization",
            "semantic_algorithm": "sha256(ElementTree.tostring(parsed_root, encoding='utf-8')); XML comments excluded",
            "semantic_expected_sha256": EXPECTED_SEMANTIC_SHA256,
            "semantic_net_sha256_by_scan": {
                record["scan_id"]: record["semantic_net_sha256"]
                for record in evidence["scan_records"]
            },
            "semantic_hashes_match": all(
                record["semantic_net_sha256"] == EXPECTED_SEMANTIC_SHA256
                for record in evidence["scan_records"]
            ),
        },
        "scan_records": evidence["scan_records"],
        "deduplicated_log_events": evidence["event_rows"],
        "outputs": {},
    }
    for name, path in outputs.items():
        if name == "manifest":
            continue
        manifest["outputs"][name] = {
            "path": str(path.relative_to(ROOT)).replace("\\", "/"),
            "bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }
    _atomic_write_json(outputs["manifest"], manifest)
    return outputs


def validate_outputs(outputs: dict[str, Path]) -> dict[str, object]:
    from PIL import Image

    run_rows = _read_csv(outputs["run_index"])
    quality_rows = _read_csv(outputs["quality_summary"])
    timeseries_rows = _read_csv(outputs["timeseries_data"])
    manifest = json.loads(outputs["manifest"].read_text(encoding="utf-8"))
    with Image.open(outputs["plot"]) as image:
        image.verify()
    sections = [
        line for line in outputs["task_report"].read_text(encoding="utf-8").splitlines()
        if line.startswith("# ")
    ]
    checks = {
        "run_index_rows": len(run_rows),
        "selected_rows": sum(row["selected"] == "True" for row in run_rows),
        "invalid_history_rows": sum(row["status"] != "valid" for row in run_rows),
        "quality_rows": len(quality_rows),
        "timeseries_rows": len(timeseries_rows),
        "task_report_sections": len(sections),
        "png_bytes": outputs["plot"].stat().st_size,
        "manifest_validation": manifest["validation"],
        "temporary_files": len(list(OUT.glob(".*.tmp"))),
    }
    expected = {
        "run_index_rows": 16,
        "selected_rows": 15,
        "invalid_history_rows": 1,
        "quality_rows": 15,
        "timeseries_rows": 360,
        "task_report_sections": 4,
    }
    for key, value in expected.items():
        if checks[key] != value:
            raise ValueError(f"output validation failed: {key}={checks[key]}, expected {value}")
    if checks["temporary_files"] != 0 or checks["png_bytes"] <= 0:
        raise ValueError("output validation failed: PNG or temporary file check")
    return checks


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="validate existing top-level summary outputs without rewriting them",
    )
    args = parser.parse_args()
    output_paths = {
        "run_index": OUT / "run_index.csv",
        "quality_summary": OUT / "quality_summary.csv",
        "timeseries_data": OUT / "scene_timeseries_data.csv",
        "plot": OUT / "scene_timeseries_overview.png",
        "manifest": OUT / "scene_matrix_manifest.json",
        "task_report": OUT / "task-report.md",
    }
    if not args.validate_only:
        evidence = collect_evidence()
        output_paths = write_outputs(evidence)
    checks = validate_outputs(output_paths)
    print(json.dumps(checks, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
