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
        "identity_row_count": 20,
    },
    "episode-cfacc140c836a55a140c": {
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
        # Own per-id bound: newest of its 18 identity rows is 1792297804.03 (15:30:04 AEDT, 18 Oct); bound = that +1 s.
        # Created before the #713 deploy boot (17:42:03 AEDT = 1792305723); no global bound is shared with other ids.
        "identity_rows_max_ts": 1792297805.0,
        "identity_row_count": 18,
    },
}

# Only these keys say when an identity row was CREATED. signal_ts, deadlines, tape windows etc. are never used as a date.
CREATION_TS_KEYS = ("decision_ts", "submitted_ts", "fill_ts", "observed_ts", "created_ts")


def identity_row_ts(row: Mapping[str, Any]) -> float | None:
    """Latest creation-time stamp of a policy-identity row, from CREATION_TS_KEYS only; None if the row carries none."""
    best = None
    for key in CREATION_TS_KEYS:
        value = row.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not (1.0e9 < float(value) < 4.0e9):
            continue
        best = float(value) if best is None or float(value) > best else best
    return best


def evaluate(present_ids: Iterable[str], identity_rows: Iterable[Mapping[str, Any]],
             listed: Mapping[str, Mapping[str, Any]] | None = None) -> dict[str, Any]:
    """Return {accepted: [...ids], rejected: [{episode_id, reason, ...}], episodes: [...evidence for accepted]}."""
    listed = LEGACY_POLICY_IDENTITY_EPISODES if listed is None else listed
    present = {str(i) for i in present_ids if str(i) in listed}
    newest: dict[str, float | None] = {i: None for i in present}
    undated: set[str] = set()
    policy_ids = {i: set(listed[i].get("policy_ids") or ()) for i in present}  # hoisted out of the row loop
    rows_seen: dict[str, int] = {i: 0 for i in present}
    # Sparse rows (some execution/lifecycle rows carry no timestamp at all) inherit the newest dated timestamp of the rows
    # that share their event_id (siblings dated by CREATION_TS_KEYS only); a row with no date and no dated sibling fails closed.
    relevant: list[tuple[str, Mapping[str, Any], float | None]] = []
    by_event: dict[tuple[str, str], float] = {}
    for row in identity_rows:
        ep = str(row.get("episode_id") or "")
        if ep not in present or not row.get("policy_signature"):
            continue
        if str(row.get("policy_id") or "") not in policy_ids[ep]:
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
            undated.add(ep)  # an identity row we cannot date: fail closed
        elif newest[ep] is None or ts > newest[ep]:
            newest[ep] = ts
    accepted, rejected, episodes = [], [], []
    for ep in sorted(present):
        bound = float(listed[ep]["identity_rows_max_ts"])
        got = newest[ep]
        expected = listed[ep].get("identity_row_count")
        if ep in undated:
            rejected.append({"episode_id": ep, "reason": "UNDATED_IDENTITY_ROW"})
        elif expected is not None and rows_seen[ep] != int(expected):
            rejected.append({"episode_id": ep, "reason": "IDENTITY_ROW_COUNT_MISMATCH",
                             "identity_rows": rows_seen[ep], "expected_identity_rows": int(expected)})
        elif got is None:
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
