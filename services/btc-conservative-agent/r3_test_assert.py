"""Shared BLOCK assertion for R3 rewrites (RK/HM #873).

A consumer of market_segment rows must surface INVALID_R3_MARKET_SEGMENT_INPUT and must
not emit any positive economic / qualification outcome derived from those rows.
"""
from __future__ import annotations

import json

R3 = "INVALID_R3_MARKET_SEGMENT_INPUT"
_TRUE_FORBIDDEN = {"profitability_supported", "ranking_eligible", "live_qualification",
                   "live_arming_authorized", "profitability_calculated"}
_VALUE_FORBIDDEN = {"net_pnl_usd", "gross_pnl_usd", "exit_price", "entry_vwap"}
_POSITIVE_OUTCOMES = {"FULL_FILL", "PARTIAL_FILL", "REALIZED_COST_COMPLETE", "QUALIFIED"}


def _walk(obj, path="$"):
    if isinstance(obj, dict):
        for key, value in obj.items():
            yield path, key, value
            yield from _walk(value, f"{path}.{key}")
    elif isinstance(obj, (list, tuple)):
        for index, value in enumerate(obj):
            yield from _walk(value, f"{path}[{index}]")


def assert_r3_block(obj, what="output"):
    text = json.dumps(obj, default=str, sort_keys=True)
    assert R3 in text, f"{what}: no {R3} evidence"
    for path, key, value in _walk(obj):
        assert not (key in _TRUE_FORBIDDEN and value is True), f"{what}: {path}.{key} is True under R3"
        assert not (key in _VALUE_FORBIDDEN and value is not None), f"{what}: {path}.{key}={value!r} under R3"
        assert not (isinstance(value, str) and value in _POSITIVE_OUTCOMES and key in
                    ("outcome_state", "entry_outcome", "outcome", "status", "classification",
                     "qualification_status", "cost_status")), f"{what}: {path}.{key}={value} under R3"
