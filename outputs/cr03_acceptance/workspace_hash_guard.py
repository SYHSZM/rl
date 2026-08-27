from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parents[1]
BEFORE = ROOT / "full_pytest_real_workspace_before.json"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def snapshot() -> dict[str, object]:
    files = [PROJECT / name for name in ("env.py", "scan_demand.py")]
    files.extend(PROJECT / "tests" / name for name in ("test_env_runner.py", "test_scan_demand.py"))
    files.extend(sorted((PROJECT / "network").glob("*.xml")))
    return {
        "captured_utc": datetime.now(timezone.utc).isoformat(),
        "hashes": {str(path.relative_to(PROJECT)).replace("\\", "/"): sha256(path) for path in files if path.is_file()},
    }


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in {"before", "after"}:
        raise SystemExit("usage: workspace_hash_guard.py before|after")
    current = snapshot()
    if sys.argv[1] == "before":
        BEFORE.write_text(json.dumps(current, indent=2, sort_keys=True), encoding="utf-8")
        return 0
    before = json.loads(BEFORE.read_text(encoding="utf-8"))
    changed = sorted(
        name for name in set(before["hashes"]) | set(current["hashes"])
        if before["hashes"].get(name) != current["hashes"].get(name)
    )
    guard = {"before": before, "after": current, "changed_paths": changed, "unchanged": not changed}
    (ROOT / "full_pytest_real_workspace_hash_guard.json").write_text(
        json.dumps(guard, indent=2, sort_keys=True), encoding="utf-8"
    )
    print(json.dumps({"unchanged": not changed, "changed_paths": changed}, indent=2))
    return 0 if not changed else 1


if __name__ == "__main__":
    raise SystemExit(main())
