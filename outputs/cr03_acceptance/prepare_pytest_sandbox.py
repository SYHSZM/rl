from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parents[1]
SANDBOX = ROOT / "pytest_sandbox" / "project"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    modules = [
        "build_network.py", "classify.py", "controllers.py", "env.py",
        "experiment_config.py", "metrics.py", "rou_generate.py", "scan_demand.py",
    ]
    network = ["merge.add.xml", "merge.con.xml", "merge.edg.xml", "merge.nod.xml", "merge.tll.xml"]
    sources = [PROJECT / name for name in modules]
    sources.extend(sorted((PROJECT / "tests").glob("*.py")))
    sources.extend(PROJECT / "network" / name for name in network)
    copied = []
    for source in sources:
        target = SANDBOX / source.relative_to(PROJECT)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        copied.append({"source": str(source), "target": str(target), "sha256": sha256(target)})
        if sha256(source) != sha256(target):
            raise RuntimeError("sandbox copy hash mismatch")
    (ROOT / "pytest_sandbox_copy_manifest.json").write_text(
        json.dumps({"created_utc": datetime.now(timezone.utc).isoformat(), "files": copied}, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(SANDBOX)


if __name__ == "__main__":
    main()
