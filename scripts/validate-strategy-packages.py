#!/usr/bin/env python3
"""CI: validate every strategies/<id>.yaml against the JSON schema (R47).

Exit 0 when the directory is empty (schema is skipped) or every package passes
the paper-bot loader hard-refuses. Exit 1 on the first refuse or missing deps
when packages are present.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AGENT = ROOT / "services" / "btc-conservative-agent"
sys.path.insert(0, str(AGENT))

from strategy_package_loader import (  # noqa: E402
    StrategyPackageRefuse,
    assert_numeric_bars_gate,
    deps_available,
    iter_strategy_yaml_paths,
    load_package_file,
    load_schema,
    missing_deps,
)


def main() -> int:
    paths = iter_strategy_yaml_paths()
    if not paths:
        # Empty dir: skip schema load entirely (HM HOLD item 3).
        print("OK  strategy packages: none registered (empty strategies/*.yaml; schema skipped)")
        return 0
    if not deps_available():
        print(
            f"FAIL  packages present but deps missing {missing_deps()} — "
            "pip install -r services/btc-conservative-agent/requirements.txt",
            file=sys.stderr,
        )
        return 1
    try:
        assert_numeric_bars_gate()
    except StrategyPackageRefuse as exc:
        print(f"FAIL  {exc}", file=sys.stderr)
        return 1
    schema = load_schema()
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
