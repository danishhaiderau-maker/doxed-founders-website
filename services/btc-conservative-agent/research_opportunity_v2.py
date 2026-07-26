"""Opportunity-centric Type-B research events.

One shared direction call is one market opportunity. Lane evaluations, paper
fills, live fills and paused-shadow replays are child evidence and must never
inflate the independent sample count.
"""
from __future__ import annotations

import json
import math
import threading
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional


SCHEMA_VERSION = "type_b_research_opportunity_v2"
COLLECTION_ID = "TYPE_B_RESEARCH_V2"
EVENT_FILE = "type_b_research_v2.jsonl"
REPORT_FILE = "type_b_research_v2_report.json"
MAX_EVENT_FILE_BYTES = 64 * 1024 * 1024
EVENT_FILE_BACKUPS = 3
BENCHMARK_LANE = "CONTINUOUS"
MODE_PRIORITY = {
    "LIVE": 0,
    "PAPER": 1,
    "PAUSED_SHADOW": 2,
    "SHADOW": 3,
    "LAB": 4,
    "RESEARCH": 5,
}

ENTRY_FEATURES = (
    "price", "adx", "adx_slope_3", "plus_di", "minus_di", "di_separation",
    "atr", "volatility_percentile", "volume_ratio", "volume_percentile",
    "ret_1m", "ret_5m", "ema_slope", "structure", "momentum", "delta",
    "imbalance", "velocity", "directional_spread", "long_score",
    "short_score", "funding_rate_pct_8h", "session_utc", "is_weekend",
)
OPTIONAL_ENTRY_FEATURES = (
    "hour_utc", "regime", "book_spread_bps", "dist_to_support",
    "dist_to_resistance", "candle_range", "body_ratio", "wick_ratio",
    "reversal_risk_score", "entry_stage", "trend_health_state",
    "adx_derived", "dmi_period",
)
ALL_ENTRY_FEATURES = ENTRY_FEATURES + OPTIONAL_ENTRY_FEATURES

_write_lock = threading.RLock()


def _finite(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def precise_adx_bucket(value: Any, width: int = 5) -> str:
    adx = _finite(value)
    if adx is None:
        return "ADX_MISSING"
    adx = max(0.0, min(100.0, adx))
    lower = int(adx // width) * width
    upper = min(100, lower + width)
    return f"ADX_{lower:02d}_{upper:02d}"


def _range_bucket(value: Any, bounds: tuple[float, ...], labels: tuple[str, ...]) -> str:
    number = _finite(value)
    if number is None:
        return "MISSING"
    for upper, label in zip(bounds, labels):
        if number < upper:
            return label
    return labels[-1]


def entry_fingerprint(row: Mapping[str, Any]) -> dict[str, str]:
    """Return stable entry-time buckets; outcome fields are intentionally excluded."""
    features = dict(row.get("entry_features") or row)
    direction = str(row.get("direction") or features.get("direction") or "UNKNOWN").upper()
    plus_di = _finite(features.get("plus_di"))
    minus_di = _finite(features.get("minus_di"))
    directional_di = None
    if plus_di is not None and minus_di is not None:
        directional_di = plus_di - minus_di if direction == "LONG" else minus_di - plus_di
    weekend = features.get("is_weekend")
    price = _finite(features.get("price"))
    atr = _finite(features.get("atr"))
    atr_pct = (100.0 * atr / price) if atr is not None and price and price > 0 else None
    return {
        "direction": direction,
        "adx_5": precise_adx_bucket(features.get("adx")),
        "adx_source_gap": _range_bucket(
            (
                _finite(features.get("adx")) - _finite(features.get("adx_derived"))
                if _finite(features.get("adx")) is not None
                and _finite(features.get("adx_derived")) is not None
                else None
            ),
            (-10.0, -3.0, 3.0, 10.0, float("inf")),
            ("ADX_PRIMARY_LOWER_10P", "ADX_PRIMARY_LOWER", "ADX_ALIGNED", "ADX_PRIMARY_HIGHER", "ADX_PRIMARY_HIGHER_10P"),
        ),
        "adx_slope": _range_bucket(
            features.get("adx_slope_3"),
            (-1.0, -0.25, 0.25, 1.0, float("inf")),
            ("FALLING_FAST", "FALLING", "FLAT", "RISING", "RISING_FAST"),
        ),
        "directional_di": _range_bucket(
            directional_di,
            (-10.0, 0.0, 10.0, 20.0, float("inf")),
            ("OPPOSED_10P", "OPPOSED", "ALIGNED_0_10", "ALIGNED_10_20", "ALIGNED_20P"),
        ),
        "di_separation": _range_bucket(
            features.get("di_separation"),
            (5.0, 10.0, 20.0, 30.0, float("inf")),
            ("DISEP_0_5", "DISEP_5_10", "DISEP_10_20", "DISEP_20_30", "DISEP_30P"),
        ),
        "volume_percentile": _range_bucket(
            features.get("volume_percentile"),
            (10.0, 25.0, 50.0, 75.0, 90.0, float("inf")),
            ("VOLP_0_10", "VOLP_10_25", "VOLP_25_50", "VOLP_50_75", "VOLP_75_90", "VOLP_90_100"),
        ),
        "volume_ratio": _range_bucket(
            features.get("volume_ratio"),
            (0.5, 0.75, 1.0, 1.5, float("inf")),
            ("VOLR_LT_050", "VOLR_050_075", "VOLR_075_100", "VOLR_100_150", "VOLR_150P"),
        ),
        "score_gap": _range_bucket(
            features.get("directional_spread"),
            (2.0, 3.0, 4.0, 5.0, float("inf")),
            ("GAP_0_1", "GAP_2", "GAP_3", "GAP_4", "GAP_5P"),
        ),
        "volatility_percentile": _range_bucket(
            features.get("volatility_percentile"),
            (10.0, 25.0, 50.0, 75.0, 90.0, float("inf")),
            ("ATRP_0_10", "ATRP_10_25", "ATRP_25_50", "ATRP_50_75", "ATRP_75_90", "ATRP_90_100"),
        ),
        "atr_pct": _range_bucket(
            atr_pct,
            (0.02, 0.04, 0.06, 0.10, float("inf")),
            ("ATRPCT_LT_002", "ATRPCT_002_004", "ATRPCT_004_006", "ATRPCT_006_010", "ATRPCT_010P"),
        ),
        "ema_slope": _range_bucket(
            features.get("ema_slope"),
            (-0.001, -0.0002, 0.0002, 0.001, float("inf")),
            ("EMA_DOWN_FAST", "EMA_DOWN", "EMA_FLAT", "EMA_UP", "EMA_UP_FAST"),
        ),
        "ret_1m": _range_bucket(
            features.get("ret_1m"),
            (-0.001, -0.00025, 0.00025, 0.001, float("inf")),
            ("RET1_DOWN_FAST", "RET1_DOWN", "RET1_FLAT", "RET1_UP", "RET1_UP_FAST"),
        ),
        "ret_5m": _range_bucket(
            features.get("ret_5m"),
            (-0.003, -0.001, 0.001, 0.003, float("inf")),
            ("RET5_DOWN_FAST", "RET5_DOWN", "RET5_FLAT", "RET5_UP", "RET5_UP_FAST"),
        ),
        "structure": _range_bucket(
            features.get("structure"),
            (-4.0, -1.0, 1.0, 4.0, float("inf")),
            ("STRUCT_BEAR_STRONG", "STRUCT_BEAR", "STRUCT_NEUTRAL", "STRUCT_BULL", "STRUCT_BULL_STRONG"),
        ),
        "momentum": _range_bucket(
            features.get("momentum"),
            (-1.0, -0.25, 0.25, 1.0, float("inf")),
            ("MOM_DOWN_STRONG", "MOM_DOWN", "MOM_FLAT", "MOM_UP", "MOM_UP_STRONG"),
        ),
        "delta": _range_bucket(
            features.get("delta"),
            (-10.0, -2.0, 2.0, 10.0, float("inf")),
            ("DELTA_NEG_STRONG", "DELTA_NEG", "DELTA_FLAT", "DELTA_POS", "DELTA_POS_STRONG"),
        ),
        "imbalance": _range_bucket(
            features.get("imbalance"),
            (-0.5, -0.1, 0.1, 0.5, float("inf")),
            ("IMB_NEG_STRONG", "IMB_NEG", "IMB_FLAT", "IMB_POS", "IMB_POS_STRONG"),
        ),
        "velocity": _range_bucket(
            features.get("velocity"),
            (-0.001, -0.0002, 0.0002, 0.001, float("inf")),
            ("VEL_DOWN_FAST", "VEL_DOWN", "VEL_FLAT", "VEL_UP", "VEL_UP_FAST"),
        ),
        "funding": _range_bucket(
            features.get("funding_rate_pct_8h"),
            (-0.02, -0.005, 0.005, 0.02, float("inf")),
            ("FUND_NEG_HIGH", "FUND_NEG", "FUND_FLAT", "FUND_POS", "FUND_POS_HIGH"),
        ),
        "support_distance": _range_bucket(
            features.get("dist_to_support"),
            (0.001, 0.003, 0.006, 0.012, float("inf")),
            ("SUP_LT_010", "SUP_010_030", "SUP_030_060", "SUP_060_120", "SUP_120P"),
        ),
        "resistance_distance": _range_bucket(
            features.get("dist_to_resistance"),
            (0.001, 0.003, 0.006, 0.012, float("inf")),
            ("RES_LT_010", "RES_010_030", "RES_030_060", "RES_060_120", "RES_120P"),
        ),
        "candle_range_pct": _range_bucket(
            (100.0 * _finite(features.get("candle_range")) / price)
            if _finite(features.get("candle_range")) is not None and price and price > 0 else None,
            (0.02, 0.05, 0.10, 0.20, float("inf")),
            ("RANGE_LT_002", "RANGE_002_005", "RANGE_005_010", "RANGE_010_020", "RANGE_020P"),
        ),
        "body_ratio": _range_bucket(
            features.get("body_ratio"),
            (0.2, 0.4, 0.6, 0.8, float("inf")),
            ("BODY_0_20", "BODY_20_40", "BODY_40_60", "BODY_60_80", "BODY_80P"),
        ),
        "wick_ratio": _range_bucket(
            features.get("wick_ratio"),
            (0.2, 0.4, 0.6, 0.8, float("inf")),
            ("WICK_0_20", "WICK_20_40", "WICK_40_60", "WICK_60_80", "WICK_80P"),
        ),
        "reversal_risk": _range_bucket(
            features.get("reversal_risk_score"),
            (20.0, 40.0, 60.0, 80.0, float("inf")),
            ("REV_0_20", "REV_20_40", "REV_40_60", "REV_60_80", "REV_80P"),
        ),
        "entry_stage": str(features.get("entry_stage") or "MISSING").upper(),
        "trend_health": str(features.get("trend_health_state") or "MISSING").upper(),
        "book_spread_bps": _range_bucket(
            features.get("book_spread_bps"),
            (0.5, 1.0, 2.0, 5.0, float("inf")),
            ("BOOK_0_05", "BOOK_05_10", "BOOK_10_20", "BOOK_20_50", "BOOK_50P"),
        ),
        "session_utc": str(features.get("session_utc") or "MISSING").upper(),
        "hour_utc": (
            f"HOUR_{int(features.get('hour_utc')):02d}"
            if _finite(features.get("hour_utc")) is not None else "MISSING"
        ),
        "regime": str(features.get("regime") or "MISSING").upper(),
        "day_type": (
            "WEEKEND" if weekend is True
            else "WEEKDAY" if weekend is False
            else "MISSING"
        ),
    }


def feature_quality(entry_features: Mapping[str, Any]) -> dict:
    categorical = {"session_utc", "is_weekend"}
    def present(name: str) -> bool:
        value = entry_features.get(name)
        if name in categorical:
            return value is not None and str(value).strip() != ""
        return _finite(value) is not None

    available = [name for name in ENTRY_FEATURES if present(name)]
    missing = [name for name in ENTRY_FEATURES if not present(name)]
    coverage = round(100.0 * len(available) / len(ENTRY_FEATURES), 2)
    critical = ("adx", "plus_di", "minus_di", "volume_ratio", "volume_percentile", "directional_spread")
    critical_missing = [name for name in critical if not present(name)]
    return {
        "available": len(available),
        "expected": len(ENTRY_FEATURES),
        "coverage_pct": coverage,
        "missing": missing,
        "critical_missing": critical_missing,
        "valid_for_holdout": not critical_missing and coverage >= 90.0,
    }


def opportunity_event(
    *,
    opportunity_id: str,
    ts: str,
    direction: str,
    entry_features: Mapping[str, Any],
    mode: str,
    bot_version: str,
    analyzer_sync_id: str,
    policy_version: str,
    metadata: Optional[Mapping[str, Any]] = None,
) -> dict:
    features = {name: entry_features.get(name) for name in ALL_ENTRY_FEATURES}
    features["adx_bucket_5"] = precise_adx_bucket(features.get("adx"))
    return {
        "schema": SCHEMA_VERSION,
        "collection_id": COLLECTION_ID,
        "event": "OPPORTUNITY",
        "opportunity_id": str(opportunity_id),
        "ts": str(ts),
        "direction": str(direction or "UNKNOWN").upper(),
        "mode": str(mode or "RESEARCH").upper(),
        "entry_features": features,
        "quality": feature_quality(features),
        "bot_version": bot_version,
        "analyzer_sync_id": analyzer_sync_id,
        "policy_version": policy_version,
        "metadata": dict(metadata or {}),
    }


def child_event(
    *,
    event: str,
    opportunity_id: str,
    ts: str,
    lane: Optional[str] = None,
    mode: Optional[str] = None,
    trade_id: Optional[str] = None,
    payload: Optional[Mapping[str, Any]] = None,
) -> dict:
    return {
        "schema": SCHEMA_VERSION,
        "collection_id": COLLECTION_ID,
        "event": str(event).upper(),
        "opportunity_id": str(opportunity_id),
        "ts": str(ts),
        "lane": str(lane or "UNSPECIFIED").upper(),
        "mode": str(mode or "RESEARCH").upper(),
        "trade_id": trade_id,
        "payload": dict(payload or {}),
    }


def append_event(base: Any, row: Mapping[str, Any]) -> None:
    path = Path(base) / EVENT_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(dict(row), separators=(",", ":"), sort_keys=True)
    with _write_lock:
        if path.is_file() and path.stat().st_size + len(encoded) + 1 > MAX_EVENT_FILE_BYTES:
            oldest = path.with_name(f"{path.name}.{EVENT_FILE_BACKUPS}")
            if oldest.exists():
                oldest.unlink()
            for index in range(EVENT_FILE_BACKUPS - 1, 0, -1):
                source = path.with_name(f"{path.name}.{index}")
                if source.exists():
                    source.replace(path.with_name(f"{path.name}.{index + 1}"))
            path.replace(path.with_name(f"{path.name}.1"))
        with path.open("a", encoding="utf-8") as handle:
            handle.write(encoded + "\n")


def load_events(path: Any) -> list[dict]:
    source = Path(path)
    if source.is_dir():
        source = source / EVENT_FILE
    sources = []
    for index in range(EVENT_FILE_BACKUPS, 0, -1):
        backup = source.with_name(f"{source.name}.{index}")
        if backup.is_file():
            sources.append(backup)
    if source.is_file():
        sources.append(source)
    if not sources:
        return []
    rows = []
    for event_source in sources:
        with event_source.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if row.get("schema") == SCHEMA_VERSION and row.get("opportunity_id"):
                    rows.append(row)
    return rows


def materialize(events: Iterable[Mapping[str, Any]]) -> list[dict]:
    grouped: dict[str, dict] = {}
    for source in events:
        row = dict(source)
        oid = str(row.get("opportunity_id") or "")
        if not oid:
            continue
        item = grouped.setdefault(oid, {
            "opportunity_id": oid, "created_ts": None, "direction": "UNKNOWN",
            "entry_features": {}, "quality": {}, "modes": set(),
            "lanes": defaultdict(lambda: {"verdict": None, "events": [], "outcomes": []}),
            "outcomes": [], "_has_opportunity": False,
        })
        mode = str(row.get("mode") or "RESEARCH").upper()
        item["modes"].add(mode)
        event = str(row.get("event") or "").upper()
        if event == "OPPORTUNITY":
            item["_has_opportunity"] = True
            item["created_ts"] = item["created_ts"] or row.get("ts")
            item["direction"] = row.get("direction") or item["direction"]
            item["entry_features"] = dict(row.get("entry_features") or {})
            item["quality"] = dict(row.get("quality") or {})
            item["bot_version"] = row.get("bot_version")
            item["analyzer_sync_id"] = row.get("analyzer_sync_id")
            item["policy_version"] = row.get("policy_version")
            continue
        lane = str(row.get("lane") or "UNSPECIFIED").upper()
        lane_row = item["lanes"][lane]
        payload = dict(row.get("payload") or {})
        if event == "LANE_VERDICT":
            lane_row["verdict"] = payload
        elif event == "OUTCOME":
            outcome = {"lane": lane, "mode": mode, "trade_id": row.get("trade_id"), **payload}
            lane_row["outcomes"].append(outcome)
            item["outcomes"].append(outcome)
        else:
            lane_row["events"].append({"event": event, "mode": mode, "trade_id": row.get("trade_id"), **payload})

    result = []
    for item in grouped.values():
        if not item.pop("_has_opportunity", False):
            continue
        deduped = {}
        for outcome in item["outcomes"]:
            key = (
                str(outcome.get("lane") or ""),
                str(outcome.get("mode") or ""),
                str(outcome.get("trade_id") or ""),
            )
            deduped[key] = outcome
        item["outcomes"] = list(deduped.values())
        for lane, lane_row in item["lanes"].items():
            lane_row["outcomes"] = [
                outcome for outcome in item["outcomes"]
                if str(outcome.get("lane") or "") == lane
            ]
        item["modes"] = sorted(item["modes"])
        item["lanes"] = {lane: value for lane, value in item["lanes"].items()}
        item["entry_fingerprint"] = entry_fingerprint(item)
        item["preferred_outcome"] = preferred_outcome(item)
        item["outcome_label"] = outcome_label(item["preferred_outcome"])
        item["status"] = "CLOSED" if item["preferred_outcome"] else "COLLECTING"
        result.append(item)
    return sorted(result, key=lambda row: str(row.get("created_ts") or ""))


def preferred_outcome(row: Mapping[str, Any]) -> Optional[dict]:
    outcomes = list(row.get("outcomes") or [])
    if not outcomes:
        return None
    benchmark = [
        out for out in outcomes
        if str(out.get("lane") or "").upper() == BENCHMARK_LANE
    ]
    if not benchmark:
        return None
    return dict(sorted(
        benchmark,
        key=lambda out: (
            MODE_PRIORITY.get(str(out.get("mode") or "").upper(), 99),
            0 if bool(out.get("filled", True)) else 1,
        ),
    )[0])


def _analysis_fingerprint(row: Mapping[str, Any]) -> dict[str, str]:
    fingerprint = dict(row.get("entry_fingerprint") or entry_fingerprint(row))
    outcome = preferred_outcome(row)
    fingerprint["evidence_mode"] = (
        str((outcome or {}).get("mode") or "MISSING").upper()
    )
    return fingerprint


def outcome_label(outcome: Optional[Mapping[str, Any]]) -> Optional[str]:
    if not outcome or not bool(outcome.get("filled", True)):
        return None
    mfe = _finite(outcome.get("max_mfe_pct"))
    if mfe is None:
        mfe = _finite(outcome.get("mfe_pct"))
    if mfe is None:
        return None
    if mfe >= 15.0:
        return "TYPE_B"
    if mfe < 10.0:
        return "TYPE_A"
    return "MIXED"


def probability_table(opportunities: Iterable[Mapping[str, Any]]) -> list[dict]:
    """Precise, outcome-labelled entry buckets. Each opportunity contributes once."""
    grouped: dict[tuple[str, str], dict[str, float]] = defaultdict(
        lambda: {"n": 0, "type_b": 0, "wins": 0, "pnl": 0.0}
    )
    for row in opportunities:
        outcome = preferred_outcome(row)
        label = outcome_label(outcome)
        if label is None:
            continue
        fingerprint = _analysis_fingerprint(row)
        evidence_mode = fingerprint.get("evidence_mode", "MISSING")
        for feature, bucket in fingerprint.items():
            if feature == "evidence_mode":
                continue
            cell = grouped[(evidence_mode, feature, str(bucket))]
            cell["n"] += 1
            cell["type_b"] += int(label == "TYPE_B")
            pnl = _finite((outcome or {}).get("net_pnl_usd")) or 0.0
            cell["pnl"] += pnl
            cell["wins"] += int(pnl > 0)
    result = []
    for (evidence_mode, feature, bucket), cell in grouped.items():
        n = int(cell["n"])
        result.append({
            "feature": feature,
            "bucket": bucket,
            "evidence_mode": evidence_mode,
            "n": n,
            "type_b": int(cell["type_b"]),
            "type_b_rate_pct": round(100.0 * cell["type_b"] / n, 2) if n else None,
            "win_rate_pct": round(100.0 * cell["wins"] / n, 2) if n else None,
            "net_pnl_usd": round(cell["pnl"], 4),
            "ev_usd": round(cell["pnl"] / n, 4) if n else None,
        })
    return sorted(result, key=lambda cell: (-cell["n"], cell["feature"], cell["bucket"]))


def _rule_matches(row: Mapping[str, Any], rule: tuple[tuple[str, str], ...]) -> bool:
    fingerprint = _analysis_fingerprint(row)
    return all(str(fingerprint.get(feature)) == bucket for feature, bucket in rule)


def _candidate_rules(rows: list[Mapping[str, Any]], min_n: int) -> list[dict]:
    dimensions = (
        "adx_5", "adx_source_gap", "adx_slope", "directional_di", "di_separation",
        "volume_percentile", "volume_ratio", "volatility_percentile", "atr_pct",
        "score_gap", "book_spread_bps", "ema_slope", "ret_1m", "ret_5m",
        "structure", "momentum", "delta", "imbalance", "velocity", "funding",
        "support_distance", "resistance_distance", "candle_range_pct",
        "body_ratio", "wick_ratio", "reversal_risk", "entry_stage",
        "trend_health", "direction", "session_utc", "hour_utc", "regime",
        "day_type", "evidence_mode",
    )
    labelled = [(row, outcome_label(preferred_outcome(row))) for row in rows]
    labelled = [(row, label) for row, label in labelled if label is not None]
    mode_totals: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for row, label in labelled:
        mode = str(_analysis_fingerprint(row).get("evidence_mode") or "")
        if mode and mode != "MISSING":
            mode_totals[mode][0] += 1
            mode_totals[mode][1] += int(label == "TYPE_B")
    counts: dict[tuple[tuple[str, str], ...], list[int]] = defaultdict(lambda: [0, 0])
    for row, label in labelled:
        fp = _analysis_fingerprint(row)
        present = [(feature, str(fp.get(feature))) for feature in dimensions if fp.get(feature) not in (None, "MISSING")]
        mode = next((condition for condition in present if condition[0] == "evidence_mode"), None)
        if mode is None:
            continue
        for condition in present:
            if condition[0] == "evidence_mode":
                continue
            key = tuple(sorted((mode, condition)))
            counts[key][0] += 1
            counts[key][1] += int(label == "TYPE_B")
        # Interactions are bounded to high-value anchors x context, always
        # including evidence mode so paper/live/shadow policies never mix.
        anchors = [condition for condition in present if condition[0] in {
            "adx_5", "adx_source_gap", "adx_slope", "directional_di", "volume_percentile",
            "volume_ratio", "volatility_percentile", "score_gap",
            "ema_slope", "structure",
        }]
        contexts = [condition for condition in present if condition[0] in {
            "direction", "session_utc", "regime", "day_type", "entry_stage",
            "trend_health",
        }]
        for idx, left in enumerate(anchors):
            for right in anchors[idx + 1:]:
                key = tuple(sorted((mode, left, right)))
                counts[key][0] += 1
                counts[key][1] += int(label == "TYPE_B")
            for context in contexts:
                key = tuple(sorted((mode, left, context)))
                counts[key][0] += 1
                counts[key][1] += int(label == "TYPE_B")
    candidates = []
    for rule, (n, type_b) in counts.items():
        if n < min_n:
            continue
        mode_bucket = next(
            (bucket for feature, bucket in rule if feature == "evidence_mode"),
            "",
        )
        mode_total, mode_type_b = mode_totals.get(mode_bucket, (0, 0))
        baseline = mode_type_b / mode_total if mode_total else 0.0
        rate = type_b / n
        candidates.append({
            "conditions": [{"feature": feature, "bucket": bucket} for feature, bucket in rule],
            "rule_key": " AND ".join(f"{feature}={bucket}" for feature, bucket in rule),
            "n": n,
            "type_b": type_b,
            "type_b_rate_pct": round(100.0 * rate, 2),
            "mode_baseline_pct": round(100.0 * baseline, 2) if mode_total else None,
            "lift": round(rate / baseline, 3) if baseline else None,
        })
    return sorted(candidates, key=lambda item: (-(item["lift"] or 0), -item["n"], item["rule_key"]))


def rolling_holdout_analysis(
    opportunities: Iterable[Mapping[str, Any]],
    *,
    min_total: int = 220,
    min_train: int = 100,
    holdout_size: int = 40,
    step: int = 40,
    min_rule_train_n: int = 12,
    min_rule_holdout_n: int = 8,
) -> dict:
    """Select candidates on past rows and validate only on later chronological windows."""
    rows = [
        dict(row) for row in opportunities
        if bool((row.get("quality") or {}).get("valid_for_holdout"))
        and outcome_label(preferred_outcome(row)) is not None
    ]
    rows.sort(key=lambda row: str(row.get("created_ts") or ""))
    windows = []
    start = min_train
    while start + holdout_size <= len(rows):
        train = rows[:start]
        holdout = rows[start:start + holdout_size]
        train_candidates = _candidate_rules(train, min_rule_train_n)[:25]
        tested = []
        for candidate in train_candidates:
            rule = tuple(
                (condition["feature"], condition["bucket"])
                for condition in candidate["conditions"]
            )
            matched = [row for row in holdout if _rule_matches(row, rule)]
            mode_bucket = next(
                (
                    condition["bucket"] for condition in candidate["conditions"]
                    if condition["feature"] == "evidence_mode"
                ),
                None,
            )
            baseline_rows = [
                row for row in holdout
                if _analysis_fingerprint(row).get("evidence_mode") == mode_bucket
            ]
            baseline_n = len(baseline_rows)
            baseline_b = sum(
                outcome_label(preferred_outcome(row)) == "TYPE_B"
                for row in baseline_rows
            )
            baseline = baseline_b / baseline_n if baseline_n else 0.0
            matched_b = sum(outcome_label(preferred_outcome(row)) == "TYPE_B" for row in matched)
            rate = matched_b / len(matched) if matched else 0.0
            lift = rate / baseline if baseline else None
            tested.append({
                **candidate,
                "holdout_n": len(matched),
                "holdout_type_b": matched_b,
                "holdout_baseline_n": baseline_n,
                "holdout_baseline_type_b_rate_pct": (
                    round(100.0 * baseline, 2) if baseline_n else None
                ),
                "holdout_type_b_rate_pct": round(100.0 * rate, 2) if matched else None,
                "holdout_lift": round(lift, 3) if lift is not None else None,
                "positive": bool(len(matched) >= min_rule_holdout_n and lift is not None and lift >= 1.2),
            })
        windows.append({
            "train_n": len(train),
            "holdout_n": len(holdout),
            "holdout_start": holdout[0].get("created_ts") if holdout else None,
            "holdout_end": holdout[-1].get("created_ts") if holdout else None,
            "baseline_type_b_rate_pct": None,
            "baseline_note": "Lift is calculated against the matching benchmark evidence mode.",
            "rules": tested,
        })
        start += step
    validation: dict[str, dict] = {}
    for window in windows:
        for rule in window["rules"]:
            item = validation.setdefault(rule["rule_key"], {
                "rule_key": rule["rule_key"],
                "conditions": rule["conditions"],
                "windows_tested": 0,
                "positive_windows": 0,
                "holdout_n": 0,
                "holdout_type_b": 0,
            })
            if rule["holdout_n"] >= min_rule_holdout_n:
                item["windows_tested"] += 1
                item["positive_windows"] += int(rule["positive"])
                item["holdout_n"] += rule["holdout_n"]
                item["holdout_type_b"] += rule["holdout_type_b"]
    validated = []
    for item in validation.values():
        item["repeat_confirmed"] = bool(item["windows_tested"] >= 3 and item["positive_windows"] >= 3)
        item["holdout_type_b_rate_pct"] = (
            round(100.0 * item["holdout_type_b"] / item["holdout_n"], 2)
            if item["holdout_n"] else None
        )
        if item["repeat_confirmed"]:
            validated.append(item)
    ready = len(rows) >= min_total and len(windows) >= 3
    manual_review_ready = bool(ready and validated)
    return {
        "eligible_outcomes": len(rows),
        "minimum_total": min_total,
        "windows_completed": len(windows),
        "windows_required": 3,
        "ready_for_research_decision": ready,
        "manual_review_ready": manual_review_ready,
        "production_entry_gate_ready": False,
        "validated_rules": sorted(validated, key=lambda item: (-item["positive_windows"], -item["holdout_n"])),
        "windows": windows,
        "usage_note": (
            "Research only. Candidate rules are selected on past rows and scored only "
            "on later non-overlapping chronological windows. Production promotion always "
            "requires a separate reviewed release."
        ),
    }


def summarize(opportunities: Iterable[Mapping[str, Any]]) -> dict:
    rows = list(opportunities)
    fields = defaultdict(lambda: {"available": 0, "total": 0})
    pnl = 0.0
    filled = type_b = valid = 0
    mode_counts = defaultdict(int)
    for row in rows:
        valid += int(bool((row.get("quality") or {}).get("valid_for_holdout")))
        for name in ALL_ENTRY_FEATURES:
            fields[name]["total"] += 1
            fields[name]["available"] += int((row.get("entry_features") or {}).get(name) is not None)
        for mode in row.get("modes") or []:
            mode_counts[str(mode)] += 1
        preferred = preferred_outcome(row)
        if not preferred:
            continue
        filled += int(bool(preferred.get("filled", True)))
        pnl += float(preferred.get("net_pnl_usd") or 0.0)
        type_b += int(outcome_label(preferred) == "TYPE_B")
    coverage = {
        name: {**counts, "pct": round(100.0 * counts["available"] / counts["total"], 2) if counts["total"] else None}
        for name, counts in fields.items()
    }
    holdout = rolling_holdout_analysis(rows)
    return {
        "schema": "type_b_research_v2_report_v1",
        "collection_id": COLLECTION_ID,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "independent_opportunities": len(rows),
        "valid_holdout_opportunities": valid,
        "completed_opportunities": sum(1 for row in rows if preferred_outcome(row)),
        "filled_opportunities": filled,
        "type_b_outcomes": type_b,
        "benchmark_net_pnl_usd": round(pnl, 4),
        "net_pnl_usd": round(pnl, 4),
        "modes_observed": dict(sorted(mode_counts.items())),
        "feature_coverage": coverage,
        "entry_probability_table": probability_table(rows),
        "rolling_holdout": holdout,
        "readiness": (
            "RESEARCH_VALIDATED"
            if holdout["manual_review_ready"]
            else "READY_FOR_ROLLING_HOLDOUT"
            if holdout["eligible_outcomes"] >= 220
            else "COLLECTING"
        ),
        "execution_policy": "ADVISORY_ONLY_NEVER_AUTO_APPLY",
        "usage_note": (
            "One shared AI_SCAN direction call equals one sample. CONTINUOUS is "
            "the stable benchmark outcome; lane and execution-mode children are "
            "audit evidence and are never substituted for that benchmark."
        ),
    }
