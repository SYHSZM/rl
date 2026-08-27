from env import run_experiment
from experiment_config import DemandPoint, default_config


config = default_config()
config.simulation_duration_s = 180
config.metrics_interval_s = 30
config.demand_phases = config.demand_phases[:1]
config.demand_phases[0].begin_s = 0
config.demand_phases[0].end_s = 180
result = run_experiment(
    config,
    DemandPoint(1200, 120),
    "alinea",
    0,
    output_root="outputs/repro_0_2_1/alinea",
)
print(result)
