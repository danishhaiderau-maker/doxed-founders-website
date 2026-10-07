"""Book-priced paper fill provenance (Phase 5, deliverable 5).

Paper fills are priced from the real Bitfinex order book. The canonical engine
is ``research/fill_model.py`` (``REALISTIC_V1``), which books paper fills from
the 1-second market-microstructure tape: top-of-book (L1 BBO) price and size
plus 1-second trade aggregates. There is no L2 depth, so size that exceeds the
top-of-book size walks the book at one spread per extra top-size level (a
documented, conservative no-L2 proxy) and never at a better price.

This module is a thin, dependency-light provenance facade: it exposes the exact
pricing source and parameters as a queryable dict for the API and for the
health monitor, and it keeps ``REALISTIC_V1`` as the single headline model
(no second pricing model is introduced). Importing it must never import the
heavy engine or touch the exchange.
"""
from __future__ import annotations

from typing import Any, Mapping

SCHEMA = "paper_fill_pricing_source_v1"

# Keep in lock-step with research/fill_model.py. These are the headline facts;
# the engine remains the single implementation of REALISTIC_V1.
HEADLINE_MODEL = "REALISTIC_V1"
PRICING_SOURCE = "BITFINEX_ORDER_BOOK_TOP_OF_BOOK_VIA_MICROSTRUCTURE_TAPE_1S"
EVIDENCE = "market_microstructure_1s_v1 (L1 BBO + top-of-book size + 1 s trade aggregates; no L2)"
DEPTH_MODEL = ("top-of-book size at placement; excess size walks the book at one spread "
               "per extra top-size level (conservative no-L2 proxy)")
SLIPPAGE_MODEL = "taker VWAP against one side of the book; resting limits book only at the declared limit"
QUEUE_MODEL = ("top-of-book size at placement when the limit equals the touch; "
               "zero when the limit improves the touch")
EXIT_MODEL = ("exits book on the executable-side margin path; resting targets book exactly "
              "at the target level, marketable exits book at the worse of trigger and post-latency mark")


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
        "engine_module": "research.fill_model",
        "note": (
            "Paper fills are priced from the real Bitfinex order book (top-of-book "
            "with realistic depth/slippage). REALISTIC_V1 remains the single headline "
            "model; no second pricing model is introduced."
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
