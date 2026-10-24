"""R0 countability: EXIT_LAG_REGRESSION / EXIT_LAG_UNMEASURED (RK PASS 24 Oct item 7, RK ruling 22:32 AEDT).

Lag measure (RK ruling, applied exactly):
    lag = exit_decision_ts - exit_trigger_receive_ts
where exit_trigger_receive_ts is the trigger row's own collector receive_ts (receive_ts_source == "WS_FRAME"), on the
Fly clock. The whole-second row s and the wall-clock trigger stamp are NEVER used as the trigger time for lag.

Scope: exits at or after the 346ef2bf deploy (Fly machine update 2026-10-24 08:20:43 UTC = 19:20:43 AEDT, epoch
1792830043), keyed on exit fill (else decision) time. Pre-deploy rows are untouched.
  * receive_ts missing / not WS_FRAME, or no decision ts  -> EXIT_LAG_UNMEASURED: non-countable, never counts toward
    (and breaks) the arm gate. Trade-tape triggers (exchange stops) carry only a whole-second recv_s, so they are
    UNMEASURED by construction.
  * lag > OLD_P95_S -> EXIT_LAG_REGRESSION: non-countable.
  * LEGACY_REGRESSION_IDS (the 16 exits between the deploy and the 21:30 AEDT read) stay EXIT_LAG_REGRESSION whatever
    they would measure; the CSV is never rewritten (the reason is applied on read).

Threshold: OLD_P95_S = 0.062 s stays. Its basis was the old measure (decision - sub-second trigger ts, pre-deploy
current-epoch rows, 995 R17 ids excluded, n = 166; 0.082 s with them, n = 1131). Recomputing it on the receive_ts
measure is NOT possible: the Fly book files (bfx_book_10l_1s.jsonl .. .5, read-only 24 Oct 22:40 AEDT) carry
receive_ts / WS_FRAME only from s = 1792830051, 8 s AFTER the deploy, so no pre-deploy exit has a trigger-row
receive_ts (n = 0). RK: the threshold may only loosen on that evidence, so it stays 0.062 s.
"""
from __future__ import annotations

import ast
import math
from typing import Any, Iterable, Optional

REASON = "EXIT_LAG_REGRESSION"
UNMEASURED = "EXIT_LAG_UNMEASURED"
RECEIVE_SOURCE = "WS_FRAME"
DEPLOY_TS = 1792830043.0          # 346ef2bf on Fly: 2026-10-24T08:20:43Z
OLD_P95_S = 0.062                 # pre-deploy p95 (old measure, n=166, excl. R17 995). Not recomputable on receive_ts.
OLD_P95_S_INCL_R17 = 0.082        # same, all 1131 pre-deploy rows (reported, not used)
OLD_P95_N = 166
RECEIVE_TS_FIRST_S = 1792830051   # first book row with a WS_FRAME receive_ts on Fly (after DEPLOY_TS)
RECOMPUTED_P95 = {"value_s": None, "n": 0, "reason": "NO_PRE_DEPLOY_RECEIVE_TS (first WS_FRAME row s=1792830051)"}
LEGACY_REGRESSION_IDS = frozenset((
    "rnd-ed34ebc12d46", "rnd-0740577637e4", "rnd-7c2e9be15799", "rnd-1b58694c7fbd", "rnd-27d413229688",
    "gs6-fa6b47417611", "gb2-ae158ae85b19", "gs6-818ee460f682", "gb2-975899b9f2d0", "rnd-b83d097fee56",
    "rnd-d69837b51d4e", "gs1-c665cd45e611", "gb2-4006a5f3e88a", "rnd-e378e885ff55", "gb1-8dcc088b3560",
    "rnd-8547b9c6c898"))
RECEIVE_KEYS = ("exit_trigger_row_s", "exit_trigger_receive_ts", "exit_trigger_receive_ts_source")

OK, REGRESSION, UNMEASURED_STATUS, PRE_DEPLOY = "OK", "REGRESSION", "UNMEASURED", "PRE_DEPLOY"


def _num(v: Any) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) and f > 0 else None


def _events(row: dict) -> dict:
    ev = row.get("event_timestamps")
    if isinstance(ev, dict):
        return ev
    if isinstance(ev, str) and ev.strip().startswith("{"):
        try:
            v = ast.literal_eval(ev)
            return v if isinstance(v, dict) else {}
        except (ValueError, SyntaxError):
            return {}
    return {}


def _get(row: dict, ev: dict, *keys):
    for k in keys:
        for src in (ev, row):
            v = src.get(k)
            if v not in (None, ""):
                return v
    return None


def decision_fill(row: dict):
    ev = _events(row)
    return (_num(_get(row, ev, "exit_decision_ts", "exit_trigger_eval_ts")),
            _num(_get(row, ev, "exit_fill_ts", "exit_ts")))


def trigger_receive_ts(row: dict) -> Optional[float]:
    """The trigger row's own WS_FRAME receive_ts, or None (never the row s or a wall-clock stamp)."""
    ev = _events(row)
    if str(_get(row, ev, "exit_trigger_receive_ts_source") or "") != RECEIVE_SOURCE:
        return None
    return _num(_get(row, ev, "exit_trigger_receive_ts"))


def lag_s(row: dict) -> Optional[float]:
    if not isinstance(row, dict):
        return None
    rx, (dec, _) = trigger_receive_ts(row), decision_fill(row)
    return round(dec - rx, 6) if rx and dec else None


def classify(row: Any) -> str:
    if not isinstance(row, dict):
        return PRE_DEPLOY
    dec, fill = decision_fill(row)
    since = fill or dec
    if str(row.get("trade_id") or "") in LEGACY_REGRESSION_IDS:
        return REGRESSION
    if not since or since < DEPLOY_TS:
        return PRE_DEPLOY
    lag = lag_s(row)
    if lag is None:
        return UNMEASURED_STATUS
    return REGRESSION if lag > OLD_P95_S else OK


def reasons(row: Any) -> list:
    c = classify(row)
    return [REASON] if c == REGRESSION else [UNMEASURED] if c == UNMEASURED_STATUS else []


def is_regression(row: Any) -> bool:
    return classify(row) == REGRESSION


def arm_gate(rows: Iterable[dict], required: Optional[int] = None) -> dict:
    """CONSECUTIVE post-deploy exits (newest backwards, by fill time) under OLD_P95_S on the receive_ts measure.
    A REGRESSION or UNMEASURED exit ends the run (it never counts toward the gate). Pure."""
    post = []
    for r in rows or ():
        c = classify(r)
        if c == PRE_DEPLOY:
            continue
        dec, fill = decision_fill(r)
        post.append((fill or dec or 0.0, str(r.get("trade_id") or ""), c, lag_s(r)))
    post.sort()
    run, breaker = 0, None
    for ts, tid, c, lag in reversed(post):
        if c != OK:
            breaker = {"trade_id": tid, "status": c, "lag_s": lag, "fill_ts": ts}
            break
        run += 1
    counts = {s: sum(1 for p in post if p[2] == s) for s in (OK, REGRESSION, UNMEASURED_STATUS)}
    out = {"schema": "exit_lag_arm_gate_v1", "consecutive_under_threshold": run, "threshold_s": OLD_P95_S,
           "measure": "exit_decision_ts - exit_trigger_receive_ts (WS_FRAME)", "post_deploy_exits": len(post),
           "counts": counts, "run_broken_by": breaker,
           "last": [{"trade_id": t, "status": c, "lag_s": l, "fill_ts": ts} for ts, t, c, l in post[-20:]],
           "rule": rule()}
    if required is not None:
        out.update(required=int(required), armable=run >= int(required))
    return out


def rule() -> dict:
    return {"reason": REASON, "unmeasured_reason": UNMEASURED, "deploy_ts": DEPLOY_TS, "old_p95_s": OLD_P95_S,
            "old_p95_n": OLD_P95_N, "old_p95_s_incl_r17": OLD_P95_S_INCL_R17, "recomputed_p95": RECOMPUTED_P95,
            "lag": "exit_decision_ts - exit_trigger_receive_ts", "receive_ts_source": RECEIVE_SOURCE,
            "legacy_regression_ids": sorted(LEGACY_REGRESSION_IDS)}
