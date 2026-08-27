from __future__ import annotations

import argparse
import csv
from dataclasses import asdict, fields
from datetime import datetime
from pathlib import Path

from build_network import preflight_network
from classify import DemandClassification, classify_demand_pair
from env import run_experiment
from experiment_config import DemandPoint, ExperimentConfig, default_config, iter_demand_grid, validate_config
from metrics import EpisodeSummary


def planned_runs(config: ExperimentConfig, controllers: tuple[str, ...]) -> list[tuple[DemandPoint, str, int]]:
    validate_config(config)
    return [(demand, controller, seed) for demand in iter_demand_grid(config) for seed in config.seeds for controller in controllers]


def scan_demand(
    config: ExperimentConfig | None = None,
    output_root: str | Path = "outputs/scan",
    controllers: tuple[str, ...] = ("none", "alinea"),
    resume: bool = True,
    use_gui: bool = False,
) -> Path:
    config = config or default_config()
    validate_config(config)
    scan_dir = _allocate_scan_dir(output_root, datetime.now().strftime("%Y%m%d_%H%M%S"))
    frozen_network = preflight_network(scan_dir / "preflight" / "merge.net.xml")
    index_rows: list[dict[str, object]] = []
    summaries: list[EpisodeSummary] = []

    for demand, controller, seed in planned_runs(config, controllers):
        result = run_experiment(config, demand, controller, seed, output_root=scan_dir / "runs", use_gui=use_gui, frozen_network=frozen_network)
        index_rows.append(
            {
                "experiment_id": result.experiment_id,
                "mainline_vph": demand.mainline_vph,
                "ramp_vph": demand.ramp_vph,
                "controller": controller,
                "seed": seed,
                "status": "valid" if result.valid else "invalid",
                "output_dir": str(result.output_dir),
                "failure_reason": result.failure_reason,
            }
        )
        summaries.append(result.summary)
        _write_index(scan_dir / "run_index.csv", index_rows)

    classifications = _classify_scan(config, summaries)
    _write_classifications(scan_dir / "demand_classification.csv", classifications)
    return scan_dir


def _allocate_scan_dir(output_root: str | Path, timestamp: str) -> Path:
    output_root = Path(output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    suffix = 0
    while True:
        name = timestamp if suffix == 0 else f"{timestamp}_{suffix:04d}"
        candidate = output_root / name
        try:
            candidate.mkdir(exist_ok=False)
        except FileExistsError:
            suffix += 1
        else:
            return candidate


def _classify_scan(config: ExperimentConfig, summaries: list[EpisodeSummary]) -> list[DemandClassification]:
    classifications = []
    for demand in iter_demand_grid(config):
        no_control = [
            summary
            for summary in summaries
            if summary.controller == "none" and summary.mainline_vph == demand.mainline_vph and summary.ramp_vph == demand.ramp_vph
        ]
        alinea = [
            summary
            for summary in summaries
            if summary.controller == "alinea" and summary.mainline_vph == demand.mainline_vph and summary.ramp_vph == demand.ramp_vph
        ]
        if no_control or alinea:
            classifications.append(
                classify_demand_pair(
                    no_control,
                    alinea,
                    config.free_flow_speed_mps,
                    config.controllable_improvement_threshold,
                    config.no_control_congestion_threshold,
                    config.alinea.ramp_storage_veh,
                )
            )
    return classifications


def _write_index(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ["experiment_id", "mainline_vph", "ramp_vph", "controller", "seed", "status", "output_dir", "failure_reason"]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _write_classifications(path: Path, rows: list[DemandClassification]) -> None:
    names = [field.name for field in fields(DemandClassification)]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=names)
        writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))


def _config_from_args(args: argparse.Namespace) -> ExperimentConfig:
    config = default_config()
    if args.smoke:
        config.simulation_duration_s = 180
        config.metrics_interval_s = 30
        config.demand_phases = config.demand_phases[:1]
        config.demand_phases[0].begin_s = 0
        config.demand_phases[0].end_s = 180
        config.mainline_range = (1200, 3600, 2400)
        config.ramp_range = (120, 600, 480)
        config.seeds = (0,)
    if args.mainline_start is not None:
        config.mainline_range = (args.mainline_start, args.mainline_end, args.mainline_step)
    if args.ramp_start is not None:
        config.ramp_range = (args.ramp_start, args.ramp_end, args.ramp_step)
    if args.seeds:
        config.seeds = tuple(int(seed.strip()) for seed in args.seeds.split(",") if seed.strip())
    return config


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--full", action="store_true")
    parser.add_argument("--output-root", default="outputs/scan")
    parser.add_argument("--mainline-start", type=int)
    parser.add_argument("--mainline-end", type=int, default=6000)
    parser.add_argument("--mainline-step", type=int, default=300)
    parser.add_argument("--ramp-start", type=int)
    parser.add_argument("--ramp-end", type=int, default=1200)
    parser.add_argument("--ramp-step", type=int, default=150)
    parser.add_argument("--seeds", default="")
    args = parser.parse_args()
    scan_dir = scan_demand(_config_from_args(args), output_root=args.output_root)
    print(scan_dir)


if __name__ == "__main__":
    main()
