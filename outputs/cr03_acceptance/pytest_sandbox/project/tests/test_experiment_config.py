from experiment_config import DemandPoint, default_config, experiment_id, iter_demand_grid, validate_config


def test_default_demand_grid_bounds_and_count():
    config = default_config()
    grid = iter_demand_grid(config)
    assert grid[0] == DemandPoint(mainline_vph=3000, ramp_vph=300)
    assert grid[-1] == DemandPoint(mainline_vph=6000, ramp_vph=1200)
    assert len(grid) == 11 * 7


def test_experiment_id_is_deterministic_and_contains_key_fields():
    config = default_config()
    demand = DemandPoint(mainline_vph=4200, ramp_vph=750)
    first = experiment_id(config, demand, "alinea", 3)
    second = experiment_id(config, demand, "alinea", 3)
    assert first == second
    assert "main4200" in first
    assert "ramp750" in first
    assert "alinea" in first
    assert "seed3" in first


def test_validate_rejects_bad_phase_order():
    config = default_config()
    config.demand_phases[1].begin_s = 500
    try:
        validate_config(config)
    except ValueError as exc:
        assert "demand phase" in str(exc)
    else:
        raise AssertionError("validate_config should reject overlapping phases")
