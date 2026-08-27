import json
from dataclasses import asdict

from classify import classify_demand_pair
from experiment_config import DemandPoint
from env import _invalid_summary


def valid_summary(controller: str, seed: int, tts: float):
    summary = _invalid_summary("valid-placeholder", controller, seed, DemandPoint(1200, 120), "")
    summary.valid = True
    summary.failure_reason = ""
    summary.total_time_spent_s = tts
    summary.mean_speed_mps = 30.0
    return summary


invalid_none = _invalid_summary("bad-none", "none", 0, DemandPoint(1200, 120), "SUMO failed: synthetic")
invalid_alinea = _invalid_summary("bad-alinea", "alinea", 0, DemandPoint(1200, 120), "TraCI unavailable: synthetic")
valid_none_seed1 = valid_summary("none", 1, 100.0)
valid_alinea_seed1 = valid_summary("alinea", 1, 90.0)

cases = {
    "invalid_only_both_controllers": asdict(classify_demand_pair(
        [invalid_none], [invalid_alinea], 33.33, 0.05, 0.8, 80,
    )),
    "invalid_seed0_plus_valid_seed1": asdict(classify_demand_pair(
        [invalid_none, valid_none_seed1], [invalid_alinea, valid_alinea_seed1], 33.33, 0.05, 0.8, 80,
    )),
    "invalid_summary_none": asdict(invalid_none),
    "invalid_summary_alinea": asdict(invalid_alinea),
}
print(json.dumps(cases, ensure_ascii=False, indent=2))
