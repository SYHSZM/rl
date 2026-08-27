from __future__ import annotations

import json
import os
import sys
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from build_network import (
    FrozenNetwork,
    FrozenNetworkMismatchError,
    build_network,
    preflight_network,
    required_detector_ids,
    verify_frozen_network,
)
from controllers import AlineaController, ControlAction, NoControlController
from experiment_config import DemandPoint, ExperimentConfig, default_config, experiment_id, validate_config
from metrics import EpisodeSummary, WindowRecord, summarize_episode, write_summary_csv, write_window_csv
from rou_generate import generate_rou_file


@dataclass
class RunResult:
    experiment_id: str
    output_dir: Path
    valid: bool
    failure_reason: str
    window_records: list[WindowRecord]
    summary: EpisodeSummary


def run_experiment(
    config: ExperimentConfig,
    demand: DemandPoint,
    controller_name: str,
    seed: int,
    output_root: str | Path = "outputs/runs",
    use_gui: bool = False,
    frozen_network: FrozenNetwork | None = None,
) -> RunResult:
    if controller_name not in {"none", "alinea"}:
        raise ValueError("controller must be 'none' or 'alinea'")
    validate_config(config)

    output_root = Path(output_root)
    if frozen_network is None:
        frozen_network = preflight_network(output_root / "_preflight" / "merge.net.xml")
    net_path = verify_frozen_network(frozen_network)

    exp_id = experiment_id(config, demand, controller_name, seed)
    output_dir = _allocate_attempt_dir(output_root, exp_id)
    _write_config_snapshot(output_dir / "config.json", config, demand, controller_name, seed)
    _write_network_snapshot(output_dir / "network_snapshot.json", frozen_network)

    route_path = output_dir / "merge.rou.xml"
    generate_rou_file(route_path, demand, seed=seed, config=config)
    additional_path = create_run_additional(output_dir, template_path=frozen_network.source_dir / "merge.add.xml")

    traci = _import_traci()
    controller = _make_controller(controller_name, config)
    windows: list[WindowRecord] = []
    action = ControlAction(config.alinea.max_rate_vph, config.alinea.max_rate_vph, config.alinea.cycle_s, 0.0)

    try:
        label = f"merge_{os.getpid()}_{id(output_dir)}"
        traci.start(_sumo_command(config, net_path, route_path, additional_path, output_dir, seed, use_gui), label=label)
        conn = traci.getConnection(label)
        departed_total = 0
        arrived_total = 0
        teleports_total = 0
        current_time = 0
        while current_time < config.simulation_duration_s:
            if current_time % config.metrics_interval_s == 0:
                occupancy = _detector_occupancy(conn, "det_bottleneck_down")
                queue_veh = _ramp_queue(conn)
                action = controller.update(occupancy=occupancy, queue_veh=queue_veh)
            _apply_ramp_signal(conn, action, current_time, config.alinea.cycle_s)
            conn.simulationStep()
            current_time = int(conn.simulation.getTime())
            departed_total += conn.simulation.getDepartedNumber()
            arrived_total += conn.simulation.getArrivedNumber()
            teleports_total += conn.simulation.getStartingTeleportNumber()
            if current_time > 0 and current_time % config.metrics_interval_s == 0:
                windows.append(
                    _collect_window_record(
                        conn,
                        exp_id,
                        controller_name,
                        seed,
                        current_time,
                        demand,
                        action,
                        departed_total,
                        arrived_total,
                        teleports_total,
                    )
                )
        verify_frozen_network(frozen_network)
        summary = summarize_episode(windows, config.free_flow_speed_mps, config.congestion_speed_ratio, config.congestion_min_duration_s)
        write_window_csv(output_dir / "windows.csv", windows)
        write_summary_csv(output_dir / "summary.csv", [summary])
        return RunResult(exp_id, output_dir, summary.valid, summary.failure_reason, windows, summary)
    except FrozenNetworkMismatchError as exc:
        summary = _invalid_summary(exp_id, controller_name, seed, demand, str(exc))
        write_window_csv(output_dir / "windows.csv", windows)
        write_summary_csv(output_dir / "summary.csv", [summary])
        raise
    except Exception as exc:
        summary = _invalid_summary(exp_id, controller_name, seed, demand, str(exc))
        write_window_csv(output_dir / "windows.csv", windows)
        write_summary_csv(output_dir / "summary.csv", [summary])
        return RunResult(exp_id, output_dir, False, str(exc), windows, summary)
    finally:
        try:
            traci.close()
        except Exception:
            pass


def _make_controller(controller_name: str, config: ExperimentConfig) -> NoControlController | AlineaController:
    if controller_name == "none":
        return NoControlController(config.alinea)
    return AlineaController(config.alinea)


def _sumo_command(
    config: ExperimentConfig,
    net_path: Path,
    route_path: Path,
    additional_path: Path,
    output_dir: Path,
    seed: int,
    use_gui: bool,
) -> list[str]:
    binary = "sumo-gui" if use_gui else "sumo"
    return [
        binary,
        "--net-file",
        str(net_path),
        "--route-files",
        str(route_path),
        "--additional-files",
        str(additional_path),
        "--step-length",
        str(config.step_length_s),
        "--begin",
        "0",
        "--end",
        str(config.simulation_duration_s),
        "--seed",
        str(seed),
        "--time-to-teleport",
        "-1",
        "--max-depart-delay",
        "1800",
        "--tripinfo-output",
        str(output_dir / "tripinfo.xml"),
        "--log",
        str(output_dir / "sumo.log"),
        "--error-log",
        str(output_dir / "sumo_error.log"),
    ]


def create_run_additional(output_dir: str | Path, template_path: str | Path | None = None) -> Path:
    output_dir = Path(output_dir)
    template_path = Path(template_path) if template_path is not None else Path(__file__).resolve().parent / "network" / "merge.add.xml"
    tree = ET.parse(template_path)
    elements = list(tree.getroot())
    detector_ids = [element.attrib.get("id", "") for element in elements]
    required_ids = required_detector_ids()
    if (
        len(elements) != len(required_ids)
        or len(set(detector_ids)) != len(detector_ids)
        or set(detector_ids) != required_ids
        or any("file" not in element.attrib for element in elements)
        or any("/" in detector_id or "\\" in detector_id or ".." in detector_id for detector_id in detector_ids)
    ):
        raise ValueError("invalid detector template: expected exactly the eight required detector IDs")

    output_dir.mkdir(parents=True, exist_ok=True)
    native_dir = (output_dir / "native").resolve()
    native_dir.mkdir(parents=True, exist_ok=True)
    for element, detector_id in zip(elements, detector_ids):
        target = (native_dir / f"{detector_id}.xml").resolve()
        if target.parent != native_dir:
            raise ValueError(f"invalid detector output path for {detector_id!r}")
        element.set("file", str(target))
    path = output_dir / "run.add.xml"
    ET.indent(tree, space="    ")
    tree.write(path, encoding="utf-8", xml_declaration=True)
    return path


def _allocate_attempt_dir(output_root: str | Path, experiment_id: str) -> Path:
    experiment_root = Path(output_root) / experiment_id
    experiment_root.mkdir(parents=True, exist_ok=True)
    attempt = 1
    while True:
        candidate = experiment_root / f"attempt_{attempt:04d}"
        try:
            candidate.mkdir(exist_ok=False)
        except FileExistsError:
            attempt += 1
        else:
            return candidate


def _import_traci() -> Any:
    if "SUMO_HOME" in os.environ:
        tools = Path(os.environ["SUMO_HOME"]) / "tools"
        if str(tools) not in sys.path:
            sys.path.append(str(tools))
    import traci

    return traci


def _apply_ramp_signal(conn: Any, action: ControlAction, current_time: int, cycle_s: int) -> None:
    in_green = current_time % cycle_s < action.green_s
    conn.trafficlight.setRedYellowGreenState("m4", "gGGG" if in_green else "rGGG")


def _collect_window_record(
    conn: Any,
    exp_id: str,
    controller_name: str,
    seed: int,
    time_s: int,
    demand: DemandPoint,
    action: ControlAction,
    departed_total: int,
    arrived_total: int,
    teleports_total: int,
) -> WindowRecord:
    speeds = [_detector_speed(conn, detector_id) for detector_id in ["det_main_0", "det_main_1", "det_main_2", "det_main_3", "det_main_4"]]
    valid_speeds = [speed for speed in speeds if speed >= 0]
    mean_speed = sum(valid_speeds) / len(valid_speeds) if valid_speeds else 0.0
    return WindowRecord(
        experiment_id=exp_id,
        controller=controller_name,
        seed=seed,
        time_s=time_s,
        mainline_vph=demand.mainline_vph,
        ramp_vph=demand.ramp_vph,
        mean_speed_mps=mean_speed,
        upstream_speed_mps=_detector_speed(conn, "det_main_3"),
        bottleneck_flow_veh=_detector_count(conn, "det_bottleneck_down"),
        bottleneck_occupancy=_detector_occupancy(conn, "det_bottleneck_down"),
        ramp_queue_veh=_ramp_queue(conn),
        alinea_requested_rate_vph=action.requested_rate_vph,
        alinea_applied_rate_vph=action.applied_rate_vph,
        departed_veh=departed_total,
        arrived_veh=arrived_total,
        teleports=teleports_total,
    )


def _detector_speed(conn: Any, detector_id: str) -> float:
    return float(conn.inductionloop.getLastStepMeanSpeed(detector_id))


def _detector_count(conn: Any, detector_id: str) -> int:
    return int(conn.inductionloop.getLastStepVehicleNumber(detector_id))


def _detector_occupancy(conn: Any, detector_id: str) -> float:
    return float(conn.inductionloop.getLastStepOccupancy(detector_id)) / 100.0


def _ramp_queue(conn: Any) -> int:
    return int(conn.lanearea.getLastStepVehicleNumber("det_ramp_queue"))


def _write_config_snapshot(path: Path, config: ExperimentConfig, demand: DemandPoint, controller_name: str, seed: int) -> None:
    snapshot = {
        "config": asdict(config),
        "demand": asdict(demand),
        "controller": controller_name,
        "seed": seed,
    }
    path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def _write_network_snapshot(path: Path, frozen_network: FrozenNetwork) -> None:
    snapshot = {
        "net_path": str(frozen_network.net_path),
        "net_sha256": frozen_network.net_sha256,
        "source_dir": str(frozen_network.source_dir),
        "source_sha256": frozen_network.source_sha256,
    }
    path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def _invalid_summary(exp_id: str, controller_name: str, seed: int, demand: DemandPoint, reason: str) -> EpisodeSummary:
    return EpisodeSummary(
        experiment_id=exp_id,
        controller=controller_name,
        seed=seed,
        mainline_vph=demand.mainline_vph,
        ramp_vph=demand.ramp_vph,
        valid=False,
        failure_reason=reason,
        total_time_spent_s=0.0,
        mean_speed_mps=0.0,
        bottleneck_throughput_veh=0,
        max_ramp_queue_veh=0,
        breakdown_start_s=0,
        congestion_duration_s=0,
        recovered=False,
        teleports=0,
    )
