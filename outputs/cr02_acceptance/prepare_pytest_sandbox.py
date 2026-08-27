from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path


EVIDENCE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = EVIDENCE_DIR.parents[1]
SANDBOX_PROJECT = EVIDENCE_DIR / "pytest_sandbox" / "project"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    modules = [
        "build_network.py", "classify.py", "controllers.py", "env.py",
        "experiment_config.py", "metrics.py", "rou_generate.py", "scan_demand.py",
    ]
    network_sources = ["merge.add.xml", "merge.con.xml", "merge.edg.xml", "merge.nod.xml", "merge.tll.xml"]
    sources = [PROJECT_ROOT / name for name in modules]
    sources.extend(sorted((PROJECT_ROOT / "tests").glob("*.py")))
    sources.extend(PROJECT_ROOT / "network" / name for name in network_sources)
    copied = []
    for source in sources:
        target = SANDBOX_PROJECT / source.relative_to(PROJECT_ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        copied.append(
            {
                "source": str(source),
                "target": str(target),
                "bytes": target.stat().st_size,
                "source_sha256": sha256(source),
                "target_sha256": sha256(target),
            }
        )
    if any(row["source_sha256"] != row["target_sha256"] for row in copied):
        raise RuntimeError("pytest sandbox copy hash mismatch")
    (EVIDENCE_DIR / "pytest_sandbox_copy_manifest.json").write_text(
        json.dumps(
            {
                "created_utc": datetime.now(timezone.utc).isoformat(),
                "isolated_cwd": str(SANDBOX_PROJECT),
                "files": copied,
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    print(SANDBOX_PROJECT)


if __name__ == "__main__":
    main()
