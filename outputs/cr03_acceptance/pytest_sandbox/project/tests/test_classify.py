from classify import classify_demand_pair
from metrics import EpisodeSummary


def summary(controller, seed, tts, breakdown, queue, valid=True):
    return EpisodeSummary(
        experiment_id=f"{controller}-{seed}",
        controller=controller,
        seed=seed,
        mainline_vph=4200,
        ramp_vph=750,
        valid=valid,
        failure_reason="",
        total_time_spent_s=tts,
        mean_speed_mps=25.0,
        bottleneck_throughput_veh=1000,
        max_ramp_queue_veh=queue,
        breakdown_start_s=breakdown,
        congestion_duration_s=600 if breakdown else 0,
        recovered=True,
        teleports=0,
    )


def test_classify_candidate_controllable_from_paired_improvement():
    no_control = [summary("none", i, 10000, 900, 10) for i in range(5)]
    alinea = [summary("alinea", i, 9000, 0, 15) for i in range(5)]
    result = classify_demand_pair(no_control, alinea, 33.33, 0.05, 0.8, 80)
    assert result.label == "candidate_controllable"
    assert result.paired_tts_improvement == 0.1


def test_classify_uncontrollable_when_ramp_spillback_occurs():
    no_control = [summary("none", i, 10000, 900, 10) for i in range(5)]
    alinea = [summary("alinea", i, 9000, 0, 100) for i in range(5)]
    result = classify_demand_pair(no_control, alinea, 33.33, 0.05, 0.8, 80)
    assert result.label == "uncontrollable"
    assert "ramp_spillback" in result.failure_reason
