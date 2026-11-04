#!/usr/bin/env python3
"""CI: validate every strategies/<id>.yaml against the JSON schema (R47).

Exit 0 when the directory is empty or every package passes the paper-bot
loader hard-refuses (schema + paper_only + series_stamp + no credentials +
evidence status). Exit 1 on the first refuse.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AGENT = ROOT / "services" / "btc-conservative-agent"
sys.path.insert(0, str(AGENT))

from strategy_package_loader import (  # noqa: E402
    StrategyPackageRefuse,
    iter_strategy_yaml_paths,
    load_package_file,
    load_schema,
)


def main() -> int:
    schema = load_schema()
    paths = iter_strategy_yaml_paths()
    if not paths:
        print("OK  strategy packages: none registered (empty strategies/*.yaml)")
        return 0
    for path in paths:
        try:
            pkg = load_package_file(path, schema=schema)
        except StrategyPackageRefuse as exc:
            print(f"FAIL  {path}: {exc}", file=sys.stderr)
            return 1
        print(
            f"OK  {path.name}: id={pkg.get('id')} "
            f"evidence={((pkg.get('evidence') or {}).get('status'))} "
            f"activated={pkg.get('_activated')}"
        )
    print(f"OK  validated {len(paths)} strategy package(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
