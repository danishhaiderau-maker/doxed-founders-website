"""Fail-closed normalization of authoritative measured execution costs."""
from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Mapping, Sequence

SCHEMA = "measured_execution_cost_receipt_v1"

def _finite(value: Any) -> float | None:
    if isinstance(value, bool): return None
    try: number = float(value)
    except (TypeError, ValueError, OverflowError): return None
    return number if math.isfinite(number) else None

def _first(source: Mapping[str, Any], *names: str) -> float | None:
    for name in names:
        value = _finite(source.get(name))
        if value is not None: return value
    return None

def build_measured_execution_cost_receipt(
    source: Mapping[str, Any], *, source_receipt_ids: Sequence[Any],
    expected_net_pnl_usd: Any = None, tolerance_usd: float = 1e-8,
) -> dict[str, Any]:
    """Map observed lifecycle/evaluator fields without defaulting absence to zero."""
    nested = source.get("canonical_economics")
    economics = nested if isinstance(nested, Mapping) else source
    entry_fee = _first(source, "entry_fee_usd", "entry_fees_usd")
    exit_fee = _first(source, "exit_fee_usd", "exit_fees_usd")
    trading = _first(economics, "trading_fees_usd", "fees_usd")
    funding = _first(economics, "funding_fees_usd", "funding_usd")
    entry_slip = _first(source, "entry_slippage_cost_usd", "entry_slippage_usd")
    exit_slip = _first(source, "exit_slippage_cost_usd", "exit_slippage_usd")
    latency = _first(economics, "latency_cost_usd")
    gross = _first(economics, "gross_pnl_usd")
    net = _first(economics, "net_pnl_usd")
    ids = sorted({str(value).strip() for value in source_receipt_ids if str(value).strip()})
    fields = {"entry_fee_usd": entry_fee, "exit_fee_usd": exit_fee,
              "funding_usd": funding, "entry_slippage_usd": entry_slip,
              "exit_slippage_usd": exit_slip, "latency_cost_usd": latency,
              "gross_pnl_usd": gross, "net_pnl_usd": net}
    blockers = []
    for name, value in fields.items():
        if value is None: blockers.append(f"UNKNOWN_{name.upper()}_MISSING")
        elif name not in {"gross_pnl_usd", "net_pnl_usd"} and value < 0:
            blockers.append(f"UNKNOWN_{name.upper()}_NEGATIVE")
    if not ids: blockers.append("UNKNOWN_SOURCE_RECEIPT_IDS_MISSING")
    if trading is None: blockers.append("UNKNOWN_TRADING_FEES_USD_MISSING")
    elif entry_fee is not None and exit_fee is not None and abs(entry_fee + exit_fee - trading) > tolerance_usd:
        blockers.append("UNKNOWN_TRADING_FEE_RECONCILIATION_MISMATCH")
    if None not in (gross, trading, funding, net) and abs(gross - trading - funding - net) > tolerance_usd:
        blockers.append("UNKNOWN_NET_PNL_RECONCILIATION_MISMATCH")
    expected = _finite(expected_net_pnl_usd)
    if expected is not None and net is not None and abs(expected - net) > tolerance_usd:
        blockers.append("UNKNOWN_CANDIDATE_NET_PNL_MISMATCH")
    blockers = sorted(set(blockers))
    body = {"schema": SCHEMA, "status": "MEASURED" if not blockers else "UNKNOWN",
            **fields, "trading_fees_usd": trading,
            "slippage_usd": entry_slip + exit_slip if entry_slip is not None and exit_slip is not None else None,
            "source_receipt_ids": ids,
            "gross_pnl_basis": "ACTUAL_EXECUTION_PRICES_INCLUDES_PRICE_IMPACT",
            "net_pnl_reconciliation_basis": "GROSS_MINUS_TRADING_FEES_MINUS_FUNDING_FEES",
            "explicit_measured_zero_fields": sorted(k for k, v in fields.items() if v == 0),
            "blockers": blockers}
    material = json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False)
    body["receipt_sha256"] = hashlib.sha256(material.encode()).hexdigest()
    return body
