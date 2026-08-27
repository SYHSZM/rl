from __future__ import annotations

import copy
import csv
import json
import tempfile
import types
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest import mock

import osm_smoke_runner as smoke


ROOT = Path(__file__).resolve().parents[1]


class SmokePreflightTests(unittest.TestCase):
    def test_workspace_preconditions_are_read_only_and_pass(self):
        with tempfile.TemporaryDirectory() as temp:
            formal = Path(temp) / "not-created"
            self.assertFalse(formal.exists())
            result = smoke.validate_preconditions(
                ROOT, formal, Path(r"D:\sumo-1.25.0\bin\sumo.exe")
            )
            self.assertTrue(result["valid"], result["errors"])
            self.assertFalse(formal.exists())
            self.assertEqual(result["protection_errors"], [])

    def test_preflight_rejects_changed_demand_hash(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "demand_delivery/fixed").mkdir(parents=True)
            (root / "demand_delivery/fixed/600+60.rou.xml").write_text(
                "<routes/>", encoding="utf-8"
            )
            errors = smoke._validate_frozen_hashes(root)
            self.assertTrue(any("demand SHA-256" in item for item in errors))

    def test_preflight_rejects_nonempty_output_without_changing_it(self):
        output = ROOT / "outputs/stage4b_smoke/main600_ramp60_alinea_seed1"
        before = {path.relative_to(output): path.read_bytes() for path in output.rglob("*") if path.is_file()}
        result = smoke.validate_preconditions(
            ROOT, output, Path(r"D:\sumo-1.25.0\bin\sumo.exe")
        )
        self.assertFalse(result["valid"])
        after = {path.relative_to(output): path.read_bytes() for path in output.rglob("*") if path.is_file()}
        self.assertEqual(after, before)


class RuntimeDetectorTests(unittest.TestCase):
    def test_runtime_detector_preserves_definitions_and_rewrites_only_files(self):
        source = ROOT / "control_adapter/osm_control.add.xml"
        original = source.read_bytes()
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "runtime_osm_control.add.xml"
            native = Path(temp) / "native"
            digest = smoke.write_runtime_detector_file(source, output, native)
            self.assertEqual(digest, smoke.sha256(output))
            self.assertEqual(source.read_bytes(), original)
            elements = list(ET.parse(output).getroot())
            self.assertEqual([item.attrib["id"] for item in elements], [
                "osm_det_main_down_1", "osm_det_main_down_2",
                "osm_det_ramp_queue", "osm_det_ramp_arrival",
            ])
            for item in elements:
                target = Path(item.attrib["file"])
                self.assertTrue(target.is_absolute())
                self.assertEqual(target.parent, native.resolve())

    def test_runtime_detector_rejects_unknown_or_duplicate_detector(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "bad.add.xml"
            source.write_text(
                '<additional><inductionLoop id="unknown" lane="x" pos="0" '
                'freq="30" file="x.xml"/></additional>', encoding="utf-8"
            )
            with self.assertRaisesRegex(ValueError, "exactly the four frozen detector IDs"):
                smoke.write_runtime_detector_file(
                    source, Path(temp) / "run.add.xml", Path(temp) / "native"
                )


class SumoCommandTests(unittest.TestCase):
    def test_command_is_cli_sumo_with_frozen_inputs_and_outputs(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp).resolve()
            runtime = output / "runtime_osm_control.add.xml"
            command = smoke.build_sumo_command(ROOT, output, runtime, smoke.SUMO_BINARY)
            self.assertEqual(command[0], str(smoke.SUMO_BINARY.resolve()))
            self.assertNotIn("sumo-gui", " ".join(command).lower())
            pairs = dict(zip(command[1::2], command[2::2]))
            self.assertEqual(pairs["--begin"], "0")
            self.assertEqual(pairs["--end"], "600")
            self.assertEqual(pairs["--step-length"], "1.0")
            self.assertEqual(pairs["--seed"], "1")
            self.assertEqual(pairs["--time-to-teleport"], "-1")
            self.assertEqual(Path(pairs["--net-file"]), (ROOT / "osm.net.xml").resolve())
            self.assertEqual(Path(pairs["--route-files"]), (ROOT / "demand_delivery/fixed/600+60.rou.xml").resolve())
            self.assertEqual(Path(pairs["--additional-files"]), runtime.resolve())


class FakeTrafficLight:
    def __init__(self, forced_state=None):
        self.state = "GG"
        self.forced_state = forced_state

    def setRedYellowGreenState(self, tls_id, state):
        if tls_id != "J5":
            raise AssertionError(tls_id)
        self.state = state

    def getRedYellowGreenState(self, tls_id):
        if tls_id != "J5":
            raise AssertionError(tls_id)
        return self.forced_state if self.forced_state is not None else self.state


class FakeSimulation:
    def __init__(self, owner):
        self.owner = owner

    def getTime(self):
        return float(self.owner.step_calls)

    def getDepartedIDList(self):
        if self.owner.step_calls == 1:
            return ("mainline_000000",)
        if self.owner.step_calls == 2:
            return ("ramp_000000",)
        return ()

    def getArrivedIDList(self):
        return ("mainline_000000",) if self.owner.step_calls == 10 else ()

    def getStartingTeleportNumber(self):
        return 0

    def getCollidingVehiclesNumber(self):
        return 0


class FakeInductionLoop:
    def getLastStepOccupancy(self, detector_id):
        return {"osm_det_main_down_1": 20.0, "osm_det_main_down_2": 30.0}[detector_id]

    def getLastStepVehicleNumber(self, detector_id):
        return 1


class FakeLaneArea:
    def getLastStepVehicleNumber(self, detector_id):
        return 3

    def getLastStepHaltingNumber(self, detector_id):
        return 1


class FakeConnection:
    def __init__(self, force_observed_state=None):
        self.step_calls = 0
        self.close_calls = 0
        self.trafficlight = FakeTrafficLight(force_observed_state)
        self.simulation = FakeSimulation(self)
        self.inductionloop = FakeInductionLoop()
        self.lanearea = FakeLaneArea()

    def simulationStep(self):
        self.step_calls += 1

    def close(self):
        self.close_calls += 1


class FakeRuntimeTests(unittest.TestCase):
    def test_tls_roundtrip_is_rr_then_GG_without_simulation_step(self):
        conn = FakeConnection()
        result = smoke.perform_tls_roundtrip(conn)
        self.assertTrue(result["valid"])
        self.assertEqual(result["observed"], ["rr", "GG"])
        self.assertEqual(conn.step_calls, 0)

    def test_control_loop_records_exactly_0_through_599(self):
        conn = FakeConnection()
        result = smoke.execute_control_loop(conn)
        self.assertEqual(conn.step_calls, 600)
        self.assertEqual([row["time_s"] for row in result["signal_rows"]], list(range(600)))
        self.assertEqual(len(result["detector_rows"]), 600)
        self.assertEqual(len(result["queue_rows"]), 600)
        self.assertEqual(result["state_mismatches"], 0)
        self.assertEqual(result["mainline_departed"], 1)
        self.assertEqual(result["ramp_departed"], 1)
        self.assertGreater(result["arrived"], 0)
        self.assertTrue(all(0.0 <= row["occupancy"] <= 1.0 for row in result["detector_rows"]))
        for field in ("departed", "arrived", "teleport", "collision"):
            values = [row[field] for row in result["detector_rows"]]
            self.assertEqual(values, sorted(values))
        last = result["detector_rows"][-1]
        self.assertEqual(last["arrived"], result["arrived"])
        self.assertEqual(last["teleport"], result["teleport"])
        self.assertEqual(last["collision"], result["collision"])
        self.assertEqual(last["departed"], result["mainline_departed"] + result["ramp_departed"])

    def test_control_updates_only_at_30_second_boundaries(self):
        result = smoke.execute_control_loop(FakeConnection())
        self.assertEqual(result["controller_update_times"], list(range(0, 600, 30)))

    def test_observed_signal_mismatch_is_counted(self):
        result = smoke.execute_control_loop(FakeConnection(force_observed_state="rr"))
        self.assertGreater(result["state_mismatches"], 0)


def clean_log_counts():
    return {"fatal": 0, "emergency_braking": 0, "warning": 0}


def valid_fake_runtime():
    return smoke.execute_control_loop(FakeConnection())


def write_fake_required_outputs(output, runtime):
    output.mkdir(parents=True, exist_ok=True)
    smoke.write_csv(output / "signal_timeline.csv", runtime["signal_rows"], smoke.SIGNAL_FIELDS)
    smoke.write_csv(output / "detector_timeline.csv", runtime["detector_rows"], smoke.DETECTOR_FIELDS)
    smoke.write_csv(output / "ramp_queue_timeline.csv", runtime["queue_rows"], smoke.QUEUE_FIELDS)
    smoke.write_runtime_detector_file(
        ROOT / "control_adapter/osm_control.add.xml",
        output / "runtime_osm_control.add.xml",
        output / "native",
    )
    (output / "tripinfo.xml").write_text(
        '<?xml version="1.0"?><tripinfos><tripinfo id="mainline_000000"/></tripinfos>',
        encoding="utf-8",
    )
    (output / "sumo.log").write_text("", encoding="utf-8")
    (output / "sumo_error.log").write_text("", encoding="utf-8")
    for detector_id in (
        "osm_det_main_down_1", "osm_det_main_down_2",
        "osm_det_ramp_queue", "osm_det_ramp_arrival",
    ):
        (output / "native" / f"{detector_id}.xml").write_text(
            '<?xml version="1.0"?><detector/>', encoding="utf-8"
        )


class OutputValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.output = Path(self.temp.name)
        self.runtime = valid_fake_runtime()
        write_fake_required_outputs(self.output, self.runtime)

    def tearDown(self):
        self.temp.cleanup()

    def test_validation_rejects_signal_mismatch(self):
        runtime = dict(self.runtime)
        runtime["state_mismatches"] = 1
        errors = smoke.validate_run_outputs(self.output, runtime, clean_log_counts(), ROOT)
        self.assertTrue(any("state mismatch" in item for item in errors))

    def test_three_csv_files_have_exact_time_axis(self):
        for name in ("signal_timeline.csv", "detector_timeline.csv", "ramp_queue_timeline.csv"):
            with self.subTest(name=name):
                with (self.output / name).open(encoding="utf-8-sig", newline="") as handle:
                    rows = list(csv.DictReader(handle))
                self.assertEqual(len(rows), 600)
                self.assertEqual([int(row["time_s"]) for row in rows], list(range(600)))

    def test_validation_rejects_out_of_range_occupancy(self):
        runtime = copy.deepcopy(self.runtime)
        runtime["detector_rows"][17]["occupancy"] = 1.01
        errors = smoke.validate_run_outputs(self.output, runtime, clean_log_counts(), ROOT)
        self.assertTrue(any("occupancy" in item for item in errors))

    def test_validation_rejects_missing_ramp_departure(self):
        runtime = dict(self.runtime)
        runtime["ramp_departed"] = 0
        errors = smoke.validate_run_outputs(self.output, runtime, clean_log_counts(), ROOT)
        self.assertTrue(any("ramp departed" in item for item in errors))

    def test_validation_rejects_collision_and_teleport(self):
        runtime = dict(self.runtime)
        runtime["collision"] = 1
        runtime["teleport"] = 1
        errors = smoke.validate_run_outputs(self.output, runtime, clean_log_counts(), ROOT)
        self.assertTrue(any("collision" in item for item in errors))
        self.assertTrue(any("teleport" in item for item in errors))

    def test_validation_rejects_fatal_and_emergency_braking(self):
        counts = {"fatal": 1, "emergency_braking": 1, "warning": 0}
        errors = smoke.validate_run_outputs(self.output, self.runtime, counts, ROOT)
        self.assertTrue(any("fatal" in item for item in errors))
        self.assertTrue(any("emergency braking" in item for item in errors))

    def test_validation_rejects_missing_and_malformed_xml(self):
        (self.output / "tripinfo.xml").unlink()
        missing = smoke.validate_run_outputs(self.output, self.runtime, clean_log_counts(), ROOT)
        self.assertTrue(any("tripinfo.xml" in item for item in missing))
        (self.output / "tripinfo.xml").write_text("<broken>", encoding="utf-8")
        malformed = smoke.validate_run_outputs(self.output, self.runtime, clean_log_counts(), ROOT)
        self.assertTrue(any("parse" in item for item in malformed))

    def test_unrelated_warning_is_recorded_but_not_fatal(self):
        (self.output / "sumo_error.log").write_text("Warning: benign test warning\n", encoding="utf-8")
        counts = smoke.scan_logs(self.output)
        self.assertEqual(counts["warning"], 1)
        self.assertEqual(counts["fatal"], 0)
        self.assertEqual(counts["emergency_braking"], 0)
        self.assertEqual(smoke.validate_run_outputs(self.output, self.runtime, counts, ROOT), [])

    def test_manifest_does_not_hash_itself(self):
        (self.output / "run_manifest.json").write_text("{}", encoding="utf-8")
        hashes = smoke.collect_output_hashes(self.output)
        self.assertNotIn("run_manifest.json", hashes)


class FakeTraciModule:
    __file__ = r"D:\sumo-1.25.0\tools\traci\__init__.py"
    constants = types.SimpleNamespace(TRACI_VERSION=22)

    def __init__(self, connection=None):
        self.connection = connection or FakeConnection()
        self.start_calls = 0

    def start(self, command, label):
        self.start_calls += 1
        self.label = label
        pairs = dict(zip(command[1::2], command[2::2]))
        Path(pairs["--log"]).write_text("", encoding="utf-8")
        Path(pairs["--error-log"]).write_text("", encoding="utf-8")
        Path(pairs["--tripinfo-output"]).write_text(
            '<?xml version="1.0"?><tripinfos><tripinfo id="mainline_000000"/></tripinfos>',
            encoding="utf-8",
        )
        for element in ET.parse(pairs["--additional-files"]).getroot():
            target = Path(element.attrib["file"])
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text('<?xml version="1.0"?><detector/>', encoding="utf-8")

    def getConnection(self, label):
        self.asserted_label = label
        return self.connection


class RunOrchestrationTests(unittest.TestCase):
    def test_fake_success_starts_and_closes_once(self):
        with tempfile.TemporaryDirectory() as temp:
            fake = FakeTraciModule()
            with mock.patch.object(smoke, "_import_traci", return_value=fake), mock.patch.object(
                smoke, "_sumo_version_text", return_value="SUMO version 1.25.0"
            ):
                result = smoke.run_smoke(ROOT, Path(temp) / "run", smoke.SUMO_BINARY)
            self.assertTrue(result["valid"], result["errors"])
            self.assertEqual(fake.start_calls, 1)
            self.assertEqual(fake.connection.close_calls, 1)

    def test_failure_is_preserved_and_second_call_does_not_start(self):
        class FailingConnection(FakeConnection):
            def simulationStep(self):
                super().simulationStep()
                if self.step_calls == 3:
                    raise RuntimeError("fake loop failure")

        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "run"
            fake = FakeTraciModule(FailingConnection())
            with mock.patch.object(smoke, "_import_traci", return_value=fake), mock.patch.object(
                smoke, "_sumo_version_text", return_value="SUMO version 1.25.0"
            ):
                first = smoke.run_smoke(ROOT, output, smoke.SUMO_BINARY)
                before = {p.relative_to(output): p.read_bytes() for p in output.rglob("*") if p.is_file()}
                second = smoke.run_smoke(ROOT, output, smoke.SUMO_BINARY)
            self.assertFalse(first["valid"])
            self.assertEqual(first["failure_stage"], "run_loop")
            self.assertTrue(any("fake loop failure" in item for item in first["errors"]))
            self.assertFalse(second["valid"])
            self.assertEqual(fake.start_calls, 1)
            self.assertEqual(fake.connection.close_calls, 1)
            after = {p.relative_to(output): p.read_bytes() for p in output.rglob("*") if p.is_file()}
            self.assertEqual(after, before)

    def test_preflight_cli_never_starts_or_creates_output(self):
        with tempfile.TemporaryDirectory() as temp:
            formal = Path(temp) / "formal"
            fake = FakeTraciModule()
            with mock.patch.object(smoke, "FORMAL_OUTPUT", formal), mock.patch.object(
                smoke, "_import_traci", return_value=fake
            ), mock.patch.object(smoke, "_sumo_version_text", return_value="SUMO version 1.25.0"):
                code = smoke.main(["--preflight-only"])
            self.assertEqual(code, 0)
            self.assertEqual(fake.start_calls, 0)
            self.assertFalse(formal.exists())


if __name__ == "__main__":
    unittest.main()
