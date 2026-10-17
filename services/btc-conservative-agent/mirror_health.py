"""Exchange-mirror health: alert + arm block (pure, no I/O).

Why: after the 2026-10-17 reset the website fly-view stopped listing the
known Bitfinex account (it had never been armed, so no relayArmedAt /
relayLastTransition), the mirror showed 0 accounts and nothing alerted.

evaluate() compares the accounts the bot has EVER seen (transition log plus the
optional LIVE_COPY_KNOWN_ACCOUNTS env) with what the website lists now.
"""
from __future__ import annotations

from typing import Any, Iterable

SCHEMA = "mirror_health_v1"
NULL_HEARTBEAT_ALERT_SEC = 600.0
MAX_VIEW_AGE_SEC = 60.0

ZERO_ACCOUNTS = "MIRROR_ZERO_ACCOUNTS"
ACCOUNT_MISSING = "MIRROR_ACCOUNT_MISSING"
HEARTBEAT_NULL = "MIRROR_HEARTBEAT_NULL_PAUSED"


def known_accounts_from_rows(rows: Iterable[dict] | None, extra: str = "") -> list[str]:
    out = {str(r.get("account")) for r in (rows or []) if isinstance(r, dict) and r.get("account")}
    out.update(x.strip() for x in str(extra or "").split(",") if x.strip())
    return sorted(out)


# Pausing CLEARS relayArmedAt on the website (applyInstanceDashboardPatch), so armed_at alone can never prove an
# account was armed once. Evidence of a past arming: the website's last_transition (STARTED / SAFETY_PAUSED) or a
# SYSTEM pause in pause_history, or Fly's own transition log having observed the account ARMED. A plain USER STOPPED /
# PAUSED transition is NOT evidence (the signed PAUSE re-record of a never-armed account writes exactly that).
ARMED_EVIDENCE_ACTIONS = frozenset({"ARMED", "OBSERVED_ARMED", "STARTED", "SAFETY_PAUSED"})


def ever_armed_from_rows(rows: Iterable[dict] | None) -> list[str]:
    return sorted({str(r.get("account")) for r in (rows or []) if isinstance(r, dict) and r.get("account")
                   and str(r.get("action") or "").upper() in ARMED_EVIDENCE_ACTIONS})


def _account_ever_armed(a: dict, history_armed: set) -> bool:
    if a.get("armed") or a.get("armed_at") or str(a.get("account")) in history_armed:
        return True
    tr = a.get("last_transition")
    if isinstance(tr, dict) and str(tr.get("action") or "").upper() in ARMED_EVIDENCE_ACTIONS:
        return True
    return any(isinstance(p, dict) and str(p.get("actor") or "").upper() == "SYSTEM"
               for p in (a.get("pause_history") or []))


def evaluate(known: Iterable[str], web: dict | None, now: float, state: dict,
             history_armed: Iterable[str] = ()) -> dict:
    """``state`` is a caller-owned dict that remembers when the heartbeat first went null."""
    web = web or {}
    known = sorted({str(k) for k in (known or []) if k})
    causes: list[str] = []
    reachable = bool(web.get("reachable"))
    listed_rows = [a for a in (web.get("accounts") or []) if isinstance(a, dict) and a.get("account")]
    listed = sorted({str(a["account"]) for a in listed_rows})
    missing = [k for k in known if k not in listed]
    hb = web.get("executor_heartbeat_age_sec")
    try:
        hb = float(hb) if hb is not None else None
    except (TypeError, ValueError):
        hb = None
    any_armed = any(bool(a.get("armed")) for a in listed_rows)
    # A PAUSED account that was NEVER armed has no executor loop, so a null heartbeat is its normal state: only an
    # account that was armed at some point (armed_at set) is expected to heartbeat while paused.
    hist = {str(x) for x in (history_armed or ())}
    ever_armed = any(_account_ever_armed(a, hist) for a in listed_rows)
    if reachable:
        if known and not listed:
            causes.append(ZERO_ACCOUNTS)
        elif missing:
            causes.append(ACCOUNT_MISSING)
        if hb is None and ever_armed and not any_armed:
            since = state.setdefault("hb_null_since", now)
            if now - float(since) > NULL_HEARTBEAT_ALERT_SEC:
                causes.append(HEARTBEAT_NULL)
        else:
            state.pop("hb_null_since", None)
    return {
        "schema": SCHEMA,
        "reachable": reachable,
        "known_accounts": known,
        "listed_accounts": listed,
        "missing_accounts": missing,
        "executor_heartbeat_age_sec": hb,
        "ever_armed": ever_armed,
        "fetched_at_ts": web.get("fetched_at_ts"),
        "hb_null_since": state.get("hb_null_since"),
        "causes": causes,
        "alert": bool(causes),
        "verdict": "RED" if causes else "GREEN",
    }


def arm_block_reason(health: dict | None, flat_audit: dict | None, now: float | None = None) -> str | None:
    """Re-arm stays blocked until the mirror lists every known account AND the bot has a fresh authoritative flat
    check. A heartbeat is required only when a listed account was armed before (a never-armed paused account has none,
    so requiring one would make arming impossible). ``now`` enables the view-freshness check."""
    h = health or {}
    known = h.get("known_accounts") or []
    if not known:
        return None  # nothing was ever mirrored: nothing to restore
    if not h.get("reachable"):
        return "MIRROR_UNREACHABLE"
    if now is not None:
        fetched = h.get("fetched_at_ts")
        try:
            stale = fetched is None or now - float(fetched) > MAX_VIEW_AGE_SEC
        except (TypeError, ValueError):
            stale = True
        if stale:
            return "MIRROR_VIEW_STALE"
    if h.get("causes"):
        return str(h["causes"][0])
    if h.get("missing_accounts"):
        return ACCOUNT_MISSING
    if h.get("ever_armed") and h.get("executor_heartbeat_age_sec") is None:
        return "MIRROR_HEARTBEAT_NULL"
    a = flat_audit or {}
    if not (a.get("authoritative") and a.get("fresh") and a.get("flat")):
        return "LIVE_FLAT_CHECK_UNCONFIRMED"
    return None
