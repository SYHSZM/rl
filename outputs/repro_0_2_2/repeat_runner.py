import sys

from env import run_experiment
from experiment_config import DemandPoint, default_config


output_root = sys.argv[1]
seed = int(sys.argv[2])
controller = sys.argv[3]
config = default_config()
config.simulation_duration_s = 180
config.metrics_interval_s = 30
config.demand_phases = config.demand_phases[:1]
config.demand_phases[0].begin_s = 0
config.demand_phases[0].end_s = 180
result = run_experiment(
    config,
    DemandPoint(1200, 120),
    controller,
    seed,
    output_root=output_root,
)
print(result)
