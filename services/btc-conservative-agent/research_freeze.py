"""21-day research freeze (FREEZE21B-20261004): one declared epoch, no tile or reset churn until day 21.

Owner-approved 2026-10-04 15:02 AEDT and re-declared 17:53/17:54 AEDT as FREEZE21B with
eleven tiles (diagnostics/FREEZE21-PROTOCOL-20261004.md, "FREEZE21B" section).
PHASE02 (2026-10-07, owner-ordered, CODE_OVERRIDE) retired five losers, leaving eight tiles.
PHASE03 (2026-10-07, owner-ordered build) appends three research tiles (GS-07, the Danish
regime router and the fade pool) as default-OFF mid-epoch additions.
The freeze window is ``[started_at, started_at + 21 days)`` of the data-epoch
manifest whose ``epoch_id`` is :data:`FREEZE_DATA_EPOCH_ID` (opened by the
first boot with ``DATA_EPOCH_ID`` set to it). Inside the window:

* every fresh-collection / epoch reset path refuses with
  ``RESEARCH_FREEZE_ACTIVE`` (HTTP 409), except during the first
  :data:`FREEZE_OPENING_SEC` (status ``OPENING``), which is the deploy
  workflow's one guarded boundary reset (``clean-epoch-reset-execute``,
  ``RESET-AT-BOUNDARY``) that starts the freeze epoch clean;
* the tile toggle may turn a frozen-roster tile back ON, but turning one OFF
  (a kill-rule action) needs the override;
* ``clean_epoch_wipe.py --pre-start execute`` refuses;
* CI (``test_research_freeze.py``) fails any registry change - roster, order,
  signature or stack version - and any ``DATA_EPOCH_ID`` change.

Mid-epoch additions (:data:`MID_EPOCH_ADDITIONS`) are the one documented
exception: owner-ordered paper tiles appended AFTER the frozen roster in the
same data epoch. They never alter the frozen tiles - CI proves the frozen
roster's signature is unchanged by recomputing it over the first
``len(FREEZE_ROSTER)`` tiles (``combo_pathway_config.
frozen_roster_registry_signature``) - and the full registry (frozen +
additions) is pinned separately in :data:`MID_EPOCH_REGISTRY_SIGNATURES`.
Each addition's research window starts at the deploy of the revision that
registers it (its first boot), not at the freeze epoch start. Additions get
the frozen tiles' toggle rules (ON always allowed, OFF needs the override).

The documented override is the only way through. At runtime it is either the
request body field ``freeze_override = {"confirmation":
"BREAK_21_DAY_RESEARCH_FREEZE", "reason": "<why, >= 10 chars>"}`` or the
environment pair ``RESEARCH_FREEZE_OVERRIDE=BREAK_21_DAY_RESEARCH_FREEZE`` and
``RESEARCH_FREEZE_OVERRIDE_REASON``. Kill-rule toggles use the reason form
``KILL_RULE:<lane>:<rule>``. In code it is a non-empty :data:`CODE_OVERRIDE`
(who approved, when, why), which also marks the freeze BROKEN for the record.
Ending the freeze early or lifting it after day 21 is a code change setting
:data:`FREEZE_STATUS` to ``"LIFTED"``.

Pure module: no I/O beyond reading the environment, no orders, no toggles.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Mapping, Optional

SCHEMA = "research_freeze_v1"
FREEZE_ID = "FREEZE21B-20261004"
FREEZE_DATA_EPOCH_ID = "ce-20261004-v31-freeze21b"
FREEZE_DAYS = 21
FREEZE_SEC = FREEZE_DAYS * 86400
# "ACTIVE" = the guard applies inside the window; "LIFTED" = owner ended it.
FREEZE_STATUS = "ACTIVE"
FREEZE_DECLARED_UTC = "2026-10-04T06:53:00Z"
FREEZE_APPROVED_BY = ("Danish (owner), 2026-10-04 15:02 AEDT, SYSTEM-REVIEW-20261004; re-declared as FREEZE21B "
                      "with the GS-20261004-01..04 and B1..B3 paper tiles, 17:53/17:54 AEDT; PHASE02 "
                      "retirement (2026-10-07) removed H-B, GS-02/03/04/05 with CODE_OVERRIDE")
# The registry frozen for 21 days (combo_pathway_config); CI compares these.
FREEZE_REGISTRY_VERSION = "v31-freeze21b-8t-v14"
FREEZE_ROSTER = (
    "FAMILY_COMMITTED_FADE_TAKER_90",
    "FAMILY_PREMIUM_REVERSION_60M",
    "FAMILY_RANDOM_CONTROL_TAKER_90",
    "FAMILY_GS01_XV_PREMIUM_ATR_TP",
    "FAMILY_GSB1_CVD_DIV_REGIME",
    "FAMILY_GSB2_REGIME_SWITCHER",
    "FAMILY_GSB3_COMMITTED_FADE_REGIME",
)
# active_tile_registry_signature() per SCORE_LED_PAPER_RESEARCH_ENABLED mode:
# Fly runs score-led ("1"); the hypothesis mode is the unset/laptop default.
FREEZE_REGISTRY_SIGNATURES = {
    "score_led": "9035474280d31626ce8365a444c9296442d8e41622c47b55a9f88c18fa77279b",
    "hypothesis": "671354b107f2f3cfbfadc70a1c923c5ec87146b4303acc85ea3316302b1c1416",
}
FREEZE_REGISTRY_SIGNATURE = FREEZE_REGISTRY_SIGNATURES["score_led"]  # the deployed Fly identity
# Owner-ordered mid-epoch additions (appended tiles, same epoch; see module doc).
MID_EPOCH_ADDITIONS = (
    {"lane": "FAMILY_GS06_COMMITTED_FADE_ATR_TP", "tile_number": 8, "hypothesis_id": "GS-20261005-06",
     "approved_by": "Danish (owner), 2026-10-05 ~20:15 AEDT (design ask) and build/deploy order",
     "spec": "diagnostics/GS05-GS06-TILE-SPECS-CORRECTED-20261005.md (supersedes the -20261005 exits)",
     "window_start": "DEPLOY_OF_REGISTERING_REVISION"},
    # PHASE03 (2026-10-07, owner-ordered build, default OFF, paper only, relay-ineligible).
    {"lane": "FAMILY_GS07_FAST_PREMIUM_FADE", "tile_number": 9, "hypothesis_id": "GS-20261007-07",
     "approved_by": "Danish (owner), 2026-10-07 PHASE03 build order (build only; no deploy/arm/relay)",
     "spec": "operator transcript (Grok Strategist Tile 14 GS-07 fast premium fade)",
     "window_start": "DEPLOY_OF_REGISTERING_REVISION"},
    {"lane": "FAMILY_DANISH_REGIME_ROUTER", "tile_number": 10, "hypothesis_id": "GS-20261007-DNR",
     "approved_by": "Danish (owner), 2026-10-07 PHASE03 build order (build only; no deploy/arm/relay)",
     "spec": "diagnostics/DANISH-REGIME-TILE-DESIGN-20261007.md",
     "window_start": "DEPLOY_OF_REGISTERING_REVISION"},
    {"lane": "FAMILY_FADE_POOL", "tile_number": 11, "hypothesis_id": "GS-20261007-FDP",
     "approved_by": "Danish (owner), 2026-10-07 PHASE03 build order (build only; no deploy/arm/relay)",
     "spec": "operator transcript (fade-pool proposal; Grok Strategist FADE-POOL-REANALYSIS-20261007)",
     "window_start": "DEPLOY_OF_REGISTERING_REVISION"},
    # Tile 14 · GS-07 V07 (owner 2026-10-10 build order; default OFF, paper only, relay-ineligible).
    {"lane": "FAMILY_GS07_V07_PREMIUM_FADE_60M", "tile_number": 12, "hypothesis_id": "GS-20261010-07V07",
     "approved_by": "Danish (owner), 2026-10-10 backlog batch: build Tile 14 GS-07 V07, then paper ON",
     "spec": "audit/TILE14-TILE15-DESIGN-20261008.md §2 (V07)",
     "window_start": "DEPLOY_OF_REGISTERING_REVISION"},
)
MID_EPOCH_ADDITION_ROSTER = tuple(item["lane"] for item in MID_EPOCH_ADDITIONS)
# active_tile_registry_signature() of the full registry (frozen roster + additions).
# 2026-10-09 (owner step-2 order): the Danish router and Fade pool thesis cut is the
# registered CONDITIONAL_EARLY_CUT_12_5M (fires only while MFE <= +2 bp); their
# profiles gained cut_max_peak_bp, so both tiles' policy signatures and this pin moved.
# The frozen roster's signature is unchanged.
MID_EPOCH_REGISTRY_SIGNATURES = {
    "score_led": "8e1a6bb79fd537944c6c334a1eee7503423167851ad5cece1d5eb6660ff83a03",
    "hypothesis": "0c7b0970fd1c7229838ddaa034d0d0b78ec62405249944574aeaa0d58bd0c900",
}
OVERRIDE_CONFIRMATION = "BREAK_21_DAY_RESEARCH_FREEZE"
OVERRIDE_ENV = "RESEARCH_FREEZE_OVERRIDE"
OVERRIDE_REASON_ENV = "RESEARCH_FREEZE_OVERRIDE_REASON"
MIN_REASON_CHARS = 10
# Same 60-minute window as scripts/fly_reset_plan_live_gates.py WINDOW_SEC: the
# boundary reset at the opening of the freeze epoch is part of the protocol.
FREEZE_OPENING_SEC = 3600
# Set to {"approved_by": ..., "approved_utc": ..., "reason": ...} only with the
# owner's explicit approval to change the frozen registry or epoch in code.
CODE_OVERRIDE: Optional[Mapping[str, str]] = {
    "approved_by": "Danish (owner), PHASE02 tile surgery, 2026-10-07",
    "approved_utc": "2026-10-07T04:36:00Z",
    "reason": ("Retire the five freeze21b losers (H-B, GS-05, GS-02, GS-04, GS-03) in one atomic "
               "registry transaction; eight paper-only relay-ineligible tiles remain"),
}

ERROR = "RESEARCH_FREEZE_ACTIVE"
NOT_STARTED, OPENING, ACTIVE, COMPLETE, LIFTED, EPOCH_MISMATCH = (
    "NOT_STARTED", "OPENING", "ACTIVE", "COMPLETE", "LIFTED", "EPOCH_MISMATCH")
GUARDED_STATES = frozenset({OPENING, ACTIVE, EPOCH_MISMATCH})

ACTION_RESET = "RESET"
ACTION_TILE_OFF = "TILE_OFF"
ACTION_TILE_ON = "TILE_ON"
ACTION_PRE_START_WIPE = "PRE_START_WIPE"


def _iso(ts: Optional[float]) -> Optional[str]:
    if ts is None:
        return None
    return datetime.fromtimestamp(float(ts), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def window(manifest: Optional[Mapping[str, Any]]) -> tuple[Optional[float], Optional[float]]:
    """(start, end) epoch seconds of the freeze, or (None, None) before the freeze epoch opens."""
    if not manifest or str(manifest.get("epoch_id") or "") != FREEZE_DATA_EPOCH_ID:
        return None, None
    try:
        start = float(manifest["started_at_ts"])
    except (KeyError, TypeError, ValueError):
        return None, None
    return start, start + FREEZE_SEC


def freeze_status(manifest: Optional[Mapping[str, Any]], now: float,
                  configured_epoch: Optional[str] = None) -> dict:
    """Public freeze state; ``configured_epoch`` is the running DATA_EPOCH_ID (None = unknown)."""
    start, end = window(manifest)
    if FREEZE_STATUS != "ACTIVE":
        status = LIFTED
    elif configured_epoch is not None and configured_epoch != FREEZE_DATA_EPOCH_ID:
        status = EPOCH_MISMATCH
    elif start is None:
        status = NOT_STARTED
    elif now < start + FREEZE_OPENING_SEC:
        status = OPENING
    elif now < end:
        status = ACTIVE
    else:
        status = COMPLETE
    day = int((now - start) // 86400) + 1 if start is not None and now >= start else None
    return {
        "schema": SCHEMA,
        "freeze_id": FREEZE_ID,
        "status": status,
        "guarded": status in GUARDED_STATES,
        "epoch_id": FREEZE_DATA_EPOCH_ID,
        "configured_epoch": configured_epoch,
        "started_at_utc": _iso(start),
        "ends_at_utc": _iso(end),
        "day": max(1, min(day, FREEZE_DAYS)) if day is not None and status in (OPENING, ACTIVE) else day,
        "days": FREEZE_DAYS,
        "seconds_remaining": round(end - now) if status in (OPENING, ACTIVE) else None,
        "roster": list(FREEZE_ROSTER),
        "mid_epoch_additions": [dict(item) for item in MID_EPOCH_ADDITIONS],
        "mid_epoch_registry_signatures": dict(MID_EPOCH_REGISTRY_SIGNATURES),
        "registry_version": FREEZE_REGISTRY_VERSION,
        "registry_signature": FREEZE_REGISTRY_SIGNATURE,
        "registry_signatures": dict(FREEZE_REGISTRY_SIGNATURES),
        "code_override": dict(CODE_OVERRIDE) if CODE_OVERRIDE else None,
        "override_form": {"confirmation": OVERRIDE_CONFIRMATION, "reason": f">= {MIN_REASON_CHARS} chars",
                          "env": [OVERRIDE_ENV, OVERRIDE_REASON_ENV],
                          "kill_rule_reason": "KILL_RULE:<lane>:<rule>"},
    }


def resolve_override(override: Any = None, env: Optional[Mapping[str, str]] = None) -> Optional[dict]:
    """A valid override from the request body or the environment, else None."""
    env = os.environ if env is None else env
    candidates = []
    if isinstance(override, Mapping):
        candidates.append(("request", override.get("confirmation"), override.get("reason")))
    candidates.append(("env", env.get(OVERRIDE_ENV), env.get(OVERRIDE_REASON_ENV)))
    for source, confirmation, reason in candidates:
        reason = str(reason or "").strip()
        if str(confirmation or "").strip() == OVERRIDE_CONFIRMATION and len(reason) >= MIN_REASON_CHARS:
            return {"source": source, "reason": reason[:500]}
    return None


def check(action: str, manifest: Optional[Mapping[str, Any]], now: float, *,
          override: Any = None, configured_epoch: Optional[str] = None, lane: Optional[str] = None,
          env: Optional[Mapping[str, str]] = None) -> dict:
    """Verdict for one guarded action: ``allowed`` plus the freeze state and any override used."""
    state = freeze_status(manifest, now, configured_epoch)
    verdict = {"allowed": True, "action": action, "lane": lane, "freeze": state, "override": None}
    if not state["guarded"]:
        return verdict
    if action == ACTION_TILE_ON and (lane in FREEZE_ROSTER or lane in MID_EPOCH_ADDITION_ROSTER):
        return verdict
    if state["status"] == OPENING and action in (ACTION_RESET, ACTION_PRE_START_WIPE):
        verdict["opening_boundary_reset"] = True
        return verdict
    used = resolve_override(override, env)
    if used:
        verdict["override"] = used
        return verdict
    verdict.update({
        "allowed": False,
        "error": ERROR,
        "summary": (f"{FREEZE_ID} is {state['status']} until {state['ends_at_utc'] or 'the freeze epoch ends'}: "
                    f"{action} refused. Override: freeze_override={{\"confirmation\": \"{OVERRIDE_CONFIRMATION}\", "
                    f"\"reason\": \"...\"}} (see diagnostics/FREEZE21-PROTOCOL-20261004.md)"),
    })
    return verdict
