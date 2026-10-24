"""RK (24 Oct 2026, .dockerignore fix PR): exchange_mirror.live_fences health, per account.

The relay publishes dashboardState.relayExchangeMirror.live_fences (schema relay_live_fences_v1) per Bitfinex account.
If it is ok:false (the LiveEntryFence read failed), unreadable (not an object / wrong schema / no boolean `blocked`) or
missing on ANY account, the relay's entry-fence state is unknown, so:
  * the arm gate BLOCKs (Fly _live_guardian_arm_block + GET /api/live-copy/arm-gate `live_fences_block`, which the relay
    arm paths refuse on),
  * the exchange-health summary / lifecycle gaps go non-GREEN (RED for an armed account, AMBER otherwise),
  * an alert (lifecycle alert LIVE_FENCES_UNREADABLE) is raised.
Pure, no I/O, never mutates its input.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping

LIVE_FENCES_SCHEMA = "relay_live_fences_v1"
ALERT_CODE = "LIVE_FENCES_UNREADABLE"
ARM_BLOCK_PREFIX = "LIVE_FENCES_UNREADABLE"

CAUSE_NO_MIRROR = "NO_EXCHANGE_MIRROR"
CAUSE_MISSING = "LIVE_FENCES_MISSING"
CAUSE_NOT_OK = "LIVE_FENCES_OK_FALSE"
CAUSE_UNREADABLE = "LIVE_FENCES_UNREADABLE"
CAUSE_NO_CREDENTIALS = "NO_CREDENTIALS_UNVERIFIED"
# RK (24 Oct 16:42): an account with no exchange credentials is UNVERIFIED and blocked, never ok (relay mirror error text)
_NO_CRED_MARKERS = ("credentials-missing", "no_credentials", "credentials unavailable")


def live_fences_cause(mirror: Any) -> str | None:
    """None when the account's live_fences view is readable; else the failure cause."""
    if not isinstance(mirror, Mapping):
        return CAUSE_NO_MIRROR
    _err = " ".join(str(mirror.get(k) or "") for k in ("error", "error_code", "credentials")).lower()
    if mirror.get("credentials") is False or mirror.get("has_credentials") is False \
            or any(x in _err for x in _NO_CRED_MARKERS):
        return CAUSE_NO_CREDENTIALS
    if "live_fences" not in mirror or mirror.get("live_fences") is None:
        return CAUSE_MISSING
    lf = mirror.get("live_fences")
    if not isinstance(lf, Mapping):
        return CAUSE_UNREADABLE
    if lf.get("ok") is False:
        return CAUSE_NOT_OK
    if lf.get("ok") is not True:  # HM 24 Oct: pass only on explicit ok:true, never on a missing error field
        return CAUSE_UNREADABLE
    if lf.get("schema") != LIVE_FENCES_SCHEMA or not isinstance(lf.get("blocked"), bool):
        return CAUSE_UNREADABLE
    return None


def account_problems(accounts: Iterable[Any] | None) -> list[dict]:
    """[{account, cause, armed, error}] for every account whose live_fences is not readable (website account rows)."""
    out = []
    for a in accounts or []:
        if not isinstance(a, Mapping):
            continue
        m = a.get("exchange_mirror")
        cause = live_fences_cause(m)
        if cause is None:
            continue
        lf = m.get("live_fences") if isinstance(m, Mapping) else None
        err = lf.get("error") if isinstance(lf, Mapping) else None
        out.append({"account": str(a.get("account")), "cause": cause, "armed": bool(a.get("armed")),
                    "error": str(err)[:160] if err else None})
    return out


def arm_block(website_view: Mapping[str, Any] | None) -> str | None:
    """Arm refusal code, or None. Fails closed: no website view / no accounts at all is itself a block."""
    v = website_view if isinstance(website_view, Mapping) else {}
    accts = v.get("accounts")
    if not isinstance(accts, list) or not any(isinstance(a, Mapping) for a in accts):
        return f"{ARM_BLOCK_PREFIX}:NO_ACCOUNTS_VIEW"
    probs = account_problems(accts)
    if not probs:
        return None
    return f"{ARM_BLOCK_PREFIX}:" + ",".join(f"{p['account']}={p['cause']}" for p in probs)


def mirror_alerts(exchange: Mapping[str, Mapping[str, Any]] | None, armed_accounts: Iterable[str] = ()) -> list[dict]:
    """Lifecycle alerts for live_copy_lifecycle.alerts (exchange = {account: mirror}). RED if armed, else AMBER."""
    armed = {str(x) for x in armed_accounts or ()}
    out = []
    for acct, m in (exchange or {}).items():
        cause = live_fences_cause(m)
        if cause is not None:
            out.append({"code": ALERT_CODE, "severity": "RED" if str(acct) in armed else "AMBER",
                        "account": str(acct), "cause": cause})
    return out


def summary(exchange: Mapping[str, Mapping[str, Any]] | None, armed_accounts: Iterable[str] = ()) -> dict:
    """Exchange-health block: {verdict, accounts:{acct:{ok,cause}}, gaps:[...]}. Never GREEN with a problem."""
    al = mirror_alerts(exchange, armed_accounts)
    verdict = "RED" if any(a["severity"] == "RED" for a in al) else ("AMBER" if al else "GREEN")
    if not exchange:
        verdict, al = "AMBER", [{"code": ALERT_CODE, "severity": "AMBER", "account": None, "cause": CAUSE_NO_MIRROR}]
    return {"verdict": verdict,
            "accounts": {str(a): {"ok": live_fences_cause(m) is None, "cause": live_fences_cause(m)}
                         for a, m in (exchange or {}).items()},
            "gaps": al}
