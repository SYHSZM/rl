from __future__ import annotations

import csv
import json
import tempfile
import types
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest import mock

import osm_paired_pilot as pilot


ROOT = Path(__file__).resolve().parents[1]


class MatrixTests(unittest.TestCase):
    def test_matrix_is_exact_three_by_two_in_frozen_order(self):
        runs = pilot.build_run_matrix(ROOT)
        self.assertEqual(
            [(r.scenario.name, r.controller, r.seed) for r in runs],
            [
                ("600+60", "none", 1), ("600+60", "alinea", 1),
                ("400+80", "none", 1), ("400+80", "alinea", 1),
                ("1000+50", "none", 1), ("1000+50", "alinea", 1),
            ],
        )
        self.assertEqual([r.scenario.nominal_veh for r in runs[::2]], [660, 480, 1050])
        self.assertTrue(all(r.route_path.is_absolute() for r in runs))

    def test_route_hashes_are_frozen(self):
        expected = {
            "600+60": "ECF07999D44C8EA4B2FB5FEED908066FF82088602DE6F59A5ABCF96EDEB250EB",
            "400+80": "D9E70ABEF4DFBC163E859F71F3FE87B8DE65700331909505B01B9EF8321BE887",
            "1000+50": "B28C03F19441559DFF2EAA9331B4A44293E07C882F125EA7BD6EDAB6FB0B0BDC",
        }
        for run in pilot.build_run_matrix(ROOT)[::2]:
            self.assertEqual(run.scenario.route_sha256, expected[run.scenario.name])
            self.assertEqual(pilot.sha256(run.route_path), expected[run.scenario.name])


class TripinfoMetricTests(unittest.TestCase):
    def test_nearest_rank_and_fixed_id_groups(self):
        self.assertEqual(pilot.nearest_rank([1.0, 2.0, 9.0], 0.95), 9.0)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "tripinfo.xml"
            path.write_text(
                """<tripinfos>
                <tripinfo id="mainline_000000" depart="0" arrival="10" duration="10" waitingTime="1" timeLoss="2" departDelay="0" routeLength="100"/>
                <tripinfo id="mainline_000001" depart="3590" arrival="3610" duration="20" waitingTime="3" timeLoss="4" departDelay="1" routeLength="100"/>
                <tripinfo id="ramp_000000" depart="5" arrival="25" duration="20" waitingTime="5" timeLoss="6" departDelay="2" routeLength="50"/>
                </tripinfos>""",
                encoding="utf-8",
            )
            scenario = pilot.PilotScenario("fixture", 2, 1, 3, "X")
            spec = pilot.RunSpec(scenario, "none", 1, path, Path(temp))
            result = pilot.parse_tripinfo(path, spec)
            self.assertEqual(result["system"]["completed"], 3)
            self.assertEqual(result["system"]["before3600"], 2)
            self.assertEqual(result["system"]["after3600"], 1)
            self.assertEqual(result["system"]["total_timeLoss"], 12.0)
            self.assertEqual(result["system"]["total_system_time_s"], 53.0)
            self.assertAlmostEqual(result["mainline"]["aggregate_speed"], 200.0 / 30.0)
            self.assertAlmostEqual(result["ramp"]["mean_trip_speed"], 2.5)


def fake_summary(controller, tts, max_queue, valid):
    group = {
        "completed": 3,
        "completion_rate": 1.0,
        "total_timeLoss": 100.0 if controller == "none" else 80.0,
        "mean_trip_speed": 20.0 if controller == "none" else 21.0,
        "total_system_time_s": tts,
    }
    return {
        "scenario": "fixture",
        "seed": 1,
        "controller": controller,
        "valid": valid,
        "errors": [] if valid else ["fixture invalid"],
        "actual_end_s": 3700,
        "terminal_censoring": False,
        "runtime": {"collision": 0, "teleport": 0},
        "online": {
            "mainline_speed_mps": 20.0 if controller == "none" else 21.0,
            "bottleneck_throughput_veh": 100,
            "breakdown_start_s": 0,
            "congestion_duration_s": 0,
            "ramp_vehicle_mean_veh": 4.0,
            "ramp_vehicle_max_veh": max_queue,
            "arrived_at_3600": 2,
            "unfinished_at_3600": 1,
        },
        "tripinfo": {
            "mainline": dict(group),
            "ramp": dict(group),
            "system": dict(group),
        },
        "log_counts": {"fatal": 0, "emergency_braking": 0, "warning": 0},
    }


class PairMetricTests(unittest.TestCase):
    def test_compare_pair_returns_the_complete_plan_field_contract(self):
        expected = {
            "scenario", "seed", "valid_pair",
            "none_actual_end_s", "alinea_actual_end_s",
            "none_total_system_time_s", "alinea_total_system_time_s", "tts_improvement_ratio",
            "none_system_total_timeLoss", "alinea_system_total_timeLoss", "system_timeLoss_improvement_ratio",
            "none_mainline_total_timeLoss", "alinea_mainline_total_timeLoss", "mainline_timeLoss_improvement_ratio",
            "none_ramp_total_timeLoss", "alinea_ramp_total_timeLoss", "ramp_timeLoss_improvement_ratio",
            "mainline_mean_trip_speed_delta_mps", "online_bottleneck_throughput_delta_veh",
            "breakdown_start_delta_s", "congestion_duration_delta_s",
            "ramp_vehicle_mean_delta_veh", "ramp_vehicle_max_delta_veh",
            "arrived_at_3600_delta", "unfinished_at_3600_delta",
            "none_completion_rate", "alinea_completion_rate",
            "none_collision", "alinea_collision", "none_teleport", "alinea_teleport",
            "none_fatal", "alinea_fatal", "none_emergency_braking", "alinea_emergency_braking",
            "pilot_direction",
        }
        result = pilot.compare_pair(fake_summary("none", 1000.0, 20, True), fake_summary("alinea", 900.0, 40, True))
        self.assertEqual(set(pilot.PAIR_FIELDS), expected)
        self.assertEqual(set(result), expected)

    def test_positive_direction_requires_five_percent_and_no_spillback(self):
        none = fake_summary("none", tts=1000.0, max_queue=20, valid=True)
        alinea = fake_summary("alinea", tts=900.0, max_queue=40, valid=True)
        pair = pilot.compare_pair(none, alinea)
        self.assertAlmostEqual(pair["tts_improvement_ratio"], 0.1)
        self.assertEqual(pair["pilot_direction"], "positive_5pct")

    def test_invalid_and_queue_spillback_directions(self):
        none = fake_summary("none", tts=1000.0, max_queue=20, valid=True)
        invalid = fake_summary("alinea", tts=800.0, max_queue=20, valid=False)
        spillback = fake_summary("alinea", tts=800.0, max_queue=81, valid=True)
        self.assertEqual(pilot.compare_pair(none, invalid)["pilot_direction"], "invalid_pair")
        self.assertEqual(pilot.compare_pair(none, spillback)["pilot_direction"], "neutral_or_negative")


class NativeThroughputTests(unittest.TestCase):
    def _write_detector(self, path, detector_id, values, terminal=()):
        root = ET.Element("detector")
        for begin, end, value in list(values) + list(terminal):
            ET.SubElement(root, "interval", {"begin": f"{begin:.2f}", "end": f"{end:.2f}",
                                               "id": detector_id, "nVehContrib": str(value)})
        ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)

    def test_native_nvehcontrib_sums_lanes_and_separates_evaluation_from_terminal(self):
        with tempfile.TemporaryDirectory() as temp:
            a, b = Path(temp) / "a.xml", Path(temp) / "b.xml"
            values = [(i * 30, (i + 1) * 30, 1) for i in range(120)]
            self._write_detector(a, "osm_det_main_down_1", values, [(3600, 3630, 2)])
            self._write_detector(b, "osm_det_main_down_2", values, [(3600, 3630, 3)])
            result = pilot.parse_native_mainline_throughput(a, b)
            self.assertEqual(result["evaluation_total"], 240)
            self.assertEqual(result["terminal_total"], 245)
            self.assertEqual(len(result["windows"]), 120)
            self.assertTrue(all(row["bottleneck_throughput_veh"] == 2 for row in result["windows"]))
            self.assertEqual(result["errors"], [])

    def test_native_throughput_rejects_missing_malformed_duplicate_misaligned_and_negative_data(self):
        with tempfile.TemporaryDirectory() as temp:
            a, b = Path(temp) / "a.xml", Path(temp) / "b.xml"
            values = [(i * 30, (i + 1) * 30, 1) for i in range(120)]
            self._write_detector(a, "osm_det_main_down_1", values)
            self._write_detector(b, "osm_det_main_down_2", values[:-1] + [(3570, 3600, -1)])
            result = pilot.parse_native_mainline_throughput(a, b)
            self.assertTrue(result["errors"])
            self.assertEqual(result["windows"], [])
            malformed = Path(temp) / "malformed.xml"
            malformed.write_text("<detector>", encoding="utf-8")
            result = pilot.parse_native_mainline_throughput(malformed, b)
            self.assertTrue(result["errors"])


class CommandTests(unittest.TestCase):
    def test_command_uses_3600_demand_and_7200_clearance_cap(self):
        spec = pilot.build_run_matrix(ROOT, Path("C:/pilot-test"))[0]
        command = pilot.build_sumo_command(ROOT, spec, spec.output_dir / "runtime_osm_control.add.xml")
        pairs = dict(zip(command[1::2], command[2::2]))
        self.assertEqual(command[0], str(pilot.SUMO_BINARY.resolve()))
        self.assertEqual(pairs["--begin"], "0")
        self.assertEqual(pairs["--end"], "7200")
        self.assertEqual(pairs["--step-length"], "1.0")
        self.assertEqual(pairs["--seed"], "1")
        self.assertEqual(Path(pairs["--route-files"]), spec.route_path)
        self.assertNotIn("sumo-gui", " ".join(command).lower())


class FakeTrafficLight:
    def __init__(self):
        self.state = "GG"
        self.calls = []

    def setRedYellowGreenState(self, tls_id, state):
        self.state = state
        self.calls.append((tls_id, state))

    def getRedYellowGreenState(self, tls_id):
        return self.state


class FakeSimulation:
    def __init__(self, owner, clear_at):
        self.owner = owner
        self.clear_at = clear_at

    def getTime(self):
        return float(self.owner.step_calls)

    def getMinExpectedNumber(self):
        return 0 if self.owner.step_calls >= self.clear_at else 1

    def getDepartedIDList(self):
        return self.owner.vehicle_ids if self.owner.step_calls == 1 else ()

    def getArrivedIDList(self):
        return self.owner.vehicle_ids if self.owner.step_calls == self.clear_at else ()

    def getStartingTeleportNumber(self):
        return 0

    def getCollidingVehiclesNumber(self):
        return 0


class FakeInductionLoop:
    def getLastStepOccupancy(self, detector_id):
        return 20.0 if detector_id.endswith("1") else 30.0

    def getLastStepVehicleNumber(self, detector_id):
        return 1

    def getLastStepMeanSpeed(self, detector_id):
        return 20.0


class FakeLaneArea:
    def getLastStepVehicleNumber(self, detector_id):
        return 2

    def getLastStepHaltingNumber(self, detector_id):
        return 1


class FakeConnection:
    def __init__(self, clear_at=3620, mainline_count=1, ramp_count=0):
        self.step_calls = 0
        self.close_calls = 0
        self.vehicle_ids = tuple(
            [f"mainline_{index:06d}" for index in range(mainline_count)]
            + [f"ramp_{index:06d}" for index in range(ramp_count)]
        )
        self.trafficlight = FakeTrafficLight()
        self.simulation = FakeSimulation(self, clear_at)
        self.inductionloop = FakeInductionLoop()
        self.lanearea = FakeLaneArea()

    def simulationStep(self):
        self.step_calls += 1

    def close(self):
        self.close_calls += 1


class EpisodeLoopTests(unittest.TestCase):
    def spec(self, controller):
        return next(r for r in pilot.build_run_matrix(ROOT, Path("C:/pilot-test")) if r.controller == controller)

    def test_none_is_always_green_and_clears_after_3600(self):
        conn = FakeConnection(clear_at=3620)
        result = pilot.execute_episode(conn, self.spec("none"))
        self.assertEqual(result["initial_j5_state"], "GG")
        self.assertEqual(result["actual_end_s"], 3620)
        self.assertFalse(result["terminal_censoring"])
        self.assertEqual(len(result["windows"]), 120)
        self.assertEqual([row["window_end_s"] for row in result["windows"]], list(range(30, 3601, 30)))
        self.assertTrue(all(state == "GG" for _, state in conn.trafficlight.calls))
        self.assertEqual(result["controller_update_times"], list(range(0, 3620, 30)))

    def test_alinea_updates_every_30_seconds_through_clearance(self):
        conn = FakeConnection(clear_at=3640)
        result = pilot.execute_episode(conn, self.spec("alinea"))
        self.assertEqual(result["controller_update_times"], list(range(0, 3640, 30)))
        self.assertEqual(result["state_mismatches"], 0)
        self.assertEqual(result["detector_read_counts"], {
            "osm_det_main_down_1": 3640,
            "osm_det_main_down_2": 3640,
            "osm_det_ramp_queue": 3640,
            "osm_det_ramp_arrival": 3640,
        })

    def test_7200_cap_sets_terminal_censoring(self):
        conn = FakeConnection(clear_at=9000)
        result = pilot.execute_episode(conn, self.spec("none"))
        self.assertEqual(result["actual_end_s"], 7200)
        self.assertTrue(result["terminal_censoring"])
        self.assertGreater(result["terminal_expected_veh"], 0)

    def test_window_speed_is_vehicle_weighted_and_congestion_is_qualified(self):
        result = pilot.execute_episode(FakeConnection(clear_at=3600), self.spec("none"))
        self.assertEqual(result["windows"][0]["mainline_speed_mps"], 20.0)
        self.assertEqual(result["breakdown_start_s"], 0)
        self.assertEqual(result["congestion_duration_s"], 0)


class PreflightTests(unittest.TestCase):
    def test_workspace_preflight_passes_with_unused_temp_output(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "unused"
            result = pilot.validate_preconditions(ROOT, output, pilot.SUMO_BINARY)
            self.assertTrue(result["valid"], result["errors"])
            self.assertFalse(output.exists())
            self.assertEqual(result["protection_errors"], [])

    def test_nonempty_output_is_rejected_without_mutation(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "pilot"
            output.mkdir()
            marker = output / "keep.txt"
            marker.write_text("keep", encoding="utf-8")
            result = pilot.validate_preconditions(ROOT, output, pilot.SUMO_BINARY)
            self.assertFalse(result["valid"])
            self.assertEqual(marker.read_text(encoding="utf-8"), "keep")


class FakeTraciModule:
    __file__ = r"D:\sumo-1.25.0\tools\traci\__init__.py"
    constants = types.SimpleNamespace(TRACI_VERSION=22)

    COUNTS = {
        "600+60.rou.xml": (600, 60),
        "400+80.rou.xml": (400, 80),
        "1000+50.rou.xml": (1000, 50),
    }

    def __init__(self, fail_start_index=None):
        self.fail_start_index = fail_start_index
        self.start_calls = 0
        self.commands = []
        self.labels = []
        self.connections = {}

    def start(self, command, label):
        self.start_calls += 1
        self.commands.append(command)
        self.labels.append(label)
        if self.fail_start_index == self.start_calls:
            raise RuntimeError("fake requested start failure")
        pairs = dict(zip(command[1::2], command[2::2]))
        route_name = Path(pairs["--route-files"]).name
        mainline_count, ramp_count = self.COUNTS[route_name]
        self.connections[label] = FakeConnection(clear_at=3600, mainline_count=mainline_count, ramp_count=ramp_count)
        Path(pairs["--log"]).write_text("", encoding="utf-8")
        Path(pairs["--error-log"]).write_text("", encoding="utf-8")
        tripinfos = ET.Element("tripinfos")
        for prefix, count in (("mainline", mainline_count), ("ramp", ramp_count)):
            for index in range(count):
                ET.SubElement(tripinfos, "tripinfo", {
                    "id": f"{prefix}_{index:06d}", "depart": str(index),
                    "arrival": str(index + 100), "duration": "100",
                    "waitingTime": "1", "timeLoss": "10", "departDelay": "0",
                    "routeLength": "2000",
                })
        ET.ElementTree(tripinfos).write(pairs["--tripinfo-output"], encoding="utf-8", xml_declaration=True)
        for detector in ET.parse(pairs["--additional-files"]).getroot():
            target = Path(detector.attrib["file"])
            target.parent.mkdir(parents=True, exist_ok=True)
            detector_root = ET.Element("detector")
            detector_id = detector.attrib["id"]
            for index in range(120):
                ET.SubElement(detector_root, "interval", {
                    "begin": f"{index * 30:.2f}", "end": f"{(index + 1) * 30:.2f}",
                    "id": detector_id, "nVehContrib": "1",
                })
            ET.ElementTree(detector_root).write(target, encoding="utf-8", xml_declaration=True)

    def getConnection(self, label):
        return self.connections[label]


class OrchestrationTests(unittest.TestCase):
    def test_fake_pilot_starts_exactly_six_fresh_runs_and_writes_three_pairs(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "pilot"
            fake = FakeTraciModule()
            with mock.patch.object(pilot, "_sumo_version_text", return_value="SUMO 1.25.0"):
                result = pilot.run_pilot(ROOT, output, fake)
            self.assertTrue(result["valid"], result["errors"])
            self.assertEqual(fake.start_calls, 6)
            self.assertEqual(len(set(fake.labels)), 6)
            with (output / "run_summary.csv").open(encoding="utf-8-sig", newline="") as handle:
                self.assertEqual(len(list(csv.DictReader(handle))), 6)
            with (output / "pair_comparison.csv").open(encoding="utf-8-sig", newline="") as handle:
                self.assertEqual(len(list(csv.DictReader(handle))), 3)
            manifest = json.loads((output / "pilot_manifest.json").read_text(encoding="utf-8"))
            self.assertTrue(manifest["valid"])

    def test_formal_pair_csv_has_nonempty_contract_and_tts_values(self):
        path = ROOT / "outputs/stage4c_pilot/pair_comparison.csv"
        with path.open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(len(rows), 3)
        self.assertEqual(set(rows[0]), set(pilot.PAIR_FIELDS))
        expected_tts = {
            "600+60": (217041.0, 217038.0),
            "400+80": (188007.0, 188003.0),
            "1000+50": (370453.0, 370448.0),
        }
        for row in rows:
            self.assertTrue(all(row[field] != "" for field in pilot.PAIR_FIELDS), row)
            self.assertEqual(float(row["none_total_system_time_s"]), expected_tts[row["scenario"]][0])
            self.assertEqual(float(row["alinea_total_system_time_s"]), expected_tts[row["scenario"]][1])

    def test_formal_manifest_has_provenance_and_run_order(self):
        manifest = json.loads((ROOT / "outputs/stage4c_pilot/pilot_manifest.json").read_text(encoding="utf-8"))
        for field in ("schema", "valid", "errors", "formal_sumo_start_count", "runs_completed",
                      "sumo_version", "traci_version", "input_hashes", "matrix", "run_order",
                      "protection_errors"):
            self.assertIn(field, manifest)
        self.assertEqual(manifest["formal_sumo_start_count"], 6)
        self.assertEqual(manifest["runs_completed"], 6)
        self.assertEqual(len(manifest["matrix"]), 6)
        self.assertEqual(len(manifest["run_order"]), 6)
        self.assertTrue(all(item["valid"] for item in manifest["run_order"]))
        self.assertEqual(len(manifest["input_hashes"]), 5)

    def test_first_invalid_run_stops_without_retry(self):
        with tempfile.TemporaryDirectory() as temp:
            fake = FakeTraciModule(fail_start_index=1)
            with mock.patch.object(pilot, "_sumo_version_text", return_value="SUMO 1.25.0"):
                result = pilot.run_pilot(ROOT, Path(temp) / "pilot", fake)
            self.assertFalse(result["valid"])
            self.assertEqual(fake.start_calls, 1)
            self.assertFalse((Path(temp) / "pilot/pair_comparison.csv").exists())

    def test_preflight_only_never_calls_start_or_creates_output(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "pilot"
            fake = FakeTraciModule()
            with mock.patch.object(pilot, "OUTPUT_ROOT", output), mock.patch.object(
                pilot, "_import_traci", return_value=fake
            ), mock.patch.object(pilot, "_sumo_version_text", return_value="SUMO 1.25.0"):
                code = pilot.main(["--preflight-only"])
            self.assertEqual(code, 0)
            self.assertEqual(fake.start_calls, 0)
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
