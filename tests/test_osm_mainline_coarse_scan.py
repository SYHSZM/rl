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
import osm_mainline_coarse_scan as scan


ROOT = Path(__file__).resolve().parents[1]


class MatrixAndDemandTests(unittest.TestCase):
    def test_matrix_has_exact_six_fixed_mainline_runs(self):
        runs = scan.build_scan_matrix(ROOT, Path("C:/stage4d-test"))
        self.assertEqual([(r.scenario.name, r.controller, r.seed) for r in runs], [
            ("1200+0", "none", 1), ("1600+0", "none", 1), ("1750+0", "none", 1),
            ("1850+0", "none", 1), ("1900+0", "none", 1), ("2000+0", "none", 1),
        ])
        self.assertTrue(all(r.scenario.ramp_vph == 0 for r in runs))
        self.assertTrue(all(r.controller == "none" for r in runs))
        self.assertTrue(all(r.seed == 1 for r in runs))

    def test_rate_zero_returns_empty_departures(self):
        self.assertEqual(calibrated_demand.planned_departures(0), [])

    def test_negative_rate_remains_rejected(self):
        with self.assertRaises(ValueError):
            calibrated_demand.planned_departures(-1)

    def test_mainline_fixed_route_has_frozen_vtypes_no_ramp_and_exact_ids(self):
        scenario = calibrated_demand.CalibratedScenario("1200+0", 1200, 0, "coarse")
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "main1200_ramp0.rou.xml"
            calibrated_demand.write_tree(calibrated_demand.build_mainline_fixed_tree(ROOT / "osm.rou.xml", scenario), path)
            root = ET.parse(path).getroot()
            trips = root.findall("trip")
            self.assertEqual(len(trips), 1200)
            self.assertEqual(trips[0].attrib["id"], "mainline_000000")
            self.assertEqual(trips[-1].attrib["id"], "mainline_001199")
            self.assertFalse(any("ramp" in node.attrib.get("id", "") for node in trips))
            departures = [float(node.attrib["depart"]) for node in trips]
            self.assertGreaterEqual(min(departures), 0.0)
            self.assertLess(max(departures), 3600.0)
            self.assertEqual([(node.tag, node.attrib) for node in root.findall("vType")],
                             [(node.tag, node.attrib) for node in ET.parse(ROOT / "osm.rou.xml").getroot().findall("vType")])

    def test_mainline_routes_are_reproducible_in_two_directories(self):
        scenario = calibrated_demand.CalibratedScenario("1750+0", 1750, 0, "coarse")
        with tempfile.TemporaryDirectory() as temp:
            a, b = Path(temp) / "a.rou.xml", Path(temp) / "b.rou.xml"
            ha = calibrated_demand.write_tree(calibrated_demand.build_mainline_fixed_tree(ROOT / "osm.rou.xml", scenario), a)
            hb = calibrated_demand.write_tree(calibrated_demand.build_mainline_fixed_tree(ROOT / "osm.rou.xml", scenario), b)
            self.assertEqual(ha, hb)
            self.assertEqual(a.read_bytes(), b.read_bytes())

    def test_multiseed_matrix_is_exactly_the_frozen_25_combinations(self):
        runs = scan.build_multiseed_scan_matrix(ROOT, Path("C:/stage4d-test"),
                                                (1900, 1925, 1950, 1975, 2000),
                                                (1, 2, 3, 4, 5))
        self.assertEqual(len(runs), 25)
        self.assertEqual([(r.scenario.mainline_vph, r.seed) for r in runs], [
            (rate, seed) for rate in (1900, 1925, 1950, 1975, 2000)
            for seed in (1, 2, 3, 4, 5)
        ])
        self.assertTrue(all(r.scenario.ramp_vph == 0 and r.controller == "none"
                            and r.scenario.name.endswith("+0") for r in runs))

    def test_multiseed_output_preflight_rejects_nonempty_without_mutation(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "out"
            output.mkdir()
            marker = output / "keep.txt"
            marker.write_text("keep", encoding="utf-8")
            result = scan.validate_multiseed_preconditions(ROOT, output, scan.SUMO_BINARY)
            self.assertFalse(result["valid"])
            self.assertEqual(marker.read_text(encoding="utf-8"), "keep")


class ClassificationTests(unittest.TestCase):
    def test_sustained_congestion_boundary_is_exactly_300_seconds(self):
        rows = [{"window_end_s": (i + 1) * 30, "mainline_speed_mps": 16.0} for i in range(10)]
        result = scan.classify_windows(rows)
        self.assertEqual(result["congestion_duration_s"], 300)
        self.assertEqual(result["status"], "congested_seed1")

    def test_nine_windows_are_not_sustained_congestion(self):
        rows = [{"window_end_s": (i + 1) * 30, "mainline_speed_mps": 16.0} for i in range(9)]
        result = scan.classify_windows(rows)
        self.assertEqual(result["status"], "uncongested_seed1")

    def test_leading_empty_windows_do_not_make_later_congestion_invalid(self):
        rows = [{"window_end_s": 30, "mainline_speed_mps": None}]
        rows.extend({"window_end_s": (i + 2) * 30, "mainline_speed_mps": 16.0} for i in range(10))
        result = scan.classify_windows(rows)
        self.assertEqual(result["status"], "congested_seed1")

    def test_missing_or_invalid_metrics_are_invalid(self):
        result = scan.classify_windows([])
        self.assertEqual(result["status"], "invalid")

    def test_bracket_summary_states_are_explicit(self):
        rows = [
            {"mainline_vph": 1200, "status": "uncongested_seed1"},
            {"mainline_vph": 1600, "status": "congested_seed1"},
        ]
        result = scan.summarize_bracket(rows)
        self.assertEqual(result["highest_uncongested_vph"], 1200)
        self.assertEqual(result["first_congested_vph"], 1600)
        self.assertTrue(result["bracket_found"])
        self.assertTrue(result["monotonicity_valid"])

    def test_upper_and_lower_bound_states_are_recorded_without_scan_expansion(self):
        self.assertEqual(scan.summarize_bracket([{"mainline_vph": 1200, "status": "uncongested_seed1"}])["first_congested_vph"], "upper_bound_not_found")
        self.assertEqual(scan.summarize_bracket([{"mainline_vph": 1200, "status": "congested_seed1"}])["highest_uncongested_vph"], "lower_bound_not_found")

    def test_non_monotonic_state_is_not_reinterpreted(self):
        result = scan.summarize_bracket([
            {"mainline_vph": 1200, "status": "congested_seed1"},
            {"mainline_vph": 1600, "status": "uncongested_seed1"},
        ])
        self.assertEqual(result["status"], "non_monotonic_seed1")
        self.assertFalse(result["monotonicity_valid"])


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
    def __init__(self, count=1200):
        self.step_calls = 0
        self.ids = tuple(f"mainline_{i:06d}" for i in range(count))
        self.simulation = FakeSimulation(self)
        class TrafficLight:
            def __init__(self): self.state, self.calls = "GG", []
            def setRedYellowGreenState(self, _, state): self.state, self.calls = state, self.calls + [state]
            def getRedYellowGreenState(self, _): return self.state
        self.trafficlight = TrafficLight()
        self.inductionloop = types.SimpleNamespace(
            getLastStepOccupancy=lambda _: 10.0,
            getLastStepVehicleNumber=lambda _: 1,
            getLastStepMeanSpeed=lambda _: 20.0,
        )
        self.lanearea = types.SimpleNamespace(getLastStepVehicleNumber=lambda _: 0, getLastStepHaltingNumber=lambda _: 0)

    def simulationStep(self): self.step_calls += 1
    def close(self): pass


class FakeTraci:
    constants = types.SimpleNamespace(TRACI_VERSION=22)

    def __init__(self, fail_at=None): self.fail_at, self.starts, self.connections = fail_at, 0, {}
    def start(self, command, label):
        self.starts += 1
        if self.starts == self.fail_at: raise RuntimeError("requested fake failure")
        route_path = Path(command[command.index("--route-files") + 1])
        count = int(re.search(r"main(\d+)_", route_path.stem).group(1))
        self.connections[label] = FakeConnection(count)
        pairs = dict(zip(command[1::2], command[2::2]))
        Path(pairs["--log"]).write_text("", encoding="utf-8")
        Path(pairs["--error-log"]).write_text("", encoding="utf-8")
        tripinfos = ET.Element("tripinfos")
        for index in range(count):
            ET.SubElement(tripinfos, "tripinfo", {
                "id": f"mainline_{index:06d}", "depart": str(index), "arrival": str(index + 100),
                "duration": "100", "waitingTime": "1", "timeLoss": "10", "departDelay": "0", "routeLength": "2000",
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
    def test_existing_native_throughput_matches_required_evaluation_totals(self):
        expected = {1200: 1133, 1600: 1511, 1750: 1641, 1850: 1733, 1900: 1783, 2000: 1813}
        for rate, total in expected.items():
            run = ROOT / "outputs/stage4d_mainline_coarse" / f"main{rate}_ramp0" / "none_seed1"
            result = scan.parse_native_mainline_throughput(
                run / "native/osm_det_main_down_1.xml", run / "native/osm_det_main_down_2.xml"
            )
            self.assertEqual(result["evaluation_total"], total)
            self.assertEqual(len(result["windows"]), 120)

    def test_existing_native_terminal_throughput_matches_demand(self):
        for rate in (1200, 1600, 1750, 1850, 1900, 2000):
            run = ROOT / "outputs/stage4d_mainline_coarse" / f"main{rate}_ramp0" / "none_seed1"
            result = scan.parse_native_mainline_throughput(
                run / "native/osm_det_main_down_1.xml", run / "native/osm_det_main_down_2.xml"
            )
            self.assertEqual(result["terminal_total"], rate)

    def test_preflight_only_is_read_only_and_does_not_start_fake_traci(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "out"
            fake = FakeTraci()
            result = scan.validate_preconditions(ROOT, output, scan.SUMO_BINARY)
            self.assertTrue(result["valid"], result["errors"])
            self.assertFalse(output.exists())
            self.assertEqual(fake.starts, 0)

    def test_nonempty_output_is_rejected_without_mutation(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "out"
            output.mkdir()
            marker = output / "keep.txt"
            marker.write_text("keep", encoding="utf-8")
            result = scan.validate_preconditions(ROOT, output, scan.SUMO_BINARY)
            self.assertFalse(result["valid"])
            self.assertEqual(marker.read_text(encoding="utf-8"), "keep")

    def test_fake_scan_starts_exactly_six_and_writes_manifest(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "out"
            result = scan.run_scan(ROOT, output, FakeTraci())
            self.assertTrue(result["valid"], result["errors"])
            self.assertEqual(result["formal_sumo_start_count"], 6)
            self.assertEqual(len(json.loads((output / "scan_manifest.json").read_text(encoding="utf-8"))["run_order"]), 6)
            with (output / "run_summary.csv").open(encoding="utf-8-sig", newline="") as handle:
                self.assertEqual(len(list(csv.DictReader(handle))), 6)

    def test_first_fake_failure_stops_without_retry(self):
        with tempfile.TemporaryDirectory() as temp:
            fake = FakeTraci(fail_at=1)
            result = scan.run_scan(ROOT, Path(temp) / "out", fake)
            self.assertFalse(result["valid"])
            self.assertEqual(fake.starts, 1)
            self.assertEqual(result["formal_sumo_start_count"], 1)

    def test_all_fake_runs_keep_green_and_zero_red_seconds(self):
        with tempfile.TemporaryDirectory() as temp:
            scan.run_scan(ROOT, Path(temp) / "out", FakeTraci())
            for path in (Path(temp) / "out").glob("main*_ramp0/none_seed1/run_summary.json"):
                summary = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(summary["online"]["red_s"], 0)
                self.assertEqual(summary["online"]["green_s"], 3600)

    def test_native_xml_missing_misaligned_or_malformed_is_invalid_without_fallback(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            valid = root / "valid.xml"
            valid.write_text("<detector><interval begin='0' end='30' id='osm_det_main_down_1' nVehContrib='7'/></detector>", encoding="utf-8")
            missing = scan.parse_native_mainline_throughput(valid, root / "missing.xml")
            self.assertTrue(missing["errors"])
            self.assertEqual(missing["evaluation_total"], 0)
            malformed = root / "malformed.xml"
            malformed.write_text("<detector>", encoding="utf-8")
            result = scan.parse_native_mainline_throughput(malformed, malformed)
            self.assertTrue(result["errors"])
            self.assertEqual(result["evaluation_total"], 0)

    def test_multiseed_aggregation_uses_only_five_independent_seed_results(self):
        summaries = []
        for rate in (1900, 1925):
            for seed in (1, 2, 3, 4, 5):
                summaries.append({"mainline_vph": rate, "seed": seed, "valid": True,
                                  "classification": {"status": "congested" if seed == 1 else "uncongested"},
                                  "runtime": {"evaluation_throughput": 100, "terminal_throughput": 100},
                                  "online": {"mainline_speed_mps": 20}, "tripinfo": {},})
        summaries.append({"mainline_vph": 1900, "seed": 1, "valid": True,
                          "classification": {"status": "congested"}})
        aggregate = scan.aggregate_multiseed_results(summaries, (1900, 1925), (1, 2, 3, 4, 5))
        self.assertEqual(aggregate["1900"]["valid_runs"], 5)
        self.assertEqual(aggregate["1900"]["congested_runs"], 1)
        self.assertEqual(aggregate["1925"]["valid_runs"], 5)

    def test_fake_multiseed_scan_starts_exactly_25_and_writes_audit_outputs(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "out"
            result = scan.run_multiseed_scan(ROOT, output, FakeTraci())
            self.assertTrue(result["valid"], result.get("errors"))
            self.assertEqual(result["formal_sumo_start_count"], 25)
            self.assertTrue((output / "multiseed_classification.json").exists())
            self.assertTrue((output / "mainline_multiseed_report.md").exists())
            self.assertEqual(len(list(output.glob("main*_ramp0/none_seed*/run_summary.json"))), 25)


if __name__ == "__main__":
    unittest.main()
