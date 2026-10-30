"""Wipe-list basenames only (R22): fill + taker markout writers deleted.

``fill_markouts.jsonl`` writer removed (HM/RK ticker/L1-priced, 30 Oct 2026).
``taker_signal_counterfactuals.jsonl`` writer removed earlier (R22 PR-B).
Basenames stay so reset/wipe lists still find old copies. No MarkoutBook.
"""
from __future__ import annotations

FILL_FILE = "fill_markouts.jsonl"
TAKER_FILE = "taker_signal_counterfactuals.jsonl"
