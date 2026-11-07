"""Strictly prior features. No interpolation, forward-fill, or future fit."""

from __future__ import annotations

from research.causal_grammar_v1 import asof as asof_mod
from research.causal_grammar_v1.contracts_v1 import FEATURE_SCHEMA, ContractError


def append_series(bound_series: str, record: dict) -> None:
    if record["series_id"] != bound_series:
        raise ContractError("CROSS_SERIES_STATE")


def include_in_prior(record: dict, cutoff_ms: int) -> bool:
    availability_ms = record.get("availability_ms")
    if availability_ms is None or availability_ms >= cutoff_ms:
        return False
    return asof_mod.is_eligible(record, cutoff_ms)


def fill_gap(last_value: object) -> object:
    del last_value
    return None


def resolve_gap(
    left_value: object,
    right_value: object,
    left_ms: int,
    right_ms: int,
    cutoff_ms: int,
) -> object:
    del left_value, right_value, left_ms, right_ms, cutoff_ms
    return None


def _feature_row(series_id: str, cutoff_ms: int, feature_spec: dict, status: str, value: object) -> dict:
    return {
        "schema": FEATURE_SCHEMA,
        "name": feature_spec["name"],
        "series_id": series_id,
        "cutoff_ms": cutoff_ms,
        "unit": feature_spec["unit"],
        "lookback_observations": feature_spec["lookback_observations"],
        "lookback_unit": feature_spec["lookback_unit"],
        "status": status,
        "value": value,
    }


def prior_mean(
    records: list[dict],
    series_id: str,
    cutoff_ms: int,
    feature_spec: dict,
    *,
    bound_series: str,
) -> dict:
    if bound_series != series_id:
        raise ContractError("CROSS_SERIES_STATE")
    if feature_spec["interpolation"] != "prohibited" or feature_spec["forward_fill"] != "prohibited":
        raise ContractError("UNSUPPORTED_SPEC")
    if feature_spec["warmup"] != "unknown_if_insufficient":
        raise ContractError("UNSUPPORTED_SPEC")
    eligible: list[dict] = []
    expired: list[dict] = []
    future: list[dict] = []
    for record in records:
        if record["series_id"] != series_id:
            continue
        append_series(bound_series, record)
        availability_ms = record.get("availability_ms")
        if availability_ms is None:
            continue
        if include_in_prior(record, cutoff_ms):
            eligible.append(record)
        elif availability_ms < cutoff_ms:
            expired.append(record)
        else:
            future.append(record)
    eligible.sort(key=lambda record: (record["availability_ms"], record["linkage"]))
    expired.sort(key=lambda record: (record["availability_ms"], record["linkage"]))
    future.sort(key=lambda record: (record["availability_ms"], record["linkage"]))
    min_periods = feature_spec["min_periods"]
    if len(eligible) >= min_periods:
        window = eligible[-min_periods:]
        value = sum(point["value"] for point in window) / min_periods
        return _feature_row(series_id, cutoff_ms, feature_spec, "DEFINED", value)
    last_value = expired[-1]["value"] if expired else None
    filled = fill_gap(last_value)
    if filled is not None:
        return _feature_row(series_id, cutoff_ms, feature_spec, "DEFINED", filled)
    if expired and future:
        interpolated = resolve_gap(
            expired[-1]["value"],
            future[0]["value"],
            expired[-1]["availability_ms"],
            future[0]["availability_ms"],
            cutoff_ms,
        )
        if interpolated is not None:
            return _feature_row(series_id, cutoff_ms, feature_spec, "DEFINED", interpolated)
    return _feature_row(series_id, cutoff_ms, feature_spec, "UNKNOWN", None)


def features_at(records: list[dict], feature_spec: dict) -> list[dict]:
    rows: list[dict] = []
    for cutoff_ms in feature_spec["cutoffs_ms"]:
        for series_id in ("A", "B", "C"):
            rows.append(
                prior_mean(
                    records,
                    series_id,
                    cutoff_ms,
                    feature_spec,
                    bound_series=series_id,
                )
            )
    return rows
