from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import subprocess


NETWORK_DIR = Path(__file__).resolve().parent / "network"
NETWORK_SOURCE_FILENAMES = (
    "merge.nod.xml",
    "merge.edg.xml",
    "merge.con.xml",
    "merge.tll.xml",
    "merge.add.xml",
)


class FrozenNetworkMismatchError(RuntimeError):
    pass


@dataclass(frozen=True)
class FrozenNetwork:
    net_path: Path
    net_sha256: str
    source_dir: Path
    source_sha256: dict[str, str]


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


def build_network(output_net: str | Path = "network/merge.net.xml", source_dir: str | Path = NETWORK_DIR) -> Path:
    output_net = Path(output_net)
    if not output_net.is_absolute():
        output_net = Path(__file__).resolve().parent / output_net
    source_dir = Path(source_dir).resolve()
    output_net.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        "netconvert",
        "--node-files",
        str(source_dir / "merge.nod.xml"),
        "--edge-files",
        str(source_dir / "merge.edg.xml"),
        "--connection-files",
        str(source_dir / "merge.con.xml"),
        "--tllogic-files",
        str(source_dir / "merge.tll.xml"),
        "--output-file",
        str(output_net),
        "--no-turnarounds",
        "true",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"netconvert failed\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}")
    return output_net


def preflight_network(output_net: str | Path, source_dir: str | Path = NETWORK_DIR) -> FrozenNetwork:
    source_dir = Path(source_dir).resolve()
    before = _source_hashes(source_dir)
    net_path = build_network(output_net, source_dir=source_dir).resolve()
    after = _source_hashes(source_dir)
    if before != after:
        changed = sorted(name for name in set(before) | set(after) if before.get(name) != after.get(name))
        raise FrozenNetworkMismatchError(f"network source changed during preflight: {', '.join(changed)}")
    return FrozenNetwork(net_path=net_path, net_sha256=_sha256(net_path), source_dir=source_dir, source_sha256=before)


def verify_frozen_network(frozen: FrozenNetwork) -> Path:
    mismatches = []
    if not frozen.net_path.is_file() or _sha256(frozen.net_path) != frozen.net_sha256:
        mismatches.append(frozen.net_path.name)
    current_sources = _source_hashes(frozen.source_dir)
    mismatches.extend(
        name for name in sorted(set(frozen.source_sha256) | set(current_sources))
        if frozen.source_sha256.get(name) != current_sources.get(name)
    )
    if mismatches:
        raise FrozenNetworkMismatchError(f"frozen network mismatch: {', '.join(mismatches)}")
    return frozen.net_path


def _source_hashes(source_dir: Path) -> dict[str, str]:
    missing = [name for name in NETWORK_SOURCE_FILENAMES if not (source_dir / name).is_file()]
    if missing:
        raise FrozenNetworkMismatchError(f"missing network source: {', '.join(missing)}")
    return {name: _sha256(source_dir / name) for name in NETWORK_SOURCE_FILENAMES}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


if __name__ == "__main__":
    print(build_network())
