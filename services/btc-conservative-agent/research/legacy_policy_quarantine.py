"""Explicit episode-id quarantine for legacy POLICY_IDENTITY_CONTAMINATION (HM-approved 18 Oct 2026).

A FIXED list of concrete episode ids, no pattern and no time cutoff to *select* episodes. An episode is excluded only if
(1) its id is on the list AND (2) every row of its listed divergent policy ids that carries a policy_signature
(decision, order_intent, execution, lifecycle ledgers) has a creation timestamp <= the id's recorded
``identity_rows_max_ts`` (the evidence bound). Any row newer than that bound means the episode is NOT quarantined and the
contamination check keeps failing (fail closed). Ids not on the list are never touched. EpochGuard is unchanged.

Quarantined episodes are removed from every result and count the safe-policy genome report builds (opportunities, decisions,
order intents, lifecycles, executions, market segments, pre-entry features, counts) and are listed only in
``epoch_scope.quarantined_legacy_episodes``. They are not archived: the next reset clears the ledgers they live in.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping

SCHEMA = "legacy_policy_identity_quarantine_v1"

LEGACY_POLICY_IDENTITY_EPISODES: dict[str, dict[str, Any]] = {
    "episode-1ff6cd183c4b011958be": {
        "lanes": ["FAMILY_GSB1_CVD_DIV_REGIME", "FAMILY_GSB2_REGIME_SWITCHER"],
        "reason": "declared_entry_ttl_sec decision 1200 vs order/execution 660 minted two policy signatures per policy",
        # The identity-bearing rows that carry the evidence are the ones of these two divergent policy ids.
        "policy_ids": [
            "GSB1_CVD_DIVERGENCE_3M_QUIET_TOUCH_TREND_ASIDE_VIOLENT_DEEP0.75ATR|DYN_MOM_QUIET_VIOLENT_LADDER_BE_TRAIL_GB_SHOCK_FLIP_CAP1",
            "SCORE_LED_NON_TIE_PAPER_V2::GSB2_QUIET_VIOLENT_CVD_DIVERGENCE_REV_TREND_COMMITTED_FADE_MOM_OFFSET0.25ATR|DYN_REV_QUIET_VIOLENT_MOM_TREND_LADDER_BE_TRAIL_GB_SHOCK_FLIP_CAP1",
        ],
        "divergent_signatures": [
            "paper-policy-0f1ae7d625a29109ef1d", "paper-policy-d03dbfffa635dad86214",
            "paper-policy-36dfd83e3c64539966ce", "paper-policy-67401bcf07b28d29c062",
        ],
        # Latest creation timestamp over the 20 identity rows of the two divergent policy ids is 1792281300.80 (10:55:00 AEDT); bound = that +0.2 s,
        # on 18 Oct 2026, before the 12:42 AEDT deploy and before the canonical-TTL fix (#713).
        "identity_rows_max_ts": 1792281301.0,
    },
}

_TS_EXCLUDE = ("deadline", "expires", "expiry")


_NESTED_EXCLUDE = _TS_EXCLUDE + ("end_ts", "until_ts", "requested", "policy_end", "required")


def _nested_creation_ts(value: Any, found: list[float]) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if isinstance(item, (Mapping, list)):
                _nested_creation_ts(item, found)
            elif (isinstance(key, str) and key.endswith("_ts") and not any(x in key for x in _NESTED_EXCLUDE)
                  and isinstance(item, (int, float)) and not isinstance(item, bool) and 1.0e9 < float(item) < 4.0e9):
                found.append(float(item))
    elif isinstance(value, list):
        for item in value[:200]:
            _nested_creation_ts(item, found)


def identity_row_ts(row: Mapping[str, Any]) -> float | None:
    """Latest creation-type timestamp of a policy-identity row.

    Top-level *_ts first (deadlines/expiries excluded). A row with none (sparse execution/lifecycle rows) falls back to its
    nested *_ts values minus deadline/window-end keys. No usable timestamp -> None, which the caller treats as fail closed.
    """
    best = None
    for key, value in row.items():
        if not key.endswith("_ts") or any(x in key for x in _TS_EXCLUDE):
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not (1.0e9 < float(value) < 4.0e9):
            continue
        best = float(value) if best is None or float(value) > best else best
    if best is not None:
        return best
    found: list[float] = []
    _nested_creation_ts(row, found)
    return max(found) if found else None


def evaluate(present_ids: Iterable[str], identity_rows: Iterable[Mapping[str, Any]],
             listed: Mapping[str, Mapping[str, Any]] | None = None) -> dict[str, Any]:
    """Return {accepted: [...ids], rejected: [{episode_id, reason, ...}], episodes: [...evidence for accepted]}."""
    listed = LEGACY_POLICY_IDENTITY_EPISODES if listed is None else listed
    present = {str(i) for i in present_ids if str(i) in listed}
    newest: dict[str, float | None] = {i: None for i in present}
    rows_seen: dict[str, int] = {i: 0 for i in present}
    # Sparse rows (some execution/lifecycle rows carry no timestamp at all) inherit the newest dated timestamp of the rows
    # that share their event_id; a row with no date and no dated sibling fails closed.
    relevant: list[tuple[str, Mapping[str, Any], float | None]] = []
    by_event: dict[tuple[str, str], float] = {}
    for row in identity_rows:
        ep = str(row.get("episode_id") or "")
        if ep not in present or not row.get("policy_signature"):
            continue
        if str(row.get("policy_id") or "") not in set(listed[ep].get("policy_ids") or ()):
            continue
        ts = identity_row_ts(row)
        relevant.append((ep, row, ts))
        event = str(row.get("event_id") or "")
        if ts is not None and event:
            by_event[(ep, event)] = max(ts, by_event.get((ep, event), 0.0))
    for ep, row, ts in relevant:
        rows_seen[ep] += 1
        if ts is None:
            ts = by_event.get((ep, str(row.get("event_id") or "")))
        if ts is None:
            newest[ep] = float("inf")  # an identity row we cannot date: fail closed
        elif newest[ep] is None or ts > newest[ep]:
            newest[ep] = ts
    accepted, rejected, episodes = [], [], []
    for ep in sorted(present):
        bound = float(listed[ep]["identity_rows_max_ts"])
        got = newest[ep]
        if got is None:
            rejected.append({"episode_id": ep, "reason": "NO_DATED_IDENTITY_ROWS_FOUND"})
        elif got > bound:
            rejected.append({"episode_id": ep, "reason": "IDENTITY_ROWS_NEWER_THAN_EVIDENCE_BOUND",
                             "newest_identity_row_ts": got, "evidence_bound_ts": bound})
        else:
            accepted.append(ep)
            episodes.append({"episode_id": ep, "identity_rows": rows_seen[ep], "newest_identity_row_ts": got,
                             "evidence_bound_ts": bound, "lanes": list(listed[ep].get("lanes") or []),
                             "reason": listed[ep].get("reason")})
    return {"schema": SCHEMA, "accepted": accepted, "rejected": rejected, "episodes": episodes}
