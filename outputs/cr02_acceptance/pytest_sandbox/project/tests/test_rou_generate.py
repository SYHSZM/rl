import xml.etree.ElementTree as ET

from experiment_config import DemandPoint, default_config
from rou_generate import generate_rou_file


def test_generate_route_has_three_mainline_and_ramp_phases(tmp_path):
    path = tmp_path / "merge.rou.xml"
    info = generate_rou_file(path, DemandPoint(4200, 750), seed=2, config=default_config())
    root = ET.parse(path).getroot()
    flows = {flow.attrib["id"]: flow.attrib for flow in root.findall("flow")}
    assert flows["main_0"]["vehsPerHour"] == "2730.0"
    assert flows["main_1"]["vehsPerHour"] == "4200.0"
    assert flows["main_2"]["vehsPerHour"] == "2940.0"
    assert flows["ramp_0"]["vehsPerHour"] == "487.5"
    assert flows["ramp_1"]["vehsPerHour"] == "750.0"
    assert flows["ramp_2"]["vehsPerHour"] == "525.0"
    assert info["seed"] == 2


def test_generate_route_uses_merge_edge_ids(tmp_path):
    path = tmp_path / "merge.rou.xml"
    generate_rou_file(path, DemandPoint(3000, 300), seed=0, config=default_config())
    root = ET.parse(path).getroot()
    routes = {route.attrib["id"]: route.attrib["edges"] for route in root.findall("route")}
    assert routes["main_route"].startswith("main_0")
    assert "main_5" in routes["main_route"]
    assert routes["ramp_route"].startswith("ramp_0")
    assert "main_5" in routes["ramp_route"]
