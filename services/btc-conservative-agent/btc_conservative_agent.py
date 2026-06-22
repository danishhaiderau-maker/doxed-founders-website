#!/usr/bin/env python3
"""
Home or Railway entry — full research bot + Bitfinex relay hooks for doxxedcrypto.digital.

Prefer running bot.py at home with home-bot.env (see docs/HOME_BOT_MIGRATION.md).
"""
from __future__ import annotations

import os
import sys

os.environ.setdefault("SHOWCASE_AGENT", "1")
os.environ.setdefault("HOME_BOT_LOCAL", "1")
os.environ.setdefault("HOME_RESEARCH_FULL", "1")
os.environ.setdefault(
    "SHOWCASE_RELAY_WEBHOOK_URL",
    "https://doxxedcrypto.digital/api/trading-agents/conservative-btc/showcase-relay-event",
)

# Ensure service directory is on path for bot + showcase_ui imports.
_SERVICE_DIR = os.path.dirname(os.path.abspath(__file__))
if _SERVICE_DIR not in sys.path:
    sys.path.insert(0, _SERVICE_DIR)

import bot as signal_engine  # noqa: E402 — synced research engine (signal backend)
from showcase_ui import register_showcase_ui  # noqa: E402

register_showcase_ui(signal_engine.app, bot_module=signal_engine, block_warehouse=False)


def main() -> None:
    signal_engine.main()


if __name__ == "__main__":
    main()
