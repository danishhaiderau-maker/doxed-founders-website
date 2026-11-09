"""Process entrypoint.

No arguments refuses to start. Capture does not read the environment, does not
take an API key, and does not arm live trading.

Run with `python -B -m public_market_collector` or set PYTHONDONTWRITEBYTECODE=1
so Python does not write __pycache__ into the checkout.

    python -B -m public_market_collector capture --output DIR --allow-root ROOT

DIR must not already exist. Pass --resume only to append to a collector
directory that this process created earlier.
"""

from __future__ import annotations

import sys
from pathlib import Path

REFUSAL = (
    "REFUSED real start: BOARD_FREEZE_UNSET OPS_NOT_APPROVED "
    "no capture no socket no autostart"
)

_REPO = Path(__file__).resolve().parents[3]


def _parse_capture_args(args: list[str]) -> tuple[str, str, bool] | None:
    output = None
    allow_root = None
    resume = False
    index = 0
    while index < len(args):
        token = args[index]
        if token == "--output" and index + 1 < len(args):
            output = args[index + 1]
            index += 2
            continue
        if token == "--allow-root" and index + 1 < len(args):
            allow_root = args[index + 1]
            index += 2
            continue
        if token == "--resume":
            resume = True
            index += 1
            continue
        if token == "--no-resume":
            resume = False
            index += 1
            continue
        return None
    if not output or not allow_root:
        return None
    return output, allow_root, resume


def main(argv: list[str] | None = None, runner=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] != ["capture"]:
        sys.stderr.write(REFUSAL + "\n")
        return 78
    parsed = _parse_capture_args(args[1:])
    if parsed is None:
        sys.stderr.write("REFUSED capture: --output and --allow-root are required\n")
        return 78
    output, allow_root, resume = parsed
    from .public_run import execute_capture

    return execute_capture(output, resume=resume, repo_root=_REPO, allow_root=allow_root, runner=runner)


if __name__ == "__main__":
    raise SystemExit(main())
