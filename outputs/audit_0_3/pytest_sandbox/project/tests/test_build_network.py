import xml.etree.ElementTree as ET

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
