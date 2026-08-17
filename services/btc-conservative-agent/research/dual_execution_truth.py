"""Separate Showcase simulated truth from Bitfinex authenticated truth."""
from __future__ import annotations
import copy


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
    relationship = {
        "divergence_classification": overlay.get("divergence_classification") or copy_fill.get("divergence_reason"),
        "shadow_label": overlay.get("label"),
        "excluded_from_showcase_strategy_stats": bool(overlay.get("excluded_from_showcase_strategy_stats")),
        "source_strategy_state_unchanged": bool(
            copy_fill.get("source_strategy_state_unchanged", overlay.get("source_strategy_state_unchanged", True))
        ),
    }
    return {
        "schema": "dual_execution_truth_v1",
        "showcase_simulated": copy.deepcopy(showcase),
        "bitfinex_authenticated": copy.deepcopy(bitfinex),
        "relationship": copy.deepcopy(relationship),
    }
