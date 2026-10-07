"""Who closed a paper trade, and how the Trades table should explain it.

``exit_reason`` stays the analyzer-facing code (``ADMIN_MANUAL_CLOSE`` is
excluded from strategy statistics everywhere).  ``close_origin`` is a separate
field that says whether that forced close came from a guarded deploy's
maintenance boundary or from an operator, so the dashboard can label it
truthfully without changing any analyzer cohort.
"""
from __future__ import annotations

from paper_pnl_canon import FORCED_EXIT_REASONS  # single source of truth

TRADE_DISPLAY_SCHEMA = "trade_display_v1"

CLOSE_ORIGIN_DEPLOY_MAINTENANCE = "DEPLOY_MAINTENANCE"
CLOSE_ORIGIN_OPERATOR = "OPERATOR"
CLOSE_ORIGIN_SAFETY = "SAFETY"
CLOSE_ORIGINS = frozenset({
    CLOSE_ORIGIN_DEPLOY_MAINTENANCE, CLOSE_ORIGIN_OPERATOR, CLOSE_ORIGIN_SAFETY,
})

_RUNS = "https://github.com/danishhaiderau-maker/doxed-founders-website/actions/runs/"

# Forced closes written before close_origin existed. Each id was flattened by
# the maintenance boundary of the cited guarded deploy run (boundary log:
# "Durable maintenance boundary is flat after round 2"). Never extend this
# map: rows closed after close_origin shipped carry the field themselves.
LEGACY_DEPLOY_FLATTEN_ATTESTATIONS = {
    "ftf-d545fd0b6724": _RUNS + "36910056265",
    "fal-57ac56b2f659": _RUNS + "36936337703",
    "far-6fbadf45dece": _RUNS + "36936337703",
    "ftf-2a96d4cb1209": _RUNS + "36936337703",
    "fal-ab974b7290cb": _RUNS + "36940879163",
    "far-b1930acfdfe5": _RUNS + "36940879163",
    "ftf-a5f01a80d49d": _RUNS + "36940879163",
    "ftf-3340d2b3c57f": _RUNS + "36949767912",
    "ftf-928c9bcd7bd5": _RUNS + "36974300808",
    "ftl-94b81db91b0a": _RUNS + "36974300808",
    "ftf-a721a6929cfb": _RUNS + "36979066944",
    "ftl-93598246300f": _RUNS + "36979066944",
    "ftl-d3f2eb57a2f6": _RUNS + "36979066944",
}

_PAPER_FILL_LABELS = {
    "FILLED_CLOSED": "Filled, then closed",
    "FILLED": "Filled",
    "NOT_FILLED": "Not filled",
}

_FORCED_CLOSE_LABELS = {
    CLOSE_ORIGIN_DEPLOY_MAINTENANCE: ("DEPLOY_BOUNDARY", "Deploy boundary (paper force-flat)", "deploy force-flat"),
    CLOSE_ORIGIN_OPERATOR: ("MANUAL", "Manual close (operator)", "manual close"),
    CLOSE_ORIGIN_SAFETY: ("SAFETY_PAUSE", "Safety-pause close", "safety-pause close"),
}


def close_origin_for_pause_owner(pause_owner) -> str:
    """A forced close issued while the deploy owns the pause is a deploy flatten."""
    owner = str(pause_owner or "").strip().upper()
    if owner == CLOSE_ORIGIN_DEPLOY_MAINTENANCE:
        return CLOSE_ORIGIN_DEPLOY_MAINTENANCE
    if owner == CLOSE_ORIGIN_SAFETY:
        return CLOSE_ORIGIN_SAFETY
    return CLOSE_ORIGIN_OPERATOR


def exit_reason_code(row) -> str:
    row = row if isinstance(row, dict) else {}
    return str(row.get("exit_reason") or row.get("outcome_exit_reason") or "").strip().upper()


def is_forced_exit(row) -> bool:
    return exit_reason_code(row) in FORCED_EXIT_REASONS


def resolve_close_origin(row) -> str | None:
    """Recorded origin, else the legacy attestation, else None (unknown)."""
    row = row if isinstance(row, dict) else {}
    if not is_forced_exit(row):
        return None
    recorded = str(row.get("close_origin") or "").strip().upper()
    if recorded in CLOSE_ORIGINS:
        return recorded
    if str(row.get("trade_id") or "").strip() in LEGACY_DEPLOY_FLATTEN_ATTESTATIONS:
        return CLOSE_ORIGIN_DEPLOY_MAINTENANCE
    return None


def _close_block(row) -> dict:
    reason = exit_reason_code(row)
    if reason not in FORCED_EXIT_REASONS:
        return {"code": "STRATEGY_EXIT", "label": None, "origin": None}
    if reason == "ADMIN_FORCE_FLAT":
        return {"code": "ADMIN_FORCE_FLAT", "label": "Admin force-flat", "origin": CLOSE_ORIGIN_OPERATOR}
    if reason == "CIRCUIT_BREAKER_ADMIN_MANUAL":
        return {"code": "SAFETY_FLAT", "label": "Safety flat", "origin": CLOSE_ORIGIN_SAFETY}
    origin = resolve_close_origin(row)
    if origin in _FORCED_CLOSE_LABELS:
        code, label, _ = _FORCED_CLOSE_LABELS[origin]
        block = {"code": code, "label": label, "origin": origin}
        attestation = LEGACY_DEPLOY_FLATTEN_ATTESTATIONS.get(str(row.get("trade_id") or "").strip())
        if attestation and not str(row.get("close_origin") or "").strip():
            block["evidence_url"] = attestation
        return block
    return {"code": "FORCED_UNATTRIBUTED", "label": "Manual or deploy close (origin not recorded)", "origin": None}


def _analyzer_block(row, close: dict, relationship: dict) -> dict:
    code = close.get("code")
    if code != "STRATEGY_EXIT":
        noun = {
            "ADMIN_FORCE_FLAT": "admin force-flat",
            "SAFETY_FLAT": "safety flat",
            "FORCED_UNATTRIBUTED": "forced exit",
        }.get(code) or _FORCED_CLOSE_LABELS[close["origin"]][2]
        return {"included": False, "code": "EXCLUDED_FORCED_EXIT", "label": "Excluded: " + noun}
    lane = str(row.get("research_lane") or "").upper()
    schema = str(row.get("pnl_accounting_schema") or "")
    if lane.startswith("FAMILY_") and schema and schema != "terminal_single_count_v1":
        return {"included": False, "code": "EXCLUDED_PRE_FIX_ACCOUNTING", "label": "Excluded: pre-fix PnL accounting"}
    if relationship.get("excluded_from_showcase_strategy_stats"):
        return {"included": False, "code": "EXCLUDED_COPY_COHORT", "label": "Excluded: copy-fidelity cohort"}
    return {"included": True, "code": "INCLUDED", "label": "Included"}


def trade_display(row, truth=None) -> dict:
    """Public-safe, self-explanatory labels for one closed paper trade row."""
    row = row if isinstance(row, dict) else {}
    truth = truth if isinstance(truth, dict) else {}
    show = truth.get("showcase_simulated") if isinstance(truth.get("showcase_simulated"), dict) else {}
    bitfinex = truth.get("bitfinex_authenticated") if isinstance(truth.get("bitfinex_authenticated"), dict) else {}
    relationship = truth.get("relationship") if isinstance(truth.get("relationship"), dict) else {}
    paper_code = str(show.get("status") or ("FILLED" if show.get("executed") else "NOT_FILLED")).upper()
    paper_fill = {
        "code": paper_code,
        "label": _PAPER_FILL_LABELS.get(paper_code, paper_code.replace("_", " ").capitalize()),
    }
    if bitfinex.get("authenticated"):
        classification = str(bitfinex.get("classification") or "").strip()
        bitfinex_copy = {
            "code": "COPIED",
            "label": "Copied to Bitfinex" + (f" ({classification})" if classification else ""),
        }
    else:
        bitfinex_copy = {"code": "NOT_COPIED", "label": "Paper only, not copied to Bitfinex"}
    bitfinex_copy["relationship"] = relationship.get("divergence_cohort")
    close = _close_block(row)
    return {
        "schema": TRADE_DISPLAY_SCHEMA,
        "paper_fill": paper_fill,
        "bitfinex_copy": bitfinex_copy,
        "close": close,
        "analyzer": _analyzer_block(row, close, relationship),
    }


def forced_exit_origin_counts(rows) -> dict:
    """Count forced exits by origin for analyzer receipts."""
    counts = {CLOSE_ORIGIN_DEPLOY_MAINTENANCE: 0, CLOSE_ORIGIN_OPERATOR: 0, CLOSE_ORIGIN_SAFETY: 0, "UNRECORDED": 0}
    for row in rows or ():
        if not is_forced_exit(row):
            continue
        origin = _close_block(row).get("origin")
        counts[origin if origin in counts else "UNRECORDED"] += 1
    return counts