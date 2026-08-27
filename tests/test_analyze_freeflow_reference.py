import csv
import importlib.util
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "analyze_freeflow_reference.py"
ANALYSIS_OUTPUTS = (
    "freeflow_reference_runs.csv",
    "freeflow_reference_summary.csv",
    "freeflow_reference_report.md",
    "multiseed_baseline_runs.csv",
    "multiseed_baseline_summary.csv",
    "multiseed_baseline_report.md",
)
SPEC = importlib.util.spec_from_file_location("analyze_freeflow_reference", SCRIPT)
audit = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(audit)


class ManifestFixtureMixin:
    def make_manifest_fixture(self):
        temp_dir = tempfile.TemporaryDirectory()
        root = Path(temp_dir.name)
        manifest = ROOT / "freeflow_cleanup_protection_manifest.csv"
        shutil.copy2(manifest, root / manifest.name)
        with manifest.open("r", encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        for row in rows:
            source = ROOT / row["file"]
            target = root / row["file"]
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        self.addCleanup(temp_dir.cleanup)
        return root

    def read_rows(self, root):
        with (root / "freeflow_cleanup_protection_manifest.csv").open(
            "r", encoding="utf-8-sig", newline=""
        ) as handle:
            return list(csv.DictReader(handle))

    def write_rows(self, root, rows, fieldnames=None):
        path = root / "freeflow_cleanup_protection_manifest.csv"
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames or rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)


class ManifestContractTests(ManifestFixtureMixin, unittest.TestCase):
    def test_missing_manifest_is_rejected(self):
        root = self.make_manifest_fixture()
        (root / "freeflow_cleanup_protection_manifest.csv").unlink()
        errors = audit.check_protection_manifest(root)
        self.assertTrue(any("manifest missing" in error for error in errors))

    def test_manifest_requires_exactly_161_rows(self):
        root = self.make_manifest_fixture()
        rows = self.read_rows(root)[:-1]
        self.write_rows(root, rows)
        errors = audit.check_protection_manifest(root)
        self.assertTrue(any("expected 161 rows" in error for error in errors))

    def test_manifest_requires_exact_category_counts(self):
        root = self.make_manifest_fixture()
        rows = self.read_rows(root)
        rows[4]["category"] = "core"
        self.write_rows(root, rows)
        errors = audit.check_protection_manifest(root)
        self.assertTrue(any("category counts" in error for error in errors))

    def test_manifest_rejects_windows_casefold_duplicate(self):
        root = self.make_manifest_fixture()
        rows = self.read_rows(root)
        rows[1]["file"] = rows[0]["file"].upper().replace("/", "\\")
        self.write_rows(root, rows)
        errors = audit.check_protection_manifest(root)
        self.assertTrue(any("duplicate protected file" in error for error in errors))


class ManifestHashTests(ManifestFixtureMixin, unittest.TestCase):
    def test_manifest_rejects_before_after_hash_mismatch(self):
        root = self.make_manifest_fixture()
        rows = self.read_rows(root)
        rows[10]["cleanup_after_sha256"] = "0" * 64
        self.write_rows(root, rows)
        errors = audit.check_protection_manifest(root)
        self.assertTrue(any("before/after hash mismatch" in error for error in errors))

    def test_manifest_rejects_non_unchanged_status(self):
        root = self.make_manifest_fixture()
        rows = self.read_rows(root)
        rows[10]["status"] = "CHANGED"
        self.write_rows(root, rows)
        errors = audit.check_protection_manifest(root)
        self.assertTrue(any("status is not UNCHANGED" in error for error in errors))

    def test_manifest_rejects_current_file_hash_mismatch(self):
        root = self.make_manifest_fixture()
        rows = self.read_rows(root)
        (root / rows[10]["file"]).write_bytes(b"changed")
        errors = audit.check_protection_manifest(root)
        self.assertTrue(any("current hash mismatch" in error for error in errors))

    def test_manifest_rejects_forged_core_hash_even_when_csv_is_self_consistent(self):
        root = self.make_manifest_fixture()
        rows = self.read_rows(root)
        row = next(item for item in rows if item["file"] == "osm.net.xml")
        row["cleanup_before_sha256"] = row["cleanup_after_sha256"] = "0" * 64
        self.write_rows(root, rows)
        errors = audit.check_protection_manifest(root)
        self.assertTrue(any("frozen core hash mismatch" in error for error in errors))


class AnalysisFixtureMixin(ManifestFixtureMixin):
    def make_analysis_fixture(self):
        root = self.make_manifest_fixture()
        required_inputs = [
            "osm.rou.xml",
            "main_only_200.rou.xml",
            "main_only_400.rou.xml",
            "ramp_only_50.rou.xml",
        ]
        required_inputs.extend(
            f"{prefix}_{case}_seed{seed}{suffix}"
            for case in ("main_only_200", "main_only_400", "ramp_only_50")
            for seed in range(1, 11)
            for prefix, suffix in (("tripinfo", ".xml"), ("sumo_error", ".log"))
        )
        required_inputs.extend(
            f"tripinfo_main{main}_ramp{ramp}_seed{seed}.xml"
            for main, ramp in audit.MIXED_SCENARIOS
            for seed in range(1, 11)
        )
        for name in required_inputs:
            shutil.copy2(ROOT / name, root / name)
        for name in ANALYSIS_OUTPUTS:
            source = ROOT / name
            if source.exists():
                shutil.copy2(source, root / name)
        return root

    def file_bytes(self, root, names):
        return {name: (root / name).read_bytes() for name in names}

    def run_analyzer(self, root):
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--root", str(root)],
            capture_output=True,
            text=True,
        )


class ProcessGateTests(AnalysisFixtureMixin, unittest.TestCase):

    def test_process_returns_2_when_manifest_is_missing(self):
        root = self.make_analysis_fixture()
        (root / "freeflow_cleanup_protection_manifest.csv").unlink()
        result = self.run_analyzer(root)
        self.assertEqual(result.returncode, 2)
        self.assertIn("VALIDATION_PASS=False", result.stdout)

    def test_report_describes_reanalysis_not_new_sumo_runs(self):
        root = self.make_analysis_fixture()
        result = self.run_analyzer(root)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        report = (root / "freeflow_reference_report.md").read_text(encoding="utf-8")
        self.assertIn("本轮未运行SUMO", report)
        self.assertIn("既有输入", report)
        self.assertNotIn("本轮创建或生成：main_only_200.rou.xml", report)


class ExistingNegativeGateTests(AnalysisFixtureMixin, unittest.TestCase):
    def test_wrong_main_only_200_rate(self):
        root = self.make_analysis_fixture()
        route = root / "main_only_200.rou.xml"
        route.write_text(
            route.read_text(encoding="utf-8").replace('vehsPerHour="200"', 'vehsPerHour="201"'),
            encoding="utf-8",
        )
        result = self.run_analyzer(root)
        self.assertEqual(result.returncode, 2)
        self.assertIn("VALIDATION_PASS=False", result.stdout)

    def test_emergency_braking_log(self):
        root = self.make_analysis_fixture()
        log = root / "sumo_error_main_only_200_seed1.log"
        log.write_text(log.read_text(encoding="utf-8") + "Emergency braking\n", encoding="utf-8")
        result = self.run_analyzer(root)
        self.assertEqual(result.returncode, 2)
        self.assertIn("VALIDATION_PASS=False", result.stdout)

    def test_missing_error_log(self):
        root = self.make_analysis_fixture()
        (root / "sumo_error_main_only_200_seed1.log").unlink()
        result = self.run_analyzer(root)
        self.assertEqual(result.returncode, 2)
        self.assertIn("VALIDATION_PASS=False", result.stdout)


class InvalidManifestPreservationTests(AnalysisFixtureMixin, unittest.TestCase):
    def test_invalid_status_exits_2_without_rewriting_manifest_or_outputs(self):
        root = self.make_analysis_fixture()
        rows = self.read_rows(root)
        rows[10]["status"] = "CHANGED"
        self.write_rows(root, rows)
        before_manifest = (root / "freeflow_cleanup_protection_manifest.csv").read_bytes()
        before_outputs = self.file_bytes(root, ANALYSIS_OUTPUTS)

        result = self.run_analyzer(root)

        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("VALIDATION_PASS=False", result.stdout)
        self.assertEqual(
            (root / "freeflow_cleanup_protection_manifest.csv").read_bytes(),
            before_manifest,
        )
        self.assertEqual(self.file_bytes(root, ANALYSIS_OUTPUTS), before_outputs)

    def test_missing_before_hash_column_exits_2_without_traceback_or_mutation(self):
        root = self.make_analysis_fixture()
        rows = self.read_rows(root)
        for row in rows:
            row.pop("cleanup_before_sha256")
        self.write_rows(root, rows)
        before_manifest = (root / "freeflow_cleanup_protection_manifest.csv").read_bytes()
        before_outputs = self.file_bytes(root, ANALYSIS_OUTPUTS)

        result = self.run_analyzer(root)

        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("VALIDATION_PASS=False", result.stdout)
        self.assertNotIn("Traceback", result.stderr)
        self.assertIn("manifest missing required columns", result.stdout)
        self.assertEqual(
            (root / "freeflow_cleanup_protection_manifest.csv").read_bytes(),
            before_manifest,
        )
        self.assertEqual(self.file_bytes(root, ANALYSIS_OUTPUTS), before_outputs)

    def test_protected_directory_is_rejected_before_analysis(self):
        root = self.make_analysis_fixture()
        rows = self.read_rows(root)
        victim = next(row["file"] for row in rows if row["category"] == "historical_seed42_tripinfo")
        target = root / victim
        target.unlink()
        target.mkdir()

        errors = audit.check_protection_manifest(root)

        self.assertTrue(any("not a regular file" in error for error in errors), errors)
