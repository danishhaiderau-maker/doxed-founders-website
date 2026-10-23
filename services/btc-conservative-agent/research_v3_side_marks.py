"""RETIRED under R3 (RK 23 Oct 2026): side-correct 1 s marks from ``market_microstructure_1s``.

This module used to build exit marks for the protection replay from the executable side of the 1 s
BBO tape (``market_microstructure_1s.jsonl``) and from the event segment's 1 s rows.  R3 forbids
pricing or triggering on ``ticker``, ``book_tape_1s`` or ``market_microstructure_1s``, and R4 allows
one shared pricer (``paper_fill``) and one trigger helper (``quote_trigger``) only.  The protection
replay now walks ``bfx_book_10l`` rows + ``bfx_trades`` prints (research_v3_book_walk, #847), so the
1 s side-mark builders are removed.  Any import of a removed builder raises ``R3Retired`` so a stale
caller fails loudly instead of silently pricing on the tape.

``PATH_HORIZON_SEC`` is kept (re-exported from research_v3_book_walk) for old importers.
"""
from __future__ import annotations

from research_v3_book_walk import PATH_HORIZON_SEC

RETIRED = frozenset({
    "TAPE_FILE", "TAPE_SYMBOL", "BASIS_SEGMENT", "BASIS_TAPE", "BASIS_SEGMENT_AND_TAPE",
    "BASIS_SEGMENT_LAST_PRICE", "BASIS_1M_ADVERSE_FIRST", "BASIS_NONE", "SIDE_CORRECT_BASES",
    "FALLBACK_BASES", "SIDE_CONVENTION", "side_quote", "tape_paths", "SideMarkTape",
    "load_side_mark_tape", "event_side_marks", "mark_source_summary",
})
REASON = ("R3: research_v3_side_marks priced exits on market_microstructure_1s; retired. "
          "Use research_v3_book_walk (paper_fill on bfx_book_10l + trade-print stops).")


class R3Retired(ImportError):
    """A removed 1 s side-mark builder was requested."""


def __getattr__(name: str):
    if name in RETIRED:
        raise R3Retired(f"{name}: {REASON}")
    raise AttributeError(name)


__all__ = ["PATH_HORIZON_SEC", "R3Retired", "RETIRED", "REASON"]
