from __future__ import annotations

import json
import os
import sys
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
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
        window_accumulator = _WindowAccumulator()
        system_exposure_tracker = _SystemExposureTracker()
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
            system_exposure_tracker.add_step(
                loaded_ids=conn.simulation.getLoadedIDList(),
                departed_ids=conn.simulation.getDepartedIDList(),
                arrived_ids=conn.simulation.getArrivedIDList(),
                dt=config.step_length_s,
            )
            _sample_window_step(conn, window_accumulator)
            if current_time > 0 and current_time % config.metrics_interval_s == 0:
                windows.append(
                    _collect_window_record(
                        window_accumulator,
                        exp_id,
                        controller_name,
                        seed,
                        current_time,
                        demand,
                        action,
                        departed_total,
                        arrived_total,
                        teleports_total,
                        system_exposure_tracker.snapshot(),
                    )
                )
                window_accumulator = _WindowAccumulator()
        verify_frozen_network(frozen_network)
        system_exposure = system_exposure_tracker.snapshot()
        summary = summarize_episode(
            windows,
            config.free_flow_speed_mps,
            config.congestion_speed_ratio,
            config.congestion_min_duration_s,
            system_exposure=system_exposure,
        )
        write_window_csv(output_dir / "windows.csv", windows)
        write_summary_csv(output_dir / "summary.csv", [summary])
        (output_dir / "system_exposure.json").write_text(
            json.dumps(system_exposure, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
        )
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


@dataclass
class _WindowAccumulator:
    step_observations: int = 0
    main_speed_weighted_sum: float = 0.0
    main_speed_vehicle_observations: int = 0
    upstream_speed_weighted_sum: float = 0.0
    upstream_speed_vehicle_observations: int = 0
    bottleneck_flow_veh: int = 0
    bottleneck_occupancy_sum: float = 0.0
    ramp_vehicle_sum: int = 0
    ramp_vehicle_max: int = 0
    ramp_halting_sum: int = 0
    ramp_halting_max: int = 0

    def add_step(
        self,
        *,
        mainline_measurements: dict[str, tuple[int, float]],
        bottleneck_count: int,
        bottleneck_occupancy: float,
        ramp_vehicle_count: int,
        ramp_halting_count: int,
    ) -> None:
        self.step_observations += 1
        for detector_id in ("det_main_0", "det_main_1", "det_main_2", "det_main_3", "det_main_4"):
            count, speed = mainline_measurements[detector_id]
            if count > 0 and speed >= 0:
                self.main_speed_weighted_sum += count * speed
                self.main_speed_vehicle_observations += count
                if detector_id == "det_main_3":
                    self.upstream_speed_weighted_sum += count * speed
                    self.upstream_speed_vehicle_observations += count
        self.bottleneck_flow_veh += bottleneck_count
        self.bottleneck_occupancy_sum += bottleneck_occupancy
        self.ramp_vehicle_sum += ramp_vehicle_count
        self.ramp_vehicle_max = max(self.ramp_vehicle_max, ramp_vehicle_count)
        self.ramp_halting_sum += ramp_halting_count
        self.ramp_halting_max = max(self.ramp_halting_max, ramp_halting_count)

    def to_record(
        self,
        *,
        experiment_id: str,
        controller: str,
        seed: int,
        time_s: int,
        demand: DemandPoint,
        action: ControlAction,
        departed_total: int,
        arrived_total: int,
        teleports_total: int,
        system_exposure: dict[str, object] | None = None,
    ) -> WindowRecord:
        mean_speed = (
            self.main_speed_weighted_sum / self.main_speed_vehicle_observations
            if self.main_speed_vehicle_observations
            else None
        )
        upstream_speed = (
            self.upstream_speed_weighted_sum / self.upstream_speed_vehicle_observations
            if self.upstream_speed_vehicle_observations
            else None
        )
        occupancy = self.bottleneck_occupancy_sum / self.step_observations if self.step_observations else 0.0
        ramp_vehicle_mean = self.ramp_vehicle_sum / self.step_observations if self.step_observations else 0.0
        ramp_halting_mean = self.ramp_halting_sum / self.step_observations if self.step_observations else 0.0
        counts = system_exposure.get("counts", {}) if system_exposure else {}
        ratios = system_exposure.get("ratios", {}) if system_exposure else {}
        tts = system_exposure.get("tts", {}) if system_exposure else {}
        terminal = system_exposure.get("terminal", {}) if system_exposure else {}
        return WindowRecord(
            experiment_id=experiment_id,
            controller=controller,
            seed=seed,
            time_s=time_s,
            mainline_vph=demand.mainline_vph,
            ramp_vph=demand.ramp_vph,
            mean_speed_mps=mean_speed,
            upstream_speed_mps=upstream_speed,
            bottleneck_flow_veh=self.bottleneck_flow_veh,
            bottleneck_occupancy=occupancy,
            ramp_queue_veh=self.ramp_vehicle_max,  # Compatibility alias; formal code uses ramp_vehicle_max_veh.
            alinea_requested_rate_vph=action.requested_rate_vph,
            alinea_applied_rate_vph=action.applied_rate_vph,
            departed_veh=departed_total,
            arrived_veh=arrived_total,
            teleports=teleports_total,
            window_step_observations=self.step_observations,
            main_speed_vehicle_observations=self.main_speed_vehicle_observations,
            upstream_speed_vehicle_observations=self.upstream_speed_vehicle_observations,
            ramp_vehicle_mean_veh=ramp_vehicle_mean,
            ramp_vehicle_max_veh=self.ramp_vehicle_max,
            ramp_halting_mean_veh=ramp_halting_mean,
            ramp_halting_max_veh=self.ramp_halting_max,
            loaded_veh=int(counts.get("loaded", 0)),
            pending_veh=int(terminal.get("pending_count", 0)),
            in_network_veh=int(terminal.get("in_network_count", 0)),
            actual_departure_ratio=ratios.get("actual_departure"),
            completion_ratio=ratios.get("completion"),
            tts_system_s=float(tts.get("system_s", 0.0)),
            tts_pending_s=float(tts.get("pending_s", 0.0)),
            tts_in_network_s=float(tts.get("in_network_s", 0.0)),
        )


@dataclass
class _SystemExposureTracker:
    loaded_seen: set[str] = field(default_factory=set)
    departed_seen: set[str] = field(default_factory=set)
    arrived_seen: set[str] = field(default_factory=set)
    pending_exposure_s: dict[str, float] = field(default_factory=dict)
    in_network_exposure_s: dict[str, float] = field(default_factory=dict)

    def add_step(
        self,
        *,
        loaded_ids: tuple[str, ...] | list[str],
        departed_ids: tuple[str, ...] | list[str],
        arrived_ids: tuple[str, ...] | list[str],
        dt: float,
    ) -> None:
        loaded = set(loaded_ids)
        departed = set(departed_ids)
        arrived = set(arrived_ids)
        self.loaded_seen.update(loaded)
        self.departed_seen.update(departed)
        self.arrived_seen.update(arrived)
        for vehicle_id in loaded | departed | arrived:
            self.pending_exposure_s.setdefault(vehicle_id, 0.0)
            self.in_network_exposure_s.setdefault(vehicle_id, 0.0)
        for vehicle_id in self.loaded_seen - self.departed_seen:
            self.pending_exposure_s[vehicle_id] += dt
        for vehicle_id in self.departed_seen - self.arrived_seen:
            self.in_network_exposure_s[vehicle_id] += dt

    def snapshot(self) -> dict[str, object]:
        all_ids = sorted(self.loaded_seen | self.departed_seen | self.arrived_seen)
        pending_ids = sorted(self.loaded_seen - self.departed_seen)
        in_network_ids = sorted(self.departed_seen - self.arrived_seen)
        per_id = {
            vehicle_id: {
                "origin": _vehicle_origin(vehicle_id),
                "pending_s": self.pending_exposure_s.get(vehicle_id, 0.0),
                "in_network_s": self.in_network_exposure_s.get(vehicle_id, 0.0),
                "system_s": self.pending_exposure_s.get(vehicle_id, 0.0) + self.in_network_exposure_s.get(vehicle_id, 0.0),
            }
            for vehicle_id in all_ids
        }
        pending_s = sum(self.pending_exposure_s.values())
        in_network_s = sum(self.in_network_exposure_s.values())
        origin_totals = {
            origin: sum(row["system_s"] for row in per_id.values() if row["origin"] == origin)
            for origin in ("mainline", "ramp", "unknown")
        }
        completed_vehicle_s = sum(per_id[vehicle_id]["system_s"] for vehicle_id in self.arrived_seen)
        terminal_in_network_s = sum(per_id[vehicle_id]["system_s"] for vehicle_id in in_network_ids)
        terminal_pending_s = sum(per_id[vehicle_id]["system_s"] for vehicle_id in pending_ids)
        loaded_count = len(self.loaded_seen)
        return {
            "schema_version": "cr02-system-exposure-v1",
            "counts": {
                "loaded": loaded_count,
                "departed": len(self.departed_seen),
                "arrived": len(self.arrived_seen),
            },
            "ratios": {
                "actual_departure": len(self.departed_seen) / loaded_count if loaded_count else None,
                "completion": len(self.arrived_seen) / loaded_count if loaded_count else None,
            },
            "tts": {
                "system_s": pending_s + in_network_s,
                "pending_s": pending_s,
                "in_network_s": in_network_s,
                "mainline_s": origin_totals["mainline"],
                "ramp_s": origin_totals["ramp"],
                "unknown_s": origin_totals["unknown"],
                "completed_vehicle_s": completed_vehicle_s,
            },
            "terminal": {
                "in_network_ids": in_network_ids,
                "pending_ids": pending_ids,
                "in_network_count": len(in_network_ids),
                "pending_count": len(pending_ids),
                "in_network_exposure_s": terminal_in_network_s,
                "pending_exposure_s": terminal_pending_s,
                "censoring": bool(in_network_ids or pending_ids),
            },
            "unknown_ids": [vehicle_id for vehicle_id in all_ids if _vehicle_origin(vehicle_id) == "unknown"],
            "per_id": per_id,
        }


def _vehicle_origin(vehicle_id: str) -> str:
    if vehicle_id.startswith("main_"):
        return "mainline"
    if vehicle_id.startswith("ramp_"):
        return "ramp"
    return "unknown"


def _sample_window_step(conn: Any, accumulator: _WindowAccumulator) -> None:
    mainline_measurements = {
        detector_id: (_detector_count(conn, detector_id), _detector_speed(conn, detector_id))
        for detector_id in ("det_main_0", "det_main_1", "det_main_2", "det_main_3", "det_main_4")
    }
    accumulator.add_step(
        mainline_measurements=mainline_measurements,
        bottleneck_count=_detector_count(conn, "det_bottleneck_down"),
        bottleneck_occupancy=_detector_occupancy(conn, "det_bottleneck_down"),
        ramp_vehicle_count=_ramp_queue(conn),
        ramp_halting_count=_ramp_halting(conn),
    )


def _collect_window_record(
    accumulator: _WindowAccumulator,
    exp_id: str,
    controller_name: str,
    seed: int,
    time_s: int,
    demand: DemandPoint,
    action: ControlAction,
    departed_total: int,
    arrived_total: int,
    teleports_total: int,
    system_exposure: dict[str, object] | None = None,
) -> WindowRecord:
    return accumulator.to_record(
        experiment_id=exp_id,
        controller=controller_name,
        seed=seed,
        time_s=time_s,
        demand=demand,
        action=action,
        departed_total=departed_total,
        arrived_total=arrived_total,
        teleports_total=teleports_total,
        system_exposure=system_exposure,
    )


def _detector_speed(conn: Any, detector_id: str) -> float:
    return float(conn.inductionloop.getLastStepMeanSpeed(detector_id))


def _detector_count(conn: Any, detector_id: str) -> int:
    return int(conn.inductionloop.getLastStepVehicleNumber(detector_id))


def _detector_occupancy(conn: Any, detector_id: str) -> float:
    return float(conn.inductionloop.getLastStepOccupancy(detector_id)) / 100.0


def _ramp_queue(conn: Any) -> int:
    return int(conn.lanearea.getLastStepVehicleNumber("det_ramp_queue"))


def _ramp_halting(conn: Any) -> int:
    return int(conn.lanearea.getLastStepHaltingNumber("det_ramp_queue"))


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
