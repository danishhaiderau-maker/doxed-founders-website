#!/usr/bin/env python3
"""Laptop smoke step to run BEFORE pinning self-aware-live to a deployed revision (HM 19 Oct, after master 4bdac452
crashed the engine loop with KeyError: 'bitfinex').

Run from the candidate checkout:  python scripts/self_aware_smoke.py [--cycle]
 1. import self_aware.engine (+ server) at this revision;
 2. every engine job has a CADENCE_SEC entry and Engine.due() works for each (the exact failure that crashed 4bdac452);
 3. --cycle: build the engine against an isolated temp HOME (no alarms, no repairs) and run one job cycle.
Exit 0 only when all steps pass. Nothing is written outside the temp directory.
"""
from __future__ import annotations

import argparse
import dataclasses
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def smoke(cycle: bool = False) -> list[str]:
    problems: list[str] = []
    try:
        from self_aware import config, engine  # noqa: PLC0415
        from self_aware import server  # noqa: F401,PLC0415
    except Exception as exc:  # noqa: BLE001
        return [f"IMPORT_FAILED {type(exc).__name__}: {exc}"]
    with tempfile.TemporaryDirectory() as tmp:
        paths = dataclasses.replace(config.Paths(), home=Path(tmp) / "home")
        try:
            eng = engine.Engine(paths, probe_local=False, repair_enabled=False, emit_alarms=False)
        except Exception as exc:  # noqa: BLE001
            return [f"ENGINE_INIT_FAILED {type(exc).__name__}: {exc}"]
        now = time.time()
        for name in eng.jobs:
            if name not in config.CADENCE_SEC:
                problems.append(f"NO_CADENCE {name}")
                continue
            try:
                eng.due(name, now)
            except Exception as exc:  # noqa: BLE001
                problems.append(f"DUE_FAILED {name} {type(exc).__name__}: {exc}")
        if cycle and not problems:
            for name in ("diagnose", "uptime"):
                if name in eng.jobs:
                    res = eng.run_job(name)
                    if isinstance(res, dict) and str(res.get("error", "")).startswith(("KeyError", "NameError", "AttributeError", "ImportError")):
                        problems.append(f"CYCLE_FAILED {name} {res['error']}")
    return problems


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cycle", action="store_true")
    problems = smoke(ap.parse_args(argv).cycle)
    for p in problems:
        print("SMOKE FAIL:", p)
    print("SMOKE OK" if not problems else f"SMOKE FAILED ({len(problems)})")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
