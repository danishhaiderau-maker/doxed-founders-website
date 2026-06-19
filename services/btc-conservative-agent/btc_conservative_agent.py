#!/usr/bin/env python3
"""
Doxxedcrypto.digital — Lightweight execution mirror entry point.

Railway MUST start this file, not bot.py directly.
bot.py remains the synced signal engine backend until full signal_engine/ extraction completes.
"""
from __future__ import annotations

import os
import sys

# Execution mirror flags — set before importing signal engine.
os.environ.setdefault("SHOWCASE_EXECUTION_ONLY", "1")
os.environ.setdefault("SHOWCASE_AGENT", "1")

# Ensure service directory is on path for bot + showcase_ui imports.
_SERVICE_DIR = os.path.dirname(os.path.abspath(__file__))
if _SERVICE_DIR not in sys.path:
    sys.path.insert(0, _SERVICE_DIR)

import bot as signal_engine  # noqa: E402 — synced research engine (signal backend)
from showcase_ui import register_showcase_ui  # noqa: E402

register_showcase_ui(signal_engine.app, bot_module=signal_engine)


def main() -> None:
    signal_engine.main()


if __name__ == "__main__":
    main()
