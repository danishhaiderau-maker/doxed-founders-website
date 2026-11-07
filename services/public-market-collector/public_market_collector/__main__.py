"""Process entrypoint. Real capture is refused in this build.

OPS is not approved. The board has not frozen an instrument, depth, or any
operational limit. This module does not read the environment, does not open a
socket, and does not create an output root.
"""

from __future__ import annotations

import sys

REFUSAL = (
    "REFUSED real start: BOARD_FREEZE_UNSET OPS_NOT_APPROVED "
    "no capture no socket no autostart"
)


def main(argv: list[str] | None = None) -> int:
    del argv
    sys.stderr.write(REFUSAL + "\n")
    return 78


if __name__ == "__main__":
    raise SystemExit(main())
