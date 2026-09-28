"""Content-addressed, fail-closed liquidation-buffer research receipts.

This module never estimates a liquidation price.  It only validates an
exchange-reported liquidation price and the evidence needed to compare that
price with the worst observed mark for a completed research episode.
"""
from __future__ import annotations

import hashlib
import math
from typing import Any, Iterable, Mapping

from research_v3_contract import canonical_json


SCHEMA = "liquidation_buffer_qualification_receipt_v1"
OBSERVATION_SCHEMA = "exchange_liquidation_buffer_observation_v1"
REQUIRED_NUMERIC_FIELDS = (
    "leverage",
    "margin_usd",
    "equity_usd",
    "entry_price",
    "worst_adverse_mark_price",
    "exchange_liquidation_price",
    "maintenance_margin_rate_pct",
    "max_adverse_excursion_pct",
    "max_drawdown_usd",
    "observed_buffer_pct",
)


def _hash(body: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()


def _finite_number(value: Any, *, positive: bool = False, nonnegative: bool = False) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    number = float(value)
    if not math.isfinite(number):
        return False
    if positive and number <= 0:
        return False
    if nonnegative and number < 0:
        return False
    return True


def _expected_buffer_pct(direction: str, mark: float, liquidation: float) -> float | None:
    """Measure distance from supplied prices; never derive liquidation."""
    if mark <= 0 or liquidation <= 0:
        return None
    if direction == "LONG" and liquidation < mark:
        return ((mark - liquidation) / mark) * 100.0
    if direction == "SHORT" and liquidation > mark:
        return ((liquidation - mark) / mark) * 100.0
    return None


def build_liquidation_buffer_receipt(
    *, policy_id: str, observations: Iterable[Mapping[str, Any]],
    minimum_required_buffer_pct: float,
) -> dict[str, Any]:
    """Seal supplied exchange evidence without inventing missing values."""
    body = {
        "schema": SCHEMA,
        "policy_id": str(policy_id),
        "minimum_required_buffer_pct": minimum_required_buffer_pct,
        "observations": [dict(row) for row in observations],
        "calculation_semantics": (
            "DISTANCE_FROM_EXCHANGE_REPORTED_LIQUIDATION_PRICE_"
            "AT_WORST_OBSERVED_ADVERSE_MARK"
        ),
        "liquidation_price_semantics": "EXCHANGE_REPORTED_NEVER_ESTIMATED",
    }
    return {**body, "content_sha256": _hash(body)}


def verify_liquidation_buffer_receipt(
    receipt: Any, *, policy_id: str, executed_episode_ids: Iterable[str],
) -> dict[str, Any]:
    """Return an auditable verification result; every defect fails closed."""
    required_ids = sorted({str(value) for value in executed_episode_ids if str(value)})
    defects: list[str] = []
    rows: list[dict[str, Any]] = []
    if not isinstance(receipt, dict):
        defects.append("LIQUIDATION_BUFFER_RECEIPT_MISSING")
    else:
        body = {key: value for key, value in receipt.items() if key != "content_sha256"}
        if receipt.get("schema") != SCHEMA:
            defects.append("LIQUIDATION_BUFFER_RECEIPT_SCHEMA_INVALID")
        if receipt.get("content_sha256") != _hash(body):
            defects.append("LIQUIDATION_BUFFER_RECEIPT_HASH_MISMATCH")
        if str(receipt.get("policy_id") or "") != str(policy_id):
            defects.append("LIQUIDATION_BUFFER_POLICY_ID_MISMATCH")
        minimum = receipt.get("minimum_required_buffer_pct")
        if not _finite_number(minimum, positive=True):
            defects.append("MINIMUM_REQUIRED_BUFFER_INVALID")
            minimum = None
        raw_rows = receipt.get("observations")
        if not isinstance(raw_rows, list):
            defects.append("LIQUIDATION_BUFFER_OBSERVATIONS_MISSING")
            raw_rows = []
        seen: set[str] = set()
        for index, raw in enumerate(raw_rows):
            prefix = f"OBSERVATION_{index}"
            if not isinstance(raw, dict):
                defects.append(f"{prefix}_INVALID")
                continue
            row = dict(raw)
            episode_id = str(row.get("episode_id") or "")
            if not episode_id:
                defects.append(f"{prefix}_EPISODE_ID_MISSING")
            elif episode_id in seen:
                defects.append(f"DUPLICATE_EPISODE_ID:{episode_id}")
            seen.add(episode_id)
            if row.get("schema") != OBSERVATION_SCHEMA:
                defects.append(f"{prefix}_SCHEMA_INVALID")
            if str(row.get("policy_id") or "") != str(policy_id):
                defects.append(f"{prefix}_POLICY_ID_MISMATCH")
            direction = str(row.get("direction") or "").upper()
            if direction not in {"LONG", "SHORT"}:
                defects.append(f"{prefix}_DIRECTION_INVALID")
            sources = row.get("source_receipt_ids")
            if not isinstance(sources, list) or not any(str(value).strip() for value in sources):
                defects.append(f"{prefix}_SOURCE_RECEIPT_REQUIRED")
            for field in REQUIRED_NUMERIC_FIELDS:
                if not _finite_number(
                    row.get(field),
                    positive=field in {
                        "leverage", "margin_usd", "equity_usd", "entry_price",
                        "worst_adverse_mark_price", "exchange_liquidation_price",
                    },
                    nonnegative=field in {
                        "maintenance_margin_rate_pct", "max_adverse_excursion_pct",
                        "max_drawdown_usd", "observed_buffer_pct",
                    },
                ):
                    defects.append(f"{prefix}_{field.upper()}_INVALID")
            if all(_finite_number(row.get(field), positive=True) for field in (
                "worst_adverse_mark_price", "exchange_liquidation_price",
            )) and direction in {"LONG", "SHORT"}:
                expected = _expected_buffer_pct(
                    direction,
                    float(row["worst_adverse_mark_price"]),
                    float(row["exchange_liquidation_price"]),
                )
                if expected is None:
                    defects.append(f"{prefix}_LIQUIDATION_SIDE_INCONSISTENT")
                elif _finite_number(row.get("observed_buffer_pct"), nonnegative=True) and not math.isclose(
                    float(row["observed_buffer_pct"]), expected, rel_tol=1e-9, abs_tol=1e-8,
                ):
                    defects.append(f"{prefix}_OBSERVED_BUFFER_MISMATCH")
                elif minimum is not None and expected < float(minimum):
                    defects.append(f"{prefix}_MINIMUM_BUFFER_NOT_MET")
            if all(_finite_number(row.get(field), positive=True) for field in (
                "entry_price", "worst_adverse_mark_price",
            )) and direction in {"LONG", "SHORT"} and _finite_number(
                row.get("max_adverse_excursion_pct"), nonnegative=True,
            ):
                entry = float(row["entry_price"])
                mark = float(row["worst_adverse_mark_price"])
                expected_mae = max(
                    0.0,
                    ((entry - mark) / entry) * 100.0 if direction == "LONG"
                    else ((mark - entry) / entry) * 100.0,
                )
                if not math.isclose(
                    float(row["max_adverse_excursion_pct"]), expected_mae,
                    rel_tol=1e-9, abs_tol=1e-8,
                ):
                    defects.append(f"{prefix}_MAX_ADVERSE_EXCURSION_MISMATCH")
            if _finite_number(row.get("margin_usd"), positive=True) and _finite_number(
                row.get("equity_usd"), positive=True,
            ) and float(row["margin_usd"]) > float(row["equity_usd"]):
                defects.append(f"{prefix}_MARGIN_EXCEEDS_EQUITY")
            if _finite_number(
                row.get("maintenance_margin_rate_pct"), nonnegative=True,
            ) and float(row["maintenance_margin_rate_pct"]) > 100.0:
                defects.append(f"{prefix}_MAINTENANCE_MARGIN_RATE_INVALID")
            rows.append(row)
        if sorted(seen) != required_ids:
            defects.append("LIQUIDATION_BUFFER_EPISODE_COHORT_MISMATCH")
    return {
        "schema": "liquidation_buffer_verification_v1",
        "policy_id": policy_id,
        "required_executed_episode_ids": required_ids,
        "observed_episode_ids": sorted({str(row.get("episode_id") or "") for row in rows if row.get("episode_id")}),
        "receipt_content_sha256": receipt.get("content_sha256") if isinstance(receipt, dict) else None,
        "defects": sorted(set(defects)),
        "passed": not defects,
        "no_guessed_liquidation_math": True,
    }
