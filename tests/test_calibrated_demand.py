import csv
import hashlib
import json
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from calibrated_demand import (
    DELIVERY_SCENARIOS,
    CalibratedScenario,
    build_fixed_tree,
    build_flow_tree,
    build_delivery,
    planned_departures,
    validate_delivery_pair,
    write_tree,
)


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "osm.rou.xml"


class CalibratedDemandTests(unittest.TestCase):
    def test_selected_scenarios_are_frozen(self):
        self.assertEqual(
            [(s.mainline_vph, s.ramp_vph) for s in DELIVERY_SCENARIOS],
            [(400, 60), (600, 60), (400, 80), (1000, 50), (600, 90)],
        )

    def test_selected_scenario_states_are_frozen(self):
        self.assertEqual(
            {s.name: s.state for s in DELIVERY_SCENARIOS},
            {
                "400+60": "light_boundary",
                "600+60": "stable_candidate",
                "400+80": "critical_unstable",
                "1000+50": "critical_unstable",
                "600+90": "oversaturated",
            },
        )

    def test_planned_departures_are_exact_count_and_half_open(self):
        departures = planned_departures(60)
        self.assertEqual(len(departures), 60)
        self.assertEqual(departures[0], 0.0)
        self.assertEqual(departures[-1], 3540.0)
        self.assertTrue(all(0.0 <= value < 3600.0 for value in departures))

    def test_invalid_rate_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "rate_vph must be non-negative"):
            planned_departures(-1)

    def test_flow_tree_preserves_vtypes_and_frozen_attributes(self):
        root = build_flow_tree(TEMPLATE, DELIVERY_SCENARIOS[0]).getroot()
        self.assertEqual([node.attrib["id"] for node in root.findall("vType")], ["Heavy_Vehicle", "Car"])
        flows = {node.attrib["id"]: node.attrib for node in root.findall("flow")}
        self.assertEqual(flows["mainline_flow"]["vehsPerHour"], "400")
        self.assertEqual(flows["mainline_flow"]["from"], "776458555")
        self.assertEqual(flows["ramp_flow"]["vehsPerHour"], "60")
        self.assertEqual(flows["ramp_flow"]["from"], "E2")

    def test_fixed_tree_has_explicit_complete_trip_attributes(self):
        root = build_fixed_tree(TEMPLATE, DELIVERY_SCENARIOS[0]).getroot()
        trips = root.findall("trip")
        self.assertEqual(len(trips), 460)
        self.assertEqual(len({trip.attrib["id"] for trip in trips}), 460)
        required = {"id", "type", "depart", "from", "to", "departLane", "departSpeed", "arrivalPos"}
        self.assertTrue(all(required <= set(trip.attrib) for trip in trips))
        self.assertTrue(all(float(trip.attrib["depart"]) < 3600.0 for trip in trips))

    def test_pair_validation_accepts_generated_pair(self):
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp)
            flow = out / "flow.rou.xml"
            fixed = out / "fixed.rou.xml"
            write_tree(build_flow_tree(TEMPLATE, DELIVERY_SCENARIOS[0]), flow)
            write_tree(build_fixed_tree(TEMPLATE, DELIVERY_SCENARIOS[0]), fixed)
            result = validate_delivery_pair(flow, fixed, DELIVERY_SCENARIOS[0], TEMPLATE)
        self.assertTrue(result["valid"])
        self.assertEqual(result["mainline_count"], 400)
        self.assertEqual(result["ramp_count"], 60)
        self.assertEqual(result["endpoint_policy"], "[0,3600)")

    def test_pair_validation_rejects_changed_departure(self):
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp)
            flow = out / "flow.rou.xml"
            fixed = out / "fixed.rou.xml"
            write_tree(build_flow_tree(TEMPLATE, DELIVERY_SCENARIOS[0]), flow)
            write_tree(build_fixed_tree(TEMPLATE, DELIVERY_SCENARIOS[0]), fixed)
            tree = ET.parse(fixed)
            tree.getroot().findall("trip")[0].set("depart", "1")
            tree.write(fixed, encoding="utf-8", xml_declaration=True)
            result = validate_delivery_pair(flow, fixed, DELIVERY_SCENARIOS[0], TEMPLATE)
        self.assertFalse(result["valid"])
        self.assertTrue(any("departure schedule" in error for error in result["errors"]))

    def _write_pair(self, out):
        scenario = DELIVERY_SCENARIOS[0]
        flow = out / "flow.rou.xml"
        fixed = out / "fixed.rou.xml"
        write_tree(build_flow_tree(TEMPLATE, scenario), flow)
        write_tree(build_fixed_tree(TEMPLATE, scenario), fixed)
        return flow, fixed, scenario

    def test_pair_validation_rejects_duplicate_flow(self):
        with tempfile.TemporaryDirectory() as temp:
            flow, fixed, scenario = self._write_pair(Path(temp))
            tree = ET.parse(flow)
            tree.getroot().append(ET.fromstring(ET.tostring(tree.getroot().find("flow"), encoding="unicode")))
            tree.write(flow, encoding="utf-8", xml_declaration=True)
            result = validate_delivery_pair(flow, fixed, scenario, TEMPLATE)
        self.assertFalse(result["valid"])
        self.assertTrue(any("flow" in error for error in result["errors"]))

    def test_pair_validation_rejects_extra_flow_trip_and_vehicle(self):
        with tempfile.TemporaryDirectory() as temp:
            flow, fixed, scenario = self._write_pair(Path(temp))
            flow_tree = ET.parse(flow)
            flow_tree.getroot().append(ET.Element("trip", {"id": "unexpected"}))
            flow_tree.write(flow, encoding="utf-8", xml_declaration=True)
            result = validate_delivery_pair(flow, fixed, scenario, TEMPLATE)
            self.assertFalse(result["valid"])
            flow, fixed, scenario = self._write_pair(Path(temp))
            fixed_tree = ET.parse(fixed)
            fixed_tree.getroot().append(ET.Element("vehicle", {"id": "unexpected"}))
            fixed_tree.write(fixed, encoding="utf-8", xml_declaration=True)
            result = validate_delivery_pair(flow, fixed, scenario, TEMPLATE)
        self.assertFalse(result["valid"])

    def test_pair_validation_rejects_modified_fixed_id(self):
        with tempfile.TemporaryDirectory() as temp:
            flow, fixed, scenario = self._write_pair(Path(temp))
            tree = ET.parse(fixed)
            tree.getroot().find("trip").set("id", "ramp_000000")
            tree.write(fixed, encoding="utf-8", xml_declaration=True)
            result = validate_delivery_pair(flow, fixed, scenario, TEMPLATE)
        self.assertFalse(result["valid"])
        self.assertTrue(any("ID" in error or "id" in error for error in result["errors"]))

    def test_build_delivery_rejects_stale_route_without_deleting_it(self):
        with tempfile.TemporaryDirectory() as temp:
            delivery = Path(temp) / "delivery"
            (delivery / "flow").mkdir(parents=True)
            stale = delivery / "flow" / "stale.rou.xml"
            stale.write_text("stale", encoding="utf-8")
            result = build_delivery(delivery, TEMPLATE)
            self.assertFalse(result["valid"])
            self.assertTrue(stale.exists())
            self.assertIn("flow/stale.rou.xml", result["unknown_files"])

    def test_malformed_xml_returns_stable_failure_fields(self):
        with tempfile.TemporaryDirectory() as temp:
            flow, fixed, scenario = self._write_pair(Path(temp))
            flow.write_text("<routes>", encoding="utf-8")
            result = validate_delivery_pair(flow, fixed, scenario, TEMPLATE)
        self.assertEqual(
            set(result),
            {"valid", "errors", "mainline_count", "ramp_count", "endpoint_policy", "flow_sha256", "fixed_sha256"},
        )
        self.assertFalse(result["valid"])
        self.assertEqual(result["mainline_count"], 0)
        self.assertEqual(result["ramp_count"], 0)
        self.assertIsNone(result["flow_sha256"])
        self.assertIsNone(result["fixed_sha256"])

    def test_build_delivery_writes_ten_routes_and_manifests(self):
        with tempfile.TemporaryDirectory() as temp:
            result = build_delivery(Path(temp) / "delivery", TEMPLATE)
            delivery = Path(temp) / "delivery"
            self.assertTrue(result["valid"])
            self.assertEqual(len(list((delivery / "flow").glob("*.rou.xml"))), 5)
            self.assertEqual(len(list((delivery / "fixed").glob("*.rou.xml"))), 5)
            with (delivery / "scenario_manifest.csv").open(encoding="utf-8-sig", newline="") as handle:
                self.assertEqual(len(list(csv.DictReader(handle))), 5)

    def test_build_delivery_is_reproducible(self):
        with tempfile.TemporaryDirectory() as temp:
            first = Path(temp) / "first"
            second = Path(temp) / "second"
            build_delivery(first, TEMPLATE)
            build_delivery(second, TEMPLATE)
            for relative in [
                *[Path("flow") / f"{s.name}.rou.xml" for s in DELIVERY_SCENARIOS],
                *[Path("fixed") / f"{s.name}.rou.xml" for s in DELIVERY_SCENARIOS],
                Path("scenario_manifest.csv"),
            ]:
                self.assertEqual(
                    hashlib.sha256((first / relative).read_bytes()).hexdigest(),
                    hashlib.sha256((second / relative).read_bytes()).hexdigest(),
                    str(relative),
                )
            first_report = json.loads((first / "validation_report.json").read_text(encoding="utf-8"))
            second_report = json.loads((second / "validation_report.json").read_text(encoding="utf-8"))
            self.assertEqual(first_report["core_sha256"], second_report["core_sha256"])


if __name__ == "__main__":
    unittest.main()
