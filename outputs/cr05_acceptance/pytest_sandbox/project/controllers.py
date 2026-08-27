from __future__ import annotations

from dataclasses import dataclass

from experiment_config import AlineaConfig


@dataclass(frozen=True)
class ControlAction:
    requested_rate_vph: float
    applied_rate_vph: float
    green_s: float
    red_s: float


class NoControlController:
    def __init__(self, config: AlineaConfig):
        self.config = config

    def update(self, occupancy: float, queue_veh: int) -> ControlAction:
        return _action_from_rate(self.config.max_rate_vph, self.config.max_rate_vph, self.config)


class AlineaController:
    def __init__(self, config: AlineaConfig):
        self.config = config
        self.previous_rate_vph = config.initial_rate_vph

    def update(self, occupancy: float, queue_veh: int) -> ControlAction:
        requested = self.previous_rate_vph + self.config.gain * (self.config.target_occupancy - occupancy)
        requested = _clip(requested, self.config.min_rate_vph, self.config.max_rate_vph)
        self.previous_rate_vph = requested
        queue_limit = self.config.ramp_storage_veh * self.config.queue_override_ratio
        applied = self.config.max_rate_vph if queue_veh >= queue_limit else requested
        return _action_from_rate(requested, applied, self.config)


def _action_from_rate(requested_rate_vph: float, applied_rate_vph: float, config: AlineaConfig) -> ControlAction:
    green_s = config.cycle_s * applied_rate_vph / config.max_rate_vph
    green_s = _clip(green_s, 0.0, float(config.cycle_s))
    return ControlAction(
        requested_rate_vph=float(requested_rate_vph),
        applied_rate_vph=float(applied_rate_vph),
        green_s=float(green_s),
        red_s=float(config.cycle_s - green_s),
    )


def _clip(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))
