"""Separate Showcase simulated truth from Bitfinex authenticated truth."""
from __future__ import annotations
import copy


DIVERGENCE_COHORTS = (
    "BOTH_EXECUTED",
    "SHOWCASE_ONLY",
    "COPY_ONLY",
    "CORRELATED_CLUSTER_BLOCKED",
    "PAUSED_OR_RESEARCH_ONLY",
    "TRANSPORT_REJECTED",
    "NEVER_EXECUTABLE",
    "EXECUTABLE_COUNTERFACTUAL",
)


def _showcase_filled(row, showcase):
    paper = row.get("paper_trade") if isinstance(row.get("paper_trade"), dict) else {}
    if showcase.get("executed") and (row.get("filled") is True or showcase.get("fill_price")):
        return True
    if paper.get("net_pnl_usd") is not None or paper.get("fill_price") or paper.get("entry"):
        return True
    return str(row.get("source_fill_status") or "").upper() in {"FILLED", "PARTIAL"}


def _copy_filled(copy_fill, row, evidence):
    if copy_fill.get("classification"):
        cls = str(copy_fill.get("classification") or "").upper()
        if "FILL" in cls or cls in {"FILLED", "PARTIAL"}:
            return True
    status = str(row.get("copy_fill_status") or evidence.get("copy_fill_status") or "").upper()
    return status in {"PARTIAL", "FILLED"}


def divergence_cohort(row, showcase, bitfinex, evidence, copy_fill):
    block = str(row.get("block_reason") or row.get("copy_disposition") or evidence.get("copy_disposition") or "").upper()
    if "CLUSTER" in block or "CORRELATED" in block:
        return "CORRELATED_CLUSTER_BLOCKED"
    if "USER_RELAY_STOP" in block:
        return "TRANSPORT_REJECTED"
    if "TRANSPORT" in block or "400" in block or "EXPIRED_CYCLE" in block:
        return "TRANSPORT_REJECTED"
    if "PAUSED" in block or "RESEARCH" in block:
        return "PAUSED_OR_RESEARCH_ONLY"
    source_filled = _showcase_filled(row, showcase)
    copy_filled = _copy_filled(copy_fill, row, evidence)
    if source_filled and copy_filled:
        return "BOTH_EXECUTED"
    if source_filled and not copy_filled:
        return "SHOWCASE_ONLY"
    if copy_filled and not source_filled:
        return "COPY_ONLY"
    origin = (row.get("fill_origin") or {}).get("classification") if isinstance(row.get("fill_origin"), dict) else None
    if origin == "NEVER_EXECUTABLE":
        return "NEVER_EXECUTABLE"
    if origin:
        return "EXECUTABLE_COUNTERFACTUAL"
    return "SHOWCASE_ONLY" if source_filled else "NEVER_EXECUTABLE"


def split_execution_truth(row: dict) -> dict:
    """Return Showcase / Bitfinex / relationship without merging fills."""
    row = row if isinstance(row, dict) else {}
    copy_fill = row.get("copy_fill_observed") if isinstance(row.get("copy_fill_observed"), dict) else {}
    overlay = row.get("exchange_confirmed_shadow_overlay") if isinstance(row.get("exchange_confirmed_shadow_overlay"), dict) else {}
    evidence = row.get("bitfinex_evidence") if isinstance(row.get("bitfinex_evidence"), dict) else {}
    if not copy_fill:
        copy_fill = evidence.get("copy_fill_observed") if isinstance(evidence.get("copy_fill_observed"), dict) else {}
    if not overlay:
        overlay = evidence.get("exchange_confirmed_shadow_overlay") if isinstance(evidence.get("exchange_confirmed_shadow_overlay"), dict) else {}
    showcase = {
        "executed": bool(row.get("executed") is True),
        "status": row.get("status"),
        "fill_price": row.get("fill_price") or row.get("entry"),
        "exit_price": row.get("exit_price") or row.get("exit"),
        "pnl_usd": row.get("net_pnl_usd", row.get("pnl")),
    }
    bitfinex = {
        "authenticated": bool(copy_fill),
        "fill_ids": list(copy_fill.get("fill_ids") or evidence.get("fill_ids") or []),
        "order_ids": list(copy_fill.get("bitfinex_order_ids") or evidence.get("bitfinex_order_ids") or []),
        "classification": copy_fill.get("classification"),
        "actual_pnl_usd": row.get("actual_bitfinex_realized_pnl_usd"),
    }
    cohort = divergence_cohort(row, showcase, bitfinex, evidence, copy_fill)
    relationship = {
        "divergence_classification": overlay.get("divergence_classification") or copy_fill.get("divergence_reason") or cohort,
        "divergence_cohort": cohort,
        "copy_disposition": row.get("copy_disposition") or evidence.get("copy_disposition"),
        "source_epoch_id": row.get("source_epoch_id") or row.get("epoch_id"),
        "copy_session_id": row.get("copy_session_id") or row.get("showcase_session_epoch"),
        "shadow_label": overlay.get("label"),
        "excluded_from_showcase_strategy_stats": bool(
            overlay.get("excluded_from_showcase_strategy_stats") or cohort in {"COPY_ONLY", "CORRELATED_CLUSTER_BLOCKED", "TRANSPORT_REJECTED"}
        ),
        "source_strategy_state_unchanged": bool(
            copy_fill.get("source_strategy_state_unchanged", overlay.get("source_strategy_state_unchanged", True))
        ),
    }
    return {
        "schema": "dual_execution_truth_v2",
        "showcase_simulated": copy.deepcopy(showcase),
        "bitfinex_authenticated": copy.deepcopy(bitfinex),
        "relationship": copy.deepcopy(relationship),
    }
