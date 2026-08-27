from experiment_config import default_config
from scan_demand import planned_runs


def test_planned_runs_pairs_controllers_by_demand_and_seed():
    config = default_config()
    config.mainline_range = (3000, 3000, 300)
    config.ramp_range = (300, 450, 150)
    config.seeds = (0, 1)
    runs = planned_runs(config, ("none", "alinea"))
    assert len(runs) == 8
    assert runs[0][1:] == ("none", 0)
    assert runs[1][1:] == ("alinea", 0)
