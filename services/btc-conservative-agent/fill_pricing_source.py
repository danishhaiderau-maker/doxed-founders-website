"""Book-priced paper fill provenance (Phase 5, deliverable 5).

Paper fills are priced from the real Bitfinex order book, never from the 1-second
tape (R3: no pricing or triggering on ticker, book_tape_1s or
market_microstructure_1s; HM #850 R2 N2, 24 Oct 2026 AEDT):

* taker fills: the shared ``paper_fill`` pricer (R4) walks the Bitfinex 10-level
  book snapshot (bfx_book_10l) at signal + delay for the full size; the WS book
  top is the only fallback; no book -> no fill;
* resting limits: fill only on live Bitfinex trade prints at or through the limit
  (aggressor side, received after placement; ``order_model`` / ``quote_trigger``);
* exits: book-only marks / triggers (``quote_trigger``), priced by ``paper_fill``.

``REALISTIC_V1`` stays the headline fill-model label.  ``research/fill_model.py``'s
1 s-tape row pricers are research-only and retired (``research/r3_quarantine``).

This module is a thin, dependency-light provenance facade: it exposes the pricing
source and parameters as a queryable dict for the API and for the health monitor.
Importing it must never import the heavy engine or touch the exchange.
"""
from __future__ import annotations

from typing import Any, Mapping

SCHEMA = "paper_fill_pricing_source_v1"

# Keep in lock-step with paper_fill.py (taker book walk) and order_model / quote_trigger (resting limits,
# triggers). These are the headline facts.
HEADLINE_MODEL = "REALISTIC_V1"
PRICING_SOURCE = "BITFINEX_ORDER_BOOK_10L_WALK_PLUS_LIVE_TRADE_PRINTS"
EVIDENCE = ("bfx_book_10l (10-level book, 1 s snapshots; WS book top fallback) + live Bitfinex trade prints; "
            "never ticker / book_tape_1s / market_microstructure_1s (R3)")
DEPTH_MODEL = "walks the 10 visible book levels for the full size; insufficient depth -> partial or no fill"
SLIPPAGE_MODEL = "taker VWAP against one side of the book; resting limits book only at the declared limit"
QUEUE_MODEL = ("book size at the limit at placement is ahead of us; aggressor prints at the limit deplete it, "
               "prints through the limit fill us")
EXIT_MODEL = ("book-only exit triggers (quote_trigger); marketable exits are priced by paper_fill's book walk; "
              "resting targets book exactly at the target level")


def fill_pricing_provenance() -> dict:
    """Queryable provenance for the book-priced paper fill model (read-only)."""
    return {
        "schema": SCHEMA,
        "headline_model": HEADLINE_MODEL,
        "pricing_source": PRICING_SOURCE,
        "evidence": EVIDENCE,
        "depth_model": DEPTH_MODEL,
        "slippage_model": SLIPPAGE_MODEL,
        "queue_estimate": QUEUE_MODEL,
        "exit_booking": EXIT_MODEL,
        "engine_module": "paper_fill",
        "note": (
            "Paper fills are priced from the real Bitfinex order book (10-level walk via the shared "
            "paper_fill pricer) and live trade prints; no paper fill is priced from the 1 s tape. "
            "REALISTIC_V1 remains the headline label."
        ),
    }


def assert_realistic_v1_compatible(candidate_model: Mapping[str, Any] | None = None) -> dict:
    """Guard used by tests: any claimed fill model must keep REALISTIC_V1 as headline."""
    model = candidate_model or fill_pricing_provenance()
    compatible = model.get("headline_model") == HEADLINE_MODEL
    return {
        "schema": SCHEMA,
        "compatible": compatible,
        "headline_model": model.get("headline_model"),
        "reason": ("REALISTIC_V1 preserved" if compatible
                   else f"expected {HEADLINE_MODEL}, got {model.get('headline_model')}"),
    }
