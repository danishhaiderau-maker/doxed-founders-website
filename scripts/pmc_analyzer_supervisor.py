#!/usr/bin/env python3
"""DoxxedLaptopSupervisor: explicit start only; no installer/initializer/reset."""
import argparse
from pathlib import Path
from pmc_supervisor.policy import CONFIG, Hold


def main(argv=None):
    parser = argparse.ArgumentParser(description="DoxxedLaptopSupervisor; paper-only; Live OFF")
    parser.add_argument("--config", required=True, choices=[CONFIG])
    args = parser.parse_args(argv)
    try:
        from pmc_supervisor.runtime import run
        return run(Path(args.config))
    except Hold as exc:
        print("HOLD " + exc.reason)
        return exc.code
    except Exception:
        print("HOLD SUPERVISOR_FAILURE")
        return 78


if __name__ == "__main__":
    raise SystemExit(main())
