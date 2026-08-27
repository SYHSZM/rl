from __future__ import annotations

import argparse
from pathlib import Path

from osm_control_adapter import build_control_adapter_audit


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit the Stage 4A OSM control adapter without starting SUMO")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--net", default="osm.net.xml")
    args = parser.parse_args(argv)
    result = build_control_adapter_audit(Path(args.output_dir), Path(args.net))
    output = Path(args.output_dir)
    print(f"AUDIT_PATH={output / 'control_adapter_audit.json'}")
    detector = output / "osm_control.add.xml"
    print(f"DETECTOR_PATH={detector if result['valid'] else None}")
    for error in result["errors"]:
        print(f"ERROR={error}")
    print(f"VALIDATION_PASS={result['valid']}")
    return 0 if result["valid"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
