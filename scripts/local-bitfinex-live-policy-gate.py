#!/usr/bin/env python3
"""CLI wrapper: laptop Bitfinex/live policy gate (HM AMBER; replaces bitfinex-production-gate)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AGENT = ROOT / "services" / "btc-conservative-agent"
if str(AGENT) not in sys.path:
    sys.path.insert(0, str(AGENT))

from local_bitfinex_live_policy_gate import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
