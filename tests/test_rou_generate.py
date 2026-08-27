import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from experiment_config import DemandPoint, default_config
from rou_generate import generate_calibrated_routes, generate_rou_file
from calibrated_demand import CALIBRATED_SCENARIOS, select_scenario


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest().upper()


class RouGenerateTests(unittest.TestCase):
    def test_legacy_generate_route_has_three_mainline_and_ramp_phases(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "merge.rou.xml"
            info = generate_rou_file(path, DemandPoint(4200, 750), seed=2, config=default_config())
            flows = {flow.attrib["id"]: flow.attrib for flow in ET.parse(path).getroot().findall("flow")}
        self.assertEqual(flows["main_0"]["vehsPerHour"], "2730.0")
        self.assertEqual(flows["main_1"]["vehsPerHour"], "4200.0")
        self.assertEqual(flows["main_2"]["vehsPerHour"], "2940.0")
        self.assertEqual(flows["ramp_0"]["vehsPerHour"], "487.5")
        self.assertEqual(flows["ramp_1"]["vehsPerHour"], "750.0")
        self.assertEqual(flows["ramp_2"]["vehsPerHour"], "525.0")
        self.assertEqual(info["seed"], 2)

    def test_legacy_generate_route_uses_merge_edge_ids(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "merge.rou.xml"
            generate_rou_file(path, DemandPoint(3000, 300), seed=0, config=default_config())
            routes = {route.attrib["id"]: route.attrib["edges"] for route in ET.parse(path).getroot().findall("route")}
        self.assertTrue(routes["main_route"].startswith("main_0"))
        self.assertIn("main_5", routes["main_route"])
        self.assertTrue(routes["ramp_route"].startswith("ramp_0"))
        self.assertIn("main_5", routes["ramp_route"])

    def test_registry_contains_exact_nine_approved_scenarios(self):
        actual = {(s.mainline_vph, s.ramp_vph, s.state) for s in CALIBRATED_SCENARIOS}
        self.assertEqual(actual, {
            (400, 60, "light_boundary"), (400, 70, "stable_candidate"),
            (600, 60, "stable_candidate"), (400, 80, "critical_unstable"),
            (600, 70, "critical_unstable"), (800, 50, "critical_unstable"),
            (1000, 50, "critical_unstable"), (600, 80, "oversaturated"),
            (600, 90, "oversaturated"),
        })

    def test_selection_is_reproducible_and_range_filtered(self):
        first = select_scenario(seed=17, state="critical_unstable", mainline_range=(600, 1000), ramp_range=(50, 70))
        second = select_scenario(seed=17, state="critical_unstable", mainline_range=(600, 1000), ramp_range=(50, 70))
        self.assertEqual(first, second)
        self.assertTrue(600 <= first.mainline_vph <= 1000)
        self.assertTrue(50 <= first.ramp_vph <= 70)
        self.assertIn(first, CALIBRATED_SCENARIOS)

    def test_selection_rejects_invalid_or_empty_filters(self):
        with self.assertRaisesRegex(ValueError, "no approved scenario"):
            select_scenario(seed=1, state="light_boundary", mainline_range=(600, 1000))
        with self.assertRaisesRegex(ValueError, "mainline range"):
            select_scenario(seed=1, state="stable_candidate", mainline_range=(700, 600))
        with self.assertRaisesRegex(ValueError, "non-negative"):
            select_scenario(seed=1, state="stable_candidate", ramp_range=(-1, 60))
        with self.assertRaisesRegex(ValueError, "state"):
            select_scenario(seed=1, state="unknown")

    def test_calibrated_formats_write_expected_files_and_metadata(self):
        for output_format, expected in (("flow", (True, False)), ("fixed", (False, True)), ("both", (True, True))):
            with tempfile.TemporaryDirectory() as temp:
                info = generate_calibrated_routes(Path(temp), seed=7, state="stable_candidate", output_format=output_format)
                self.assertEqual(info["validation"]["valid"], True)
                self.assertEqual(bool(info["flow_path"]) and Path(info["flow_path"]).is_file(), expected[0])
                self.assertEqual(bool(info["fixed_path"]) and Path(info["fixed_path"]).is_file(), expected[1])
                self.assertTrue(Path(info["metadata_path"]).is_file())
                metadata = json.loads(Path(info["metadata_path"]).read_text(encoding="utf-8"))
                self.assertEqual(metadata["output_format"], output_format)
                self.assertEqual(metadata["selected_scenario"], info["selected_scenario"])
                self.assertEqual(metadata["core_sha256"], info["core_sha256"])
                self.assertEqual(metadata["validation"]["flow_sha256"], metadata["flow_sha256"])
                self.assertEqual(metadata["validation"]["fixed_sha256"], metadata["fixed_sha256"])

    def test_cli_reproducible_and_invalid_range_returns_two(self):
        with tempfile.TemporaryDirectory() as temp:
            first, second = Path(temp) / "a", Path(temp) / "b"
            args = ["calibrated", "--seed", "17", "--state", "critical_unstable", "--main-min", "600", "--main-max", "1000", "--ramp-min", "50", "--ramp-max", "70", "--format", "both"]
            one = subprocess.run([sys.executable, "rou_generate.py", *args[:1], "--output-dir", str(first), *args[1:]], cwd=PROJECT_ROOT, capture_output=True, text=True)
            two = subprocess.run([sys.executable, "rou_generate.py", *args[:1], "--output-dir", str(second), *args[1:]], cwd=PROJECT_ROOT, capture_output=True, text=True)
            self.assertEqual(one.returncode, 0, one.stderr)
            self.assertEqual(two.returncode, 0, two.stderr)
            self.assertIn("flow_path:", one.stdout)
            self.assertIn("fixed_path:", one.stdout)
            one_meta = json.loads(next(first.glob("*_metadata.json")).read_text(encoding="utf-8"))
            two_meta = json.loads(next(second.glob("*_metadata.json")).read_text(encoding="utf-8"))
            self.assertEqual(one_meta["selected_scenario"], two_meta["selected_scenario"])
            self.assertEqual(one_meta["core_sha256"], two_meta["core_sha256"])
            one_xml = sorted(first.glob("*.rou.xml"))
            two_xml = sorted(second.glob("*.rou.xml"))
            self.assertEqual([sha256(path) for path in one_xml], [sha256(path) for path in two_xml])
            bad = subprocess.run([sys.executable, "rou_generate.py", "calibrated", "--output-dir", str(Path(temp) / "bad"), "--seed", "1", "--state", "stable_candidate", "--main-min", "700", "--main-max", "600"], cwd=PROJECT_ROOT, capture_output=True, text=True)
        self.assertEqual(bad.returncode, 2)
        self.assertIn("mainline range", bad.stderr)

    def test_calibrated_generation_does_not_overwrite_template(self):
        with tempfile.TemporaryDirectory() as temp:
            template = Path(temp) / "osm.rou.xml"
            template.write_bytes((PROJECT_ROOT / "osm.rou.xml").read_bytes())
            before = sha256(template)
            info = generate_calibrated_routes(Path(temp) / "generated", seed=1, state="light_boundary", output_format="flow", template_path=template)
            self.assertEqual(sha256(template), before)
            self.assertNotEqual(Path(info["flow_path"]).resolve(), template.resolve())


if __name__ == "__main__":
    unittest.main()
