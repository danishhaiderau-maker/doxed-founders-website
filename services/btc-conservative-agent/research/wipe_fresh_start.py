#!/usr/bin/env python3
"""Retired compatibility entry point for the fail-closed V2 reset."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from reset_type_b_research_v2 import main


if __name__ == "__main__":
    raise SystemExit(main())
