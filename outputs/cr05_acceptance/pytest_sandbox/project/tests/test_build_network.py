import hashlib
import shutil
import xml.etree.ElementTree as ET

import pytest

import build_network as build_network_module
from build_network import build_network, required_detector_ids


def test_required_detector_ids_are_stable():
    assert required_detector_ids() == {
        "det_main_0",
        "det_main_1",
        "det_main_2",
        "det_main_3",
        "det_main_4",
        "det_bottleneck_down",
        "det_ramp_queue",
        "det_ramp_arrival",
    }


def test_build_network_creates_net_file(tmp_path):
    out = tmp_path / "merge.net.xml"
    path = build_network(out)
    assert path == out
    assert out.exists()
    root = ET.parse(out).getroot()
    edges = {edge.attrib["id"] for edge in root.findall("edge") if not edge.attrib["id"].startswith(":")}
    assert {"main_0", "main_1", "main_2", "main_3", "main_4", "main_5", "ramp_0"}.issubset(edges)


def test_merge_signal_uses_permissive_ramp_green():
    root = ET.parse("network/merge.tll.xml").getroot()
    phase = root.find(".//phase")
    assert phase.attrib["state"] == "gGGG"


def test_preflight_builds_and_verifies_frozen_network(tmp_path):
    preflight_network = getattr(build_network_module, "preflight_network", None)
    verify_frozen_network = getattr(build_network_module, "verify_frozen_network", None)
    assert preflight_network is not None, "CR-05 requires preflight_network"
    assert verify_frozen_network is not None, "CR-05 requires verify_frozen_network"

    frozen = preflight_network(tmp_path / "preflight" / "merge.net.xml")

    assert frozen.net_path == tmp_path / "preflight" / "merge.net.xml"
    assert frozen.net_sha256 == hashlib.sha256(frozen.net_path.read_bytes()).hexdigest()
    assert set(frozen.source_sha256) == {
        "merge.nod.xml",
        "merge.edg.xml",
        "merge.con.xml",
        "merge.tll.xml",
        "merge.add.xml",
    }
    assert verify_frozen_network(frozen) == frozen.net_path


def test_frozen_network_guard_rejects_temporary_source_mutation(tmp_path):
    preflight_network = getattr(build_network_module, "preflight_network", None)
    verify_frozen_network = getattr(build_network_module, "verify_frozen_network", None)
    mismatch_error = getattr(build_network_module, "FrozenNetworkMismatchError", None)
    assert preflight_network is not None and verify_frozen_network is not None and mismatch_error is not None
    source_dir = tmp_path / "network_source_copy"
    source_dir.mkdir()
    for name in ("merge.nod.xml", "merge.edg.xml", "merge.con.xml", "merge.tll.xml", "merge.add.xml"):
        shutil.copy2(build_network_module.NETWORK_DIR / name, source_dir / name)
    frozen = preflight_network(tmp_path / "frozen" / "merge.net.xml", source_dir=source_dir)
    (source_dir / "merge.add.xml").write_text(
        (source_dir / "merge.add.xml").read_text(encoding="utf-8") + "\n<!-- deliberate temporary-copy mutation -->\n",
        encoding="utf-8",
    )

    with pytest.raises(mismatch_error, match="merge.add.xml"):
        verify_frozen_network(frozen)


def test_frozen_network_guard_rejects_temporary_net_mutation(tmp_path):
    preflight_network = getattr(build_network_module, "preflight_network", None)
    verify_frozen_network = getattr(build_network_module, "verify_frozen_network", None)
    mismatch_error = getattr(build_network_module, "FrozenNetworkMismatchError", None)
    assert preflight_network is not None and verify_frozen_network is not None and mismatch_error is not None
    frozen = preflight_network(tmp_path / "frozen" / "merge.net.xml")
    frozen.net_path.write_bytes(frozen.net_path.read_bytes() + b"\n<!-- deliberate temporary frozen-net mutation -->\n")

    with pytest.raises(mismatch_error, match="merge.net.xml"):
        verify_frozen_network(frozen)
