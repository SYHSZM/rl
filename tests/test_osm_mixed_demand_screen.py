from __future__ import annotations

import csv
import json
import re
import tempfile
import types
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

import calibrated_demand
import osm_mixed_demand_screen as screen


ROOT = Path(__file__).resolve().parents[1]


class MatrixAndRouteTests(unittest.TestCase):
    def test_matrix_has_exactly_twelve_none_seed1_combinations(self):
        runs = screen.build_screen_matrix(ROOT, Path("C:/mixed-screen-test"))
        self.assertEqual(len(runs), 12)
        self.assertEqual([(r.scenario.mainline_vph, r.scenario.ramp_vph, r.controller, r.seed) for r in runs], [
            (mainline, ramp, "none", 1)
            for mainline in (1200, 1925, 1950, 1975)
            for ramp in (30, 60, 90)
        ])

    def test_fixed_route_has_mainline_and_ramp_counts_and_prefixes(self):
        scenario = calibrated_demand.CalibratedScenario("1925+60", 1925, 60, "mixed-screen")
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "mixed.rou.xml"
            calibrated_demand.write_tree(calibrated_demand.build_fixed_tree(ROOT / "osm.rou.xml", scenario), path)
            trips = ET.parse(path).getroot().findall("trip")
            self.assertEqual(len(trips), 1985)
            self.assertEqual(sum(t.attrib["id"].startswith("mainline_") for t in trips), 1925)
            self.assertEqual(sum(t.attrib["id"].startswith("ramp_") for t in trips), 60)

    def test_nonempty_output_is_rejected_without_deleting_marker(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "out"
            output.mkdir()
            marker = output / "keep.txt"
            marker.write_text("keep", encoding="utf-8")
            result = screen.validate_preconditions(ROOT, output, screen.SUMO_BINARY)
            self.assertFalse(result["valid"])
            self.assertEqual(marker.read_text(encoding="utf-8"), "keep")


class NativeAndClassificationTests(unittest.TestCase):
    def test_ramp_arrival_xml_sums_evaluation_and_terminal_nvehcontrib(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "arrival.xml"
            root = ET.Element("detector")
            for i in range(120):
                ET.SubElement(root, "interval", {
                    "begin": str(i * 30), "end": str((i + 1) * 30),
                    "id": "osm_det_ramp_arrival", "nVehContrib": "1",
                })
            ET.SubElement(root, "interval", {"begin": "3600", "end": "3630",
                                               "id": "osm_det_ramp_arrival", "nVehContrib": "2"})
            ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)
            result = screen.parse_ramp_arrivals(path)
            self.assertEqual(result["evaluation_total"], 120)
            self.assertEqual(result["terminal_total"], 122)
            self.assertFalse(result["errors"])

    def test_missing_or_malformed_ramp_arrival_is_invalid(self):
        with tempfile.TemporaryDirectory() as temp:
            result = screen.parse_ramp_arrivals(Path(temp) / "missing.xml")
            self.assertTrue(result["errors"])
            self.assertEqual(result["evaluation_total"], 0)

    def test_classification_is_limited_to_four_allowed_labels(self):
        allowed = {"free_flow", "control_candidate", "baseline_unstable", "invalid"}
        for congestion, baseline, valid, ramp_max in (
            (0, 0, True, 20), (300, 0, True, 20), (300, 4, True, 20), (300, 0, True, 81),
            (300, 0, False, 20),
        ):
            label = screen.classify_screen_run({
                "valid": valid,
                "online": {"congestion_duration_s": congestion, "ramp_vehicle_max_veh": ramp_max},
                "runtime": {"ramp_evaluation_throughput": 30},
            }, baseline)
            self.assertIn(label, allowed)


class FakeSimulation:
    def __init__(self, owner, clear_at=3600):
        self.owner, self.clear_at = owner, clear_at

    def getTime(self): return float(self.owner.step_calls)
    def getMinExpectedNumber(self): return 0 if self.owner.step_calls >= self.clear_at else 1
    def getDepartedIDList(self): return self.owner.ids if self.owner.step_calls == 1 else ()
    def getArrivedIDList(self): return self.owner.ids if self.owner.step_calls == self.clear_at else ()
    def getStartingTeleportNumber(self): return 0
    def getCollidingVehiclesNumber(self): return 0


class FakeConnection:
    def __init__(self, mainline, ramp):
        self.step_calls = 0
        self.ids = tuple([f"mainline_{i:06d}" for i in range(mainline)] +
                         [f"ramp_{i:06d}" for i in range(ramp)])
        self.simulation = FakeSimulation(self)
        class TrafficLight:
            def __init__(self): self.state = "GG"
            def setRedYellowGreenState(self, _, state): self.state = state
            def getRedYellowGreenState(self, _): return self.state
        self.trafficlight = TrafficLight()
        self.inductionloop = types.SimpleNamespace(
            getLastStepOccupancy=lambda _: 10.0,
            getLastStepVehicleNumber=lambda _: 1,
            getLastStepMeanSpeed=lambda _: 20.0,
        )
        self.lanearea = types.SimpleNamespace(
            getLastStepVehicleNumber=lambda _: 0,
            getLastStepHaltingNumber=lambda _: 0,
        )

    def simulationStep(self): self.step_calls += 1
    def close(self): pass


class FakeTraci:
    constants = types.SimpleNamespace(TRACI_VERSION=22)

    def __init__(self, fail_at=None): self.fail_at, self.starts, self.connections = fail_at, 0, {}

    def start(self, command, label):
        self.starts += 1
        if self.starts == self.fail_at: raise RuntimeError("requested fake failure")
        route_path = Path(command[command.index("--route-files") + 1])
        match = re.search(r"main(\d+)_ramp(\d+)", route_path.stem)
        self.connections[label] = FakeConnection(int(match.group(1)), int(match.group(2)))
        pairs = dict(zip(command[1::2], command[2::2]))
        Path(pairs["--log"]).write_text("", encoding="utf-8")
        Path(pairs["--error-log"]).write_text("", encoding="utf-8")
        tripinfos = ET.Element("tripinfos")
        for vehicle in self.connections[label].ids:
            ET.SubElement(tripinfos, "tripinfo", {
                "id": vehicle, "depart": "0", "arrival": "100", "duration": "100",
                "waitingTime": "1", "timeLoss": "10", "departDelay": "0", "routeLength": "2000",
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

    def getConnection(self, label): return self.connections[label]


class RunnerTests(unittest.TestCase):
    def test_fake_success_starts_exactly_twelve_and_writes_outputs(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "out"
            result = screen.run_screen(ROOT, output, FakeTraci())
            self.assertTrue(result["valid"], result.get("errors"))
            self.assertEqual(result["formal_sumo_start_count"], 12)
            self.assertEqual(len(list(output.glob("main*_ramp*/none_seed1/run_summary.json"))), 12)
            with (output / "run_summary.csv").open(encoding="utf-8-sig") as handle:
                self.assertEqual(len(list(csv.DictReader(handle))), 12)
            self.assertTrue((output / "screening_classification.json").exists())

    def test_first_invalid_stops_without_retry_or_remaining_starts(self):
        with tempfile.TemporaryDirectory() as temp:
            fake = FakeTraci(fail_at=1)
            result = screen.run_screen(ROOT, Path(temp) / "out", fake)
            self.assertFalse(result["valid"])
            self.assertEqual(fake.starts, 1)
            self.assertEqual(result["formal_sumo_start_count"], 1)


if __name__ == "__main__":
    unittest.main()
