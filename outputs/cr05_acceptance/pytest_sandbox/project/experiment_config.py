from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field


@dataclass(frozen=True)
class DemandPoint:
    mainline_vph: int
    ramp_vph: int


@dataclass
class DemandPhase:
    begin_s: int
    end_s: int
    multiplier: float


@dataclass
class AlineaConfig:
    target_occupancy: float = 0.22
    gain: float = 70.0
    initial_rate_vph: float = 900.0
    min_rate_vph: float = 240.0
    max_rate_vph: float = 1200.0
    cycle_s: int = 30
    queue_override_ratio: float = 0.8
    ramp_storage_veh: int = 80


@dataclass
class ExperimentConfig:
    simulation_duration_s: int = 3600
    step_length_s: float = 1.0
    metrics_interval_s: int = 30
    mainline_range: tuple[int, int, int] = (3000, 6000, 300)
    ramp_range: tuple[int, int, int] = (300, 1200, 150)
    seeds: tuple[int, ...] = tuple(range(10))
    free_flow_speed_mps: float = 33.33
    congestion_speed_ratio: float = 0.65
    free_flow_speed_ratio: float = 0.85
    congestion_min_duration_s: int = 300
    controllable_improvement_threshold: float = 0.05
    no_control_congestion_threshold: float = 0.8
    demand_phases: list[DemandPhase] = field(
        default_factory=lambda: [
            DemandPhase(0, 600, 0.65),
            DemandPhase(600, 2400, 1.0),
            DemandPhase(2400, 3600, 0.70),
        ]
    )
    alinea: AlineaConfig = field(default_factory=AlineaConfig)


def default_config() -> ExperimentConfig:
    return ExperimentConfig()


def iter_demand_grid(config: ExperimentConfig) -> list[DemandPoint]:
    validate_config(config)
    main_start, main_end, main_step = config.mainline_range
    ramp_start, ramp_end, ramp_step = config.ramp_range
    return [
        DemandPoint(mainline_vph=mainline, ramp_vph=ramp)
        for mainline in range(main_start, main_end + 1, main_step)
        for ramp in range(ramp_start, ramp_end + 1, ramp_step)
    ]


def experiment_id(config: ExperimentConfig, demand: DemandPoint, controller: str, seed: int) -> str:
    snapshot = _config_snapshot(config)
    digest = hashlib.sha1(json.dumps(snapshot, sort_keys=True).encode("utf-8")).hexdigest()[:8]
    return f"main{demand.mainline_vph}_ramp{demand.ramp_vph}_{controller}_seed{seed}_{digest}"


def validate_config(config: ExperimentConfig) -> None:
    if config.simulation_duration_s <= 0:
        raise ValueError("simulation_duration_s must be positive")
    if config.metrics_interval_s <= 0:
        raise ValueError("metrics_interval_s must be positive")
    if config.simulation_duration_s % config.metrics_interval_s != 0:
        raise ValueError("simulation_duration_s must be divisible by metrics_interval_s")
    _validate_range("mainline_range", config.mainline_range)
    _validate_range("ramp_range", config.ramp_range)
    if not config.seeds:
        raise ValueError("seeds must not be empty")
    if config.demand_phases[0].begin_s != 0:
        raise ValueError("demand phase sequence must start at 0")
    previous_end = 0
    for phase in config.demand_phases:
        if phase.begin_s != previous_end or phase.end_s <= phase.begin_s:
            raise ValueError("demand phase sequence must be contiguous and ordered")
        if phase.multiplier < 0:
            raise ValueError("demand phase multiplier must be non-negative")
        previous_end = phase.end_s
    if previous_end != config.simulation_duration_s:
        raise ValueError("demand phase sequence must end at simulation_duration_s")
    if config.alinea.min_rate_vph > config.alinea.max_rate_vph:
        raise ValueError("ALINEA min_rate_vph must be <= max_rate_vph")
    if not 0 < config.alinea.queue_override_ratio <= 1:
        raise ValueError("ALINEA queue_override_ratio must be in (0, 1]")


def _validate_range(name: str, values: tuple[int, int, int]) -> None:
    start, end, step = values
    if step <= 0:
        raise ValueError(f"{name} step must be positive")
    if start > end:
        raise ValueError(f"{name} start must be <= end")


def _config_snapshot(config: ExperimentConfig) -> dict[str, object]:
    return asdict(config)
