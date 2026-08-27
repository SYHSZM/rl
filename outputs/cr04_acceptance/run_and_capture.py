from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def main() -> int:
    if len(sys.argv) < 3:
        raise SystemExit("usage: run_and_capture.py LABEL COMMAND [ARG ...]")
    label, command = sys.argv[1], sys.argv[2:]
    result = subprocess.run(command, text=True, capture_output=True)
    (ROOT / f"{label}_stdout.log").write_text(result.stdout, encoding="utf-8")
    (ROOT / f"{label}_stderr.log").write_text(result.stderr, encoding="utf-8")
    (ROOT / f"{label}_result.json").write_text(
        json.dumps(
            {
                "captured_utc": datetime.now(timezone.utc).isoformat(),
                "cwd": str(Path.cwd()),
                "command": command,
                "exit_code": result.returncode,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
