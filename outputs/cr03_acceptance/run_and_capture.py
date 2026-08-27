from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def main() -> int:
    if len(sys.argv) < 3:
        raise SystemExit("usage: run_and_capture.py LABEL COMMAND [ARG ...]")
    label = sys.argv[1]
    command = sys.argv[2:]
    evidence_dir = Path(__file__).resolve().parent
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    result = subprocess.run(command, text=True, capture_output=True, env=env, check=False)
    (evidence_dir / f"{label}_stdout.log").write_text(result.stdout, encoding="utf-8")
    (evidence_dir / f"{label}_stderr.log").write_text(result.stderr, encoding="utf-8")
    (evidence_dir / f"{label}_result.json").write_text(
        json.dumps(
            {
                "captured_utc": datetime.now(timezone.utc).isoformat(),
                "cwd": os.getcwd(),
                "command": command,
                "exit_code": result.returncode,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(result.stdout, end="")
    print(result.stderr, end="", file=sys.stderr)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
