from __future__ import annotations

import argparse
from pathlib import Path

from calibrated_demand import build_delivery


def main() -> int:
    parser = argparse.ArgumentParser(description="Build Stage 2 calibrated demand delivery assets.")
    parser.add_argument("--output-dir", type=Path, default=Path("demand_delivery"))
    parser.add_argument("--template", type=Path, default=Path("osm.rou.xml"))
    args = parser.parse_args()
    report = build_delivery(args.output_dir, args.template)
    print(f"valid={report['valid']}")
    print(f"output_dir={args.output_dir}")
    return 0 if report["valid"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
