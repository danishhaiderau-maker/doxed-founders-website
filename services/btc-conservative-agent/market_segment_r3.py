"""R3 gate for market_segment input (RK, 25 Oct 2026).

market_segment rows and ``v3/market_segments`` envelopes were copies of
market_microstructure_1s (L1 + ticker last), which R3 bans as evidence. The
writers are retired (R17, ``research_v3_bridge.MARKET_SEGMENT_WRITES_RETIRED``).
Every consumer must route its read through this module, which either drops the
input (returns no rows) or fails closed with the visible reason below. No caller
may read the ledger or envelopes directly; ``test_market_segment_r3_gate`` greps
for that.
"""
from __future__ import annotations

from typing import Any

_INPUT_ALLOWED = False
_REASON = "INVALID_R3_MARKET_SEGMENT_INPUT"
RULE = "R3"
# Every consumer touch of the gate is recorded so the R3 legacy-xfail plugin
# (r3_legacy_xfail.py) can prove a failure came from this gate and nothing else.
# A bounded counter (never a growing list): safe in the long-running bot process.
HIT_COUNT = 0
LAST_HIT = ""


def _hit(tag: str) -> None:
    global HIT_COUNT, LAST_HIT
    HIT_COUNT += 1
    LAST_HIT = tag


def __getattr__(name: str):
    if name == "INPUT_ALLOWED":
        _hit("INPUT_ALLOWED")
        return _INPUT_ALLOWED
    if name == "REASON":
        _hit("REASON")
        return _REASON
    raise AttributeError(name)
LEDGERS = ("v3/ledgers/market_segment.jsonl", "v3/recovery_ledgers/market_segment.jsonl")


class MarketSegmentInputInvalid(ValueError):
    """Raised where a consumer cannot drop the input and must fail closed."""

    def __init__(self, source: str):
        _hit("require_envelope:" + str(source))
        super().__init__(f"{_REASON}:{source}")
        self.source = source
        self.reason = _REASON


def invalid(source: str) -> dict[str, Any]:
    """Visible marker a consumer attaches to its output."""
    _hit("invalid:" + str(source))
    return {"status": "INVALID", "rule": RULE, "reason": _REASON, "source": str(source),
            "input_dropped": True}


def rows(source: str, loader=None) -> list:
    """Dropped input: market_segment rows are never loaded (loader is not called)."""
    _hit("rows:" + str(source))
    if _INPUT_ALLOWED and loader is not None:  # pragma: no cover - INPUT_ALLOWED is pinned False
        return list(loader())
    return []


def require_envelope(source: str):
    """Fail closed: a segment envelope may never be evidence under R3."""
    raise MarketSegmentInputInvalid(source)
