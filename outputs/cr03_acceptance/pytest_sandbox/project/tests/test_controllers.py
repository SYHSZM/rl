from controllers import AlineaController, NoControlController
from experiment_config import AlineaConfig, default_config


def test_no_control_uses_max_rate_and_full_green():
    cfg = default_config().alinea
    action = NoControlController(cfg).update(occupancy=0.3, queue_veh=0)
    assert action.requested_rate_vph == cfg.max_rate_vph
    assert action.applied_rate_vph == cfg.max_rate_vph
    assert action.green_s == cfg.cycle_s
    assert action.red_s == 0


def test_alinea_clips_rate_to_bounds():
    cfg = AlineaConfig(gain=2000.0, initial_rate_vph=900.0)
    ctl = AlineaController(cfg)
    low = ctl.update(occupancy=1.0, queue_veh=0)
    assert low.requested_rate_vph == cfg.min_rate_vph
    high = ctl.update(occupancy=0.0, queue_veh=0)
    assert cfg.min_rate_vph <= high.requested_rate_vph <= cfg.max_rate_vph


def test_alinea_queue_override_raises_applied_rate():
    cfg = default_config().alinea
    ctl = AlineaController(cfg)
    action = ctl.update(occupancy=1.0, queue_veh=cfg.ramp_storage_veh)
    assert action.applied_rate_vph == cfg.max_rate_vph
    assert action.applied_rate_vph >= action.requested_rate_vph
