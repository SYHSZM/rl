from __future__ import annotations

from pathlib import Path
import subprocess


NETWORK_DIR = Path(__file__).resolve().parent / "network"


def required_detector_ids() -> set[str]:
    return {
        "det_main_0",
        "det_main_1",
        "det_main_2",
        "det_main_3",
        "det_main_4",
        "det_bottleneck_down",
        "det_ramp_queue",
        "det_ramp_arrival",
    }


def build_network(output_net: str | Path = "network/merge.net.xml") -> Path:
    output_net = Path(output_net)
    if not output_net.is_absolute():
        output_net = Path(__file__).resolve().parent / output_net
    output_net.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        "netconvert",
        "--node-files",
        str(NETWORK_DIR / "merge.nod.xml"),
        "--edge-files",
        str(NETWORK_DIR / "merge.edg.xml"),
        "--connection-files",
        str(NETWORK_DIR / "merge.con.xml"),
        "--tllogic-files",
        str(NETWORK_DIR / "merge.tll.xml"),
        "--output-file",
        str(output_net),
        "--no-turnarounds",
        "true",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"netconvert failed\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}")
    return output_net


if __name__ == "__main__":
    print(build_network())
