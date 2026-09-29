"""Deterministic, fail-closed quantity semantics for research execution receipts.

The venue constraints consumed here are evidence, not configuration defaults.
They must carry a reproducible payload hash so an analyzer cannot silently use
constraints from another symbol or revision.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_DOWN
import hashlib
import json
from typing import Any, Mapping


CONSTRAINT_SCHEMA = "signed_quantity_constraints_v1"
DECISION_SCHEMA = "quantity_execution_decision_v1"
INTEGRITY_BINDING = "SHA256_CANONICAL_PAYLOAD_NOT_CRYPTOGRAPHIC_SIGNATURE"


def _decimal(value: Any) -> Decimal | None:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return result if result.is_finite() and result > 0 else None


def _payload(raw: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema": CONSTRAINT_SCHEMA,
        "symbol": str(raw.get("symbol") or "").strip(),
        "quantity_step": str(raw.get("quantity_step") or "").strip(),
        "quantity_precision": raw.get("quantity_precision"),
        "min_lot": str(raw.get("min_lot") or "").strip(),
        "min_notional": str(raw.get("min_notional") or "").strip(),
        "captured_at": str(raw.get("captured_at") or "").strip(),
        "source_revision": str(raw.get("source_revision") or "").strip(),
        "source": str(raw.get("source") or "").strip(),
        "integrity_binding": str(raw.get("integrity_binding") or "").strip(),
    }


def _sha256(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_signed_quantity_constraints(
    *, symbol: str, quantity_step: Any, quantity_precision: int,
    min_lot: Any, min_notional: Any, captured_at: str,
    source_revision: str, source: str,
) -> dict[str, Any]:
    """Build an integrity-bound constraint receipt from explicit venue data."""
    raw = {
        "schema": CONSTRAINT_SCHEMA,
        "symbol": symbol,
        "quantity_step": str(quantity_step),
        "quantity_precision": quantity_precision,
        "min_lot": str(min_lot),
        "min_notional": str(min_notional),
        "captured_at": captured_at,
        "source_revision": source_revision,
        "source": source,
        "integrity_binding": INTEGRITY_BINDING,
    }
    payload = _payload(raw)
    raw["payload_sha256"] = _sha256(payload)
    return raw


def validate_signed_quantity_constraints(
    raw: Mapping[str, Any] | None, *, symbol: str | None = None,
) -> tuple[dict[str, Any] | None, list[str]]:
    if not isinstance(raw, Mapping):
        return None, ["SIGNED_QUANTITY_CONSTRAINTS_MISSING"]
    payload = _payload(raw)
    reasons: list[str] = []
    if raw.get("schema") != CONSTRAINT_SCHEMA:
        reasons.append("QUANTITY_CONSTRAINT_SCHEMA_INVALID")
    if not payload["symbol"]:
        reasons.append("QUANTITY_CONSTRAINT_SYMBOL_MISSING")
    elif symbol and payload["symbol"] != str(symbol).strip():
        reasons.append("QUANTITY_CONSTRAINT_SYMBOL_MISMATCH")
    step = _decimal(payload["quantity_step"])
    min_lot = _decimal(payload["min_lot"])
    min_notional = _decimal(payload["min_notional"])
    precision = payload["quantity_precision"]
    if step is None:
        reasons.append("QUANTITY_STEP_MISSING_OR_INVALID")
    if not isinstance(precision, int) or precision < 0:
        reasons.append("QUANTITY_PRECISION_MISSING_OR_INVALID")
    if min_lot is None:
        reasons.append("MIN_LOT_MISSING_OR_INVALID")
    if min_notional is None:
        reasons.append("MIN_NOTIONAL_MISSING_OR_INVALID")
    if not payload["captured_at"]:
        reasons.append("QUANTITY_CONSTRAINT_CAPTURE_TIME_MISSING")
    if not payload["source_revision"]:
        reasons.append("QUANTITY_CONSTRAINT_SOURCE_REVISION_MISSING")
    if not payload["source"]:
        reasons.append("QUANTITY_CONSTRAINT_SOURCE_MISSING")
    if payload["integrity_binding"] != INTEGRITY_BINDING:
        reasons.append("QUANTITY_CONSTRAINT_INTEGRITY_BINDING_INVALID")
    supplied_hash = str(raw.get("payload_sha256") or "").strip().lower()
    if len(supplied_hash) != 64 or supplied_hash != _sha256(payload):
        reasons.append("QUANTITY_CONSTRAINT_SIGNATURE_INVALID")
    if step is not None and isinstance(precision, int) and precision >= 0:
        # This contract uses decimal-place precision. Requiring consistency
        # prevents an analyzer from choosing whichever field yields more fills.
        if step != Decimal(1).scaleb(-precision):
            reasons.append("QUANTITY_STEP_PRECISION_MISMATCH")
    if reasons:
        return None, reasons
    return {
        **payload,
        "quantity_step": str(step),
        "min_lot": str(min_lot),
        "min_notional": str(min_notional),
        "payload_sha256": supplied_hash,
    }, []


def apply_quantity_constraints(
    *, requested_qty: Any, raw_partial_qty: Any, execution_price: Any,
    accumulated_qty: Any = 0, constraints: Mapping[str, Any] | None,
    symbol: str | None = None,
) -> dict[str, Any]:
    """Classify one partial increment and preserve every quantity boundary."""
    requested = _decimal(requested_qty)
    raw = _decimal(raw_partial_qty) or Decimal(0)
    price = _decimal(execution_price)
    try:
        accumulated = Decimal(str(accumulated_qty or 0))
    except (InvalidOperation, TypeError, ValueError):
        accumulated = Decimal(-1)
    base = {
        "schema": DECISION_SCHEMA,
        "requested_quantity": float(requested) if requested is not None else requested_qty,
        "raw_partial_quantity": float(raw),
        "rounded_executable_quantity": 0.0,
        "accumulated_quantity_before": float(accumulated) if accumulated >= 0 else accumulated_qty,
        "accumulated_quantity_after": float(accumulated) if accumulated >= 0 else accumulated_qty,
        "execution_price": float(price) if price is not None else execution_price,
        "executable_notional": None,
        "minimum_lot_decision": "UNKNOWN",
        "minimum_notional_decision": "UNKNOWN",
        "final_classification": "UNSUPPORTED",
        "accepted": False,
        "constraints": None,
        "reasons": [],
    }
    normalized, reasons = validate_signed_quantity_constraints(constraints, symbol=symbol)
    if requested is None:
        reasons.append("INVALID_REQUESTED_QTY")
    if accumulated < 0:
        reasons.append("INVALID_ACCUMULATED_QTY")
    if price is None:
        reasons.append("EXECUTION_PRICE_MISSING_OR_INVALID")
    if normalized is None or reasons:
        base["reasons"] = list(dict.fromkeys(reasons))
        return base
    base["constraints"] = normalized
    remaining = max(Decimal(0), requested - accumulated)
    raw = min(raw, remaining)
    step = Decimal(normalized["quantity_step"])
    rounded = (raw / step).to_integral_value(rounding=ROUND_DOWN) * step
    min_lot = Decimal(normalized["min_lot"])
    min_notional = Decimal(normalized["min_notional"])
    notional = rounded * price
    base["raw_partial_quantity"] = float(raw)
    base["rounded_executable_quantity"] = float(rounded)
    base["executable_notional"] = float(notional)
    base["minimum_lot_decision"] = "PASS" if rounded >= min_lot else "FAIL"
    base["minimum_notional_decision"] = "PASS" if notional >= min_notional else "FAIL"
    if raw <= 0:
        base["final_classification"] = "NO_FILL" if accumulated == 0 else "PARTIAL_FILL"
        base["reasons"] = ["NO_RAW_PARTIAL_QUANTITY"]
        return base
    if rounded <= 0:
        base["final_classification"] = "NO_FILL" if accumulated == 0 else "PARTIAL_FILL"
        base["reasons"] = ["RAW_PARTIAL_ROUNDED_TO_ZERO"]
        return base
    if rounded < min_lot:
        base["final_classification"] = "NO_FILL" if accumulated == 0 else "PARTIAL_FILL"
        base["reasons"] = ["MINIMUM_LOT_NOT_MET"]
        return base
    if notional < min_notional:
        base["final_classification"] = "NO_FILL" if accumulated == 0 else "PARTIAL_FILL"
        base["reasons"] = ["MINIMUM_NOTIONAL_NOT_MET"]
        return base
    after = min(requested, accumulated + rounded)
    base["accepted"] = True
    base["accumulated_quantity_after"] = float(after)
    base["final_classification"] = "FULL_FILL" if after >= requested else "PARTIAL_FILL"
    return base
