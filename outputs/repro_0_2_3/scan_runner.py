import sys

from experiment_config import default_config
from scan_demand import scan_demand


output_root = sys.argv[1]
config = default_config()
config.simulation_duration_s = 180
config.metrics_interval_s = 30
config.demand_phases = config.demand_phases[:1]
config.demand_phases[0].begin_s = 0
config.demand_phases[0].end_s = 180
config.mainline_range = (1200, 1200, 1)
config.ramp_range = (120, 120, 1)
config.seeds = (0,)
scan_dir = scan_demand(
    config=config,
    output_root=output_root,
    controllers=("none", "alinea"),
    resume=True,
    use_gui=False,
)
print(scan_dir)
