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
}


def canonical_profile(schema, **fields):
    """Return a stable semantic identity from explicit runtime facts."""
    return json.dumps({"schema": schema, **fields}, sort_keys=True, separators=(",", ":"))


def _number(value, default=0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _cluster_boundary(policy, snapshot, evidence):
    candidates = (
        policy.get("correlated_cluster_boundary_pct"),
        policy.get("cluster_boundary_pct"),
        (evidence.get("cluster_evidence") or {}).get("boundary_pct") if isinstance(evidence.get("cluster_evidence"), dict) else None,
        snapshot.get("correlated_cluster_boundary_pct"),
    )
    for value in candidates:
        if value is not None and value != "":
            return value
    return None


def _chase_configuration(policy, snapshot):
    chase = (
        policy.get("chase")
        if isinstance(policy.get("chase"), dict)
        else snapshot.get("chase") if isinstance(snapshot.get("chase"), dict) else {}
    )
    enabled = policy.get("chase_enabled")
    if enabled is None:
        enabled = chase.get("enabled")
    if enabled is None and (
        policy.get("limit_chase_enabled") is not None or chase
    ):
        enabled = policy.get("limit_chase_enabled", True if chase else None)
    if enabled is None:
        return None
    return {
        "enabled": bool(enabled),
        "config": chase or None,
    }


def policy_comparability_key(policy, buf, snapshot):
    policy = policy if isinstance(policy, dict) else {}
    buf = buf if isinstance(buf, dict) else {}
    snapshot = snapshot if isinstance(snapshot, dict) else {}
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
        or policy.get("fee_model")
    )
    profile = (
        snapshot.get("execution_profile")
        or config.get("execution_profile")
        or evidence.get("execution_profile")
        or policy.get("execution_profile")
    )
    leverage = buf.get("leverage")
    if leverage is None:
        leverage = policy.get("leverage") or snapshot.get("leverage") or evidence.get("leverage")
    chase = _chase_configuration(policy, snapshot)
    cluster_boundary = _cluster_boundary(policy, snapshot, evidence)
    source_revision = (
        snapshot.get("source_git_rev")
        or snapshot.get("source_revision")
        or evidence.get("source_git_rev")
    )
    platform = snapshot.get("platform_relay_evidence") if isinstance(snapshot.get("platform_relay_evidence"), dict) else {}
    executor_revision = (
        snapshot.get("executor_revision")
        or evidence.get("generating_revision")
        or platform.get("generating_revision")
    )
    epoch_id = (
        snapshot.get("epoch_id")
        or snapshot.get("fresh_epoch_id")
        or policy.get("epoch_id")
        or evidence.get("epoch_id")
    )
    fill_gate_rev = (
        snapshot.get("fill_gate_rev")
        or policy.get("fill_gate_rev")
        or (snapshot.get("venue_fill_gate") or {}).get("revision")
        or (evidence.get("venue_fill_gate") or {}).get("revision")
        or evidence.get("fill_gate_rev")
    )
    if (
        any(policy.get(key) is None for key in REQUIRED_POLICY_KEYS)
        or leverage is None
        or not fee
        or not profile
        or chase is None
        or cluster_boundary is None
        or not source_revision
        or not executor_revision
        or not epoch_id
        or not fill_gate_rev
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
        "chase": chase,
        "correlated_cluster_boundary_pct": cluster_boundary,
        "source_revision": source_revision,
        "executor_revision": executor_revision,
        "epoch_id": epoch_id,
        "fill_gate_rev": fill_gate_rev,
    }
    digest = hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
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
