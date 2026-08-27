from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


EVIDENCE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = EVIDENCE_DIR.parents[1]
BEFORE_PATH = EVIDENCE_DIR / "full_pytest_real_workspace_before.json"
GUARD_PATH = EVIDENCE_DIR / "full_pytest_real_workspace_hash_guard.json"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def snapshot() -> dict[str, object]:
    files = [PROJECT_ROOT / name for name in ("env.py", "metrics.py")]
    files.extend(PROJECT_ROOT / "tests" / name for name in ("test_env_runner.py", "test_metrics.py"))
    files.extend(sorted((PROJECT_ROOT / "network").glob("*.xml")))
    return {
        "captured_utc": datetime.now(timezone.utc).isoformat(),
        "hashes": {
            str(path.relative_to(PROJECT_ROOT)).replace("\\", "/"): sha256(path)
            for path in files if path.is_file()
        },
    }


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in {"before", "after"}:
        raise SystemExit("usage: workspace_hash_guard.py before|after")
    current = snapshot()
    if sys.argv[1] == "before":
        BEFORE_PATH.write_text(json.dumps(current, indent=2, sort_keys=True), encoding="utf-8")
        return 0
    before = json.loads(BEFORE_PATH.read_text(encoding="utf-8"))
    changed = sorted(
        path for path in set(before["hashes"]) | set(current["hashes"])
        if before["hashes"].get(path) != current["hashes"].get(path)
    )
    guard = {"before": before, "after": current, "changed_paths": changed, "unchanged": not changed}
    GUARD_PATH.write_text(json.dumps(guard, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({"unchanged": guard["unchanged"], "changed_paths": changed}, indent=2))
    return 0 if guard["unchanged"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
