"""Pure counterfactual policy and replay-horizon normalization."""

from __future__ import annotations

import hashlib
import json


REQUIRED_POLICY_KEYS = (
    "policy_snapshot_schema",
    "policy_version",
    "hard_stop_margin_pct",
    "thesis_fast_exit_unreal_pct",
    "thesis_mfe_protect_pct",
    "trail_ladder",
    "exit_profile_id",
)
REQUIRED_HORIZONS_SEC = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "60m": 3600,
    "120m": 7200,
    "1h": 3600,
    "4h": 14400,
}


def _number(value, default=0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def policy_comparability_key(policy, buf, snapshot):
    evidence = (
        snapshot.get("bitfinex_evidence")
        if isinstance(snapshot.get("bitfinex_evidence"), dict)
        else {}
    )
    config = snapshot.get("config") or {}
    fee = (
        snapshot.get("fee_model")
        or config.get("fee_model")
        or evidence.get("fee_model")
    )
    profile = (
        snapshot.get("execution_profile")
        or config.get("execution_profile")
        or evidence.get("execution_profile")
    )
    leverage = buf.get("leverage")
    if (
        any(policy.get(key) is None for key in REQUIRED_POLICY_KEYS)
        or leverage is None
        or not fee
        or not profile
    ):
        return None
    value = {
        "schema": "policy_comparability_v1",
        **{
            key: policy.get(key)
            for key in REQUIRED_POLICY_KEYS
            if key != "policy_snapshot_schema"
        },
        "leverage": leverage,
        "fee_model": fee,
        "execution_profile": profile,
    }
    digest = hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return f"policy_comparability_v1:{digest}"


def horizons(replay, *, post_exit, required_horizons=None):
    origin_raw = replay.get("exit_t_rel" if post_exit else "virtual_fill_t")
    valid = origin_raw is not None and _number(origin_raw, -1) >= 0
    origin = _number(origin_raw, 0) if valid else None
    ticks = sorted(
        (row for row in replay.get("ticks") or [] if isinstance(row, dict)),
        key=lambda row: _number(row.get("t"), 0),
    )
    executable_key = (
        "best_bid"
        if str(replay.get("direction") or "LONG").upper() == "LONG"
        else "best_ask"
    )
    values = {}
    for label, seconds in (required_horizons or REQUIRED_HORIZONS_SEC).items():
        target = origin + seconds if origin is not None else None
        observed = next(
            (
                row
                for row in ticks
                if target is not None
                and (
                    not post_exit
                    or str(row.get("phase") or "") == "post_exit"
                )
                and _number(row.get("t"), -1) >= target
                and _number(row.get(executable_key), 0) > 0
            ),
            None,
        )
        values[label] = {
            "required_sec": seconds,
            "observed": observed is not None,
            "tick_t_rel": observed.get("t") if observed else None,
            "price": observed.get(executable_key) if observed else None,
            "unreal_pct": observed.get("unreal_pct") if observed else None,
            "best_bid": observed.get("best_bid") if observed else None,
            "best_ask": observed.get("best_ask") if observed else None,
            "observed_ts": observed.get("observed_ts") if observed else None,
        }
    complete = valid and all(row["observed"] for row in values.values())
    if post_exit:
        complete = complete and replay.get("post_exit_complete") is True
    result = {
        "schema": "post_exit_horizons_v1" if post_exit else "entry_horizons_v1",
        "required": values,
        "complete": bool(complete),
    }
    if not post_exit:
        result["origin_t_rel"] = origin
    return result
