from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    files = [
        {
            "path": str(path.relative_to(ROOT)).replace("\\", "/"),
            "bytes": path.stat().st_size,
            "sha256": sha256(path),
        }
        for path in sorted(ROOT.rglob("*"))
        if path.is_file() and path.name != "evidence_manifest.json"
    ]
    manifest = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "scope": "Task 3 / CR-02 only",
        "files": files,
    }
    (ROOT / "evidence_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
    )
    print(json.dumps({"files": len(files)}, indent=2))


if __name__ == "__main__":
    main()
