from __future__ import annotations

from dataclasses import dataclass

from metrics import EpisodeSummary


@dataclass
class DemandClassification:
    mainline_vph: int
    ramp_vph: int
    label: str
    valid_seed_count: int
    no_control_congestion_prob: float
    alinea_congestion_prob: float
    paired_tts_improvement: float
    positive_pair_count: int
    failure_reason: str


def classify_episode(summary: EpisodeSummary, free_flow_speed_mps: float) -> str:
    if not summary.valid:
        return "invalid"
    if summary.breakdown_start_s > 0:
        return "congested"
    if summary.mean_speed_mps >= free_flow_speed_mps * 0.85:
        return "free_flow"
    return "critical"


def classify_demand_pair(
    no_control: list[EpisodeSummary],
    alinea: list[EpisodeSummary],
    free_flow_speed_mps: float,
    controllable_improvement_threshold: float,
    no_control_congestion_threshold: float,
    ramp_storage_veh: int,
) -> DemandClassification:
    no_by_seed = {summary.seed: summary for summary in no_control if summary.valid}
    alinea_by_seed = {summary.seed: summary for summary in alinea if summary.valid}
    paired_seeds = sorted(set(no_by_seed) & set(alinea_by_seed))

    if not paired_seeds:
        return _classification(no_control, alinea, "insufficient_valid_runs", 0, 0.0, 0.0, 0.0, 0, "no_valid_pairs")

    no_valid = [no_by_seed[seed] for seed in paired_seeds]
    alinea_valid = [alinea_by_seed[seed] for seed in paired_seeds]
    no_congestion_prob = _congestion_probability(no_valid)
    alinea_congestion_prob = _congestion_probability(alinea_valid)
    no_tts_mean = _mean([summary.total_time_spent_s for summary in no_valid])
    alinea_tts_mean = _mean([summary.total_time_spent_s for summary in alinea_valid])
    improvement = 0.0 if no_tts_mean <= 0 else (no_tts_mean - alinea_tts_mean) / no_tts_mean
    positive_pair_count = sum(
        1
        for seed in paired_seeds
        if no_by_seed[seed].total_time_spent_s > alinea_by_seed[seed].total_time_spent_s
    )

    ramp_spillback = any(summary.max_ramp_queue_veh > ramp_storage_veh for summary in alinea_valid)
    both_congested = no_congestion_prob >= no_control_congestion_threshold and alinea_congestion_prob >= no_control_congestion_threshold
    if ramp_spillback:
        label = "uncontrollable"
        reason = "ramp_spillback"
    elif both_congested:
        label = "uncontrollable"
        reason = "both_controls_congested"
    elif no_congestion_prob >= no_control_congestion_threshold and improvement >= controllable_improvement_threshold and positive_pair_count > 0:
        label = "candidate_controllable"
        reason = ""
    elif no_congestion_prob == 0 and alinea_congestion_prob == 0:
        label = "free_flow"
        reason = ""
    elif 0 < no_congestion_prob < no_control_congestion_threshold:
        label = "critical"
        reason = ""
    else:
        label = "congested"
        reason = ""

    return _classification(
        no_control,
        alinea,
        label,
        len(paired_seeds),
        no_congestion_prob,
        alinea_congestion_prob,
        round(improvement, 6),
        positive_pair_count,
        reason,
    )


def _classification(
    no_control: list[EpisodeSummary],
    alinea: list[EpisodeSummary],
    label: str,
    valid_seed_count: int,
    no_control_congestion_prob: float,
    alinea_congestion_prob: float,
    paired_tts_improvement: float,
    positive_pair_count: int,
    failure_reason: str,
) -> DemandClassification:
    source = (no_control or alinea)[0] if (no_control or alinea) else None
    return DemandClassification(
        mainline_vph=source.mainline_vph if source else 0,
        ramp_vph=source.ramp_vph if source else 0,
        label=label,
        valid_seed_count=valid_seed_count,
        no_control_congestion_prob=round(no_control_congestion_prob, 6),
        alinea_congestion_prob=round(alinea_congestion_prob, 6),
        paired_tts_improvement=paired_tts_improvement,
        positive_pair_count=positive_pair_count,
        failure_reason=failure_reason,
    )


def _congestion_probability(summaries: list[EpisodeSummary]) -> float:
    if not summaries:
        return 0.0
    return sum(1 for summary in summaries if summary.breakdown_start_s > 0) / len(summaries)


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0
