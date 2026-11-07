"""Fixture-only synthetic CPU lifecycle reference.

Stage A is an approved design dependency. No approved Stage A implementation
is pinned. Interface compatibility stays BLOCKED. Live trading is OFF.
This package is not an R57 sweep, a profitability result, or research completion.
"""

from __future__ import annotations

COMPATIBILITY = "BLOCKED"
FIXTURE_STUB = "FIXTURE-ONLY / COMPATIBILITY NOT ESTABLISHED"
LIVE = "OFF"
STAGE_A_DESIGN_CARD_SHA256 = (
    "ecb42d094510f7dd507b4ebc5fb2797a2614951614745d1fcfbaa79e44bfb122"
)
STAGE_A_IMPLEMENTATION_PIN = None
AUTHORITATIVE_BASE = "40087a65f2157f8c6701ea3670adc5c29a28c87d"
PRICER_ID = "research.event_replay_v1.pricer.v1"

__all__ = [
    "AUTHORITATIVE_BASE",
    "COMPATIBILITY",
    "FIXTURE_STUB",
    "LIVE",
    "PRICER_ID",
    "STAGE_A_DESIGN_CARD_SHA256",
    "STAGE_A_IMPLEMENTATION_PIN",
]
