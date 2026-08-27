import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from controllers import ControlAction, NoControlController
from experiment_config import default_config
from osm_control_adapter import (
    OSM_CONTROL_PROFILE,
    aggregate_mainline_occupancy,
    apply_ramp_signal,
    audit_osm_network,
    build_control_adapter_audit,
    build_detector_tree,
    read_ramp_queue,
    write_detector_file,
)


ROOT = Path(__file__).resolve().parents[1]


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest().upper()


class FakeTrafficLight:
    def __init__(self):
        self.calls = []

    def setRedYellowGreenState(self, tls_id, state):
        self.calls.append((tls_id, state))


class FakeConnection:
    def __init__(self):
        self.trafficlight = FakeTrafficLight()
        self.inductionloop = self
        self.lanearea = self

    def getLastStepOccupancy(self, detector_id):
        return {"osm_det_main_down_1": 20.0, "osm_det_main_down_2": 30.0}[detector_id]

    def getLastStepVehicleNumber(self, detector_id):
        return 12

    def getLastStepHaltingNumber(self, detector_id):
        return 5


class OsmControlAdapterTests(unittest.TestCase):
    def test_frozen_profile_and_positive_network_audit(self):
        self.assertEqual(OSM_CONTROL_PROFILE.tls_id, "J5")
        self.assertEqual(OSM_CONTROL_PROFILE.controlled_link_indices, (0, 1))
        self.assertEqual(OSM_CONTROL_PROFILE.green_state, "GG")
        self.assertEqual(OSM_CONTROL_PROFILE.red_state, "rr")
        result = audit_osm_network(ROOT / "osm.net.xml")
        self.assertTrue(result["valid"], result["errors"])
        for check in (
            "tls_junction",
            "controlled_connections",
            "ramp_path_uses_control",
            "mainline_path_avoids_controlled_entry",
            "merge_topology",
            "passenger_lanes",
        ):
            self.assertEqual(result["checks"][check], "PASS")

    def test_mutated_networks_fail_relevant_static_checks(self):
        mutations = (
            ("tlLogic id=\"J5\"", "tlLogic id=\"BROKEN\"", "tls_logic"),
            ("linkIndex=\"1\"", "linkIndex=\"7\"", "controlled_connections"),
            (
                "to=\"712593561#2.441\"",
                "to=\"nonexistent-edge\"",
                "controlled_connections",
            ),
        )
        original = (ROOT / "osm.net.xml").read_text(encoding="utf-8")
        for old, new, check in mutations:
            with self.subTest(check=check):
                with tempfile.TemporaryDirectory() as temp:
                    path = Path(temp) / "mutated.net.xml"
                    path.write_text(original.replace(old, new, 1), encoding="utf-8")
                    result = audit_osm_network(path)
                    self.assertFalse(result["valid"])
                    self.assertEqual(result["checks"][check], "FAIL")
                    self.assertTrue(result["errors"])

    def test_detector_tree_and_reproducible_files(self):
        root = build_detector_tree().getroot()
        detectors = list(root)
        self.assertEqual(len(detectors), 4)
        expected = [
            ("inductionLoop", "osm_det_main_down_1", "1060166442#2_1", "150", None),
            ("inductionLoop", "osm_det_main_down_2", "1060166442#2_2", "150", None),
            ("laneAreaDetector", "osm_det_ramp_queue", "712593561#2_1", "0", "430"),
            ("inductionLoop", "osm_det_ramp_arrival", "712593561#2_1", "20", None),
        ]
        for element, (tag, detector_id, lane, pos, end_pos) in zip(detectors, expected):
            self.assertEqual(element.tag, tag)
            self.assertEqual(element.attrib["id"], detector_id)
            self.assertEqual(element.attrib["lane"], lane)
            self.assertEqual(element.attrib["pos"], pos)
            self.assertEqual(element.attrib["freq"], "30")
            self.assertEqual(element.attrib.get("endPos"), end_pos)
            self.assertEqual(element.attrib["file"], f"native/{detector_id}.xml")
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            one = Path(first) / "detectors.add.xml"
            two = Path(second) / "detectors.add.xml"
            self.assertEqual(write_detector_file(one), write_detector_file(two))
            self.assertEqual(one.read_bytes(), two.read_bytes())
            ET.parse(one)
            self.assertNotIn(first, one.read_text(encoding="utf-8"))

    def test_fake_observations_are_aggregated_in_alinea_units(self):
        conn = FakeConnection()
        self.assertAlmostEqual(aggregate_mainline_occupancy(conn), 0.25)
        self.assertEqual(read_ramp_queue(conn), (12, 5))

    def test_signal_boundaries_and_no_control(self):
        conn = FakeConnection()
        action = ControlAction(1000.0, 400.0, 10.0, 20.0)
        expected = [(0, "GG"), (9, "GG"), (10, "rr"), (29, "rr"), (30, "GG")]
        for current_time, state in expected:
            self.assertEqual(apply_ramp_signal(conn, action, current_time, 30), state)
        self.assertTrue(all(tls_id == "J5" for tls_id, _ in conn.trafficlight.calls))
        no_control = NoControlController(default_config().alinea).update(0.22, 0)
        conn = FakeConnection()
        for current_time in (0, 15, 29, 30, 59):
            self.assertEqual(apply_ramp_signal(conn, no_control, current_time, 30), "GG")

    def test_audit_is_reproducible_and_excludes_runtime_paths(self):
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            one = build_control_adapter_audit(first, ROOT / "osm.net.xml")
            two = build_control_adapter_audit(second, ROOT / "osm.net.xml")
            self.assertTrue(one["valid"])
            self.assertTrue(two["valid"])
            self.assertEqual(sorted(Path(first).iterdir()), [Path(first) / "control_adapter_audit.json", Path(first) / "osm_control.add.xml"])
            self.assertEqual(sorted(Path(second).iterdir()), [Path(second) / "control_adapter_audit.json", Path(second) / "osm_control.add.xml"])
            self.assertEqual(one["detector_sha256"], two["detector_sha256"])
            self.assertEqual(one["core_sha256"], two["core_sha256"])
            self.assertEqual(one["net_sha256"], sha256(ROOT / "osm.net.xml"))
            self.assertEqual(set(one["checks"]), {
                "tls_junction", "tls_logic", "controlled_connections", "passenger_lanes",
                "merge_topology", "ramp_path_uses_control", "mainline_path_avoids_controlled_entry",
            })
            self.assertTrue(all(value == "PASS" for value in one["checks"].values()))
            self.assertNotIn("generation_timestamp", one["core"])
            self.assertNotIn(str(Path(first).resolve()), json.dumps(one["core"]))

    def test_cli_valid_and_invalid_exit_cleanly(self):
        script = ROOT / "audit_osm_control_adapter.py"
        with tempfile.TemporaryDirectory() as temp:
            completed = subprocess.run(
                [sys.executable, str(script), "--output-dir", temp, "--net", str(ROOT / "osm.net.xml")],
                cwd=ROOT, capture_output=True, text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertIn("VALIDATION_PASS=True", completed.stdout)
        original = (ROOT / "osm.net.xml").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as temp:
            net = Path(temp) / "broken.net.xml"
            out = Path(temp) / "out"
            net.write_text(original.replace('tlLogic id="J5"', 'tlLogic id="BROKEN"', 1), encoding="utf-8")
            completed = subprocess.run(
                [sys.executable, str(script), "--output-dir", str(out), "--net", str(net)],
                cwd=ROOT, capture_output=True, text=True,
            )
            self.assertEqual(completed.returncode, 2)
            self.assertIn("VALIDATION_PASS=False", completed.stdout)
            self.assertNotIn("Traceback", completed.stdout + completed.stderr)
            self.assertTrue((out / "control_adapter_audit.json").exists())
            self.assertFalse((out / "osm_control.add.xml").exists())


if __name__ == "__main__":
    unittest.main()
