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

RK ruling 25 Oct (EXIT_LAG gate, + option (a)) - supersedes the 0.062 s threshold for the ARM GATE:
  * Gate scope starts at the #868 deploy: GATE_SCOPE_START_TS (25 Oct 2026 02:12 AEDT, master 78f958e5).
  * Admin / force-flat closes (paper_pnl_canon.FORCED_EXIT_REASONS) -> ADMIN_EXCLUDED: excluded from the run, still
    non-countable (FORCE_FLATTENED elsewhere), ids listed by the route.
  * Exchange-resident stop exits -> EXCHANGE_FILLED (not UNMEASURED): excluded from the timer run.
  * Any other exit without a WS_FRAME receive_ts BREAKS the run (fails closed). So does an exit with NO decision and
    NO fill/exit timestamp (UNTIMED): it can't be placed before the scope start, so it is in scope and breaks the run.
  * Threshold: never 0.062 and never a percentile of our own lag. It is REAL_EXCHANGE_THRESHOLD_S, set from the real
    Bitfinex trigger->fill time of >= 10 live exchange-resting stops (current epoch, real exchange timestamps), computed
    by HM and reviewed by RK, and recorded here with its source ids. Until then: gate = UNPROVEN, arm_eligible = False.
  * Option (a) (ruling: SHARED-TRACKER 2026-10-25 03:50 AEDT "EXIT_LAG arm gate per RK ruling" (RK verbatim text pasted there)): UNPROVEN does NOT block arming GS-01
    at $0.25 margin (GS-01 only; enforced by arm_block_reason / gs01_size_block_reason on the tile-ON and live-entry
    paths, fail closed). Instead every in-scope
    exit (GS-01 live and paper, all lanes - fail closed) is non-countable EXIT_LAG_UNPROVEN until the threshold is set.
  * Pass: our measured lag <= REAL_EXCHANGE_THRESHOLD_S across >= REQUIRED_RUN consecutive measured exits.
  * Per measured exit: whether a paper STOP fill used the FIRST full book row received at/after the trigger
    (FIRST_ROW_AT_OR_AFTER_FILL_TS, proof OK), never the decision-time row; anything else is a VIOLATION (R0).
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
# --- RK ruling 25 Oct ---
# 2026-10-24T15:12:00Z = 25 Oct 02:12 AEDT. Fly v724 (#868, exit_trigger_receive_ts stamping) was RELEASED at 02:30:20
# AEDT (flyctl releases); 02:12 is deliberately EARLIER: the extra pre-release exits can only be UNPROVEN or break
# the run (safe direction).
GATE_SCOPE_START_TS = 1792854720.0
GATE_SCOPE_REASON = ("EXIT_LAG scope starts 25 Oct 02:12 AEDT, an earlier (safe) bound than the Fly v724 (#868) release "
                     "at 02:30:20 AEDT; pre-release exits in the window are only UNPROVEN or break the run")
REQUIRED_RUN = 20
MIN_EXCHANGE_STOPS = 10
# Set ONLY after HM computes it from >= 10 real live exchange-resting stops and RK reviews it (with source ids).
REAL_EXCHANGE_THRESHOLD_S: Optional[float] = None
REAL_EXCHANGE_THRESHOLD_SOURCE_IDS: tuple = ()
UNPROVEN_REASON = "EXIT_LAG_UNPROVEN"
# Option (a), ruling: SHARED-TRACKER 2026-10-25 03:50 AEDT "EXIT_LAG arm gate per RK ruling" (RK verbatim text pasted there).
GS01_UNPROVEN_ARM_ALLOWED = True     # UNPROVEN does not block GS-01 at $0.25 (GS-01 only)
GS01_MAX_NOTIONAL_USD = 0.25         # GS-01 per-order MARGIN cap (isolated, 100x), enforced on arm + live entry
GS01_TILE_KEY = "gs1"
UNTIMED_STATUS = "UNTIMED"
ARM_BLOCK_UNREADABLE = "EXIT_LAG_GATE_UNREADABLE"
ARM_BLOCK_GS01 = "EXIT_LAG_GS01_ARM_BLOCKED"
ARM_BLOCK_NOT_ELIGIBLE = "EXIT_LAG_ARM_NOT_ELIGIBLE"
GS01_SIZE_BLOCK = "GS01_MARGIN_ABOVE_CAP"
GS01_SIZE_UNREADABLE = "GS01_MARGIN_UNREADABLE"
ADMIN_EXCLUDED, EXCHANGE_FILLED, MEASURED = "ADMIN_EXCLUDED", "EXCHANGE_FILLED", "MEASURED"
GATE_UNPROVEN, GATE_PASS, GATE_FAIL = "UNPROVEN", "PASS", "FAIL"
STOP_ROW_OK, STOP_ROW_VIOLATION, STOP_ROW_NA = "FIRST_ROW_AFTER_TRIGGER_OK", "VIOLATION", "NOT_A_STOP_FILL"
FIRST_AFTER_RULE = "FIRST_ROW_AT_OR_AFTER_FILL_TS"
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


ADMIN_EXCLUDED_REASON = "EXIT_LAG_ADMIN_EXCLUDED"


def reasons(row: Any) -> list:
    """Non-countable reasons. Rows before GATE_SCOPE_START_TS keep the 24 Oct rule (0.062 s); rows in the RK 25 Oct
    scope use the new statuses and, while the real exchange threshold is unset, EXIT_LAG_UNPROVEN (option (a))."""
    if not isinstance(row, dict):
        return []
    if str(row.get("trade_id") or "") in LEGACY_REGRESSION_IDS:
        return [REASON]
    st = gate_status(row)
    if st == PRE_DEPLOY:
        c = classify(row)
        return [REASON] if c == REGRESSION else [UNMEASURED] if c == UNMEASURED_STATUS else []
    # EXCHANGE_FILLED adds no exit-lag reason of its own: the exchange filled it, our timer did not (RK 25 Oct (3)).
    out = {ADMIN_EXCLUDED: [ADMIN_EXCLUDED_REASON], UNMEASURED_STATUS: [UNMEASURED], UNTIMED_STATUS: [UNMEASURED]}.get(st, [])
    if st == MEASURED and threshold_proven():
        lag = lag_s(row)
        if lag is None or lag > float(REAL_EXCHANGE_THRESHOLD_S):
            out = [REASON]
    return list(out) + unproven_reasons(row)


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


# ---------------------------------------------------------------------------------------------------------------------
# RK ruling 25 Oct (EXIT_LAG arm gate) + option (a)
# ---------------------------------------------------------------------------------------------------------------------
def _upper(v) -> str:
    return str(v or "").strip().upper()


def is_admin_close(row: dict) -> bool:
    from paper_pnl_canon import FORCED_EXIT_REASONS
    return _upper(row.get("exit_reason")) in FORCED_EXIT_REASONS


def is_exchange_stop(row: dict) -> bool:
    import order_model as _om
    ev = _events(row)
    if _om.is_exchange_stop_reason(row.get("exit_reason")):
        return True
    if _upper(_get(row, ev, "exit_order_class")) != "EXCHANGE_RESIDENT":
        return False
    # HM R2 (b): the class alone counts only with the bot's own provenance stamp (bot.py exchange-stop fill sets
    # exit_stop_flags=[EXCHANGE_RESIDENT, ...]; the paper race sets exit_race_lost_by). Anything else -> not excluded
    # (it stays in the run, so it can only break it: fail closed).
    flags = _get(row, ev, "exit_stop_flags")
    flags = [_upper(f) for f in flags] if isinstance(flags, (list, tuple)) else []
    return "EXCHANGE_RESIDENT" in flags or bool(_get(row, ev, "exit_race_lost_by"))


def gate_status(row: Any) -> str:
    """Per-exit status for the RK 25 Oct run: PRE_DEPLOY (before GATE_SCOPE_START_TS) / ADMIN_EXCLUDED /
    EXCHANGE_FILLED / UNMEASURED (breaks the run) / MEASURED."""
    if not isinstance(row, dict):
        return PRE_DEPLOY
    dec, fill = decision_fill(row)
    since = fill or dec
    if not since:
        return UNTIMED_STATUS   # HM R2 (b): cannot be shown pre-scope -> in scope, breaks the run (fail closed)
    if since < GATE_SCOPE_START_TS:
        return PRE_DEPLOY
    if is_admin_close(row):
        return ADMIN_EXCLUDED
    if is_exchange_stop(row):
        return EXCHANGE_FILLED
    return MEASURED if lag_s(row) is not None else UNMEASURED_STATUS


def threshold_proven() -> bool:
    return REAL_EXCHANGE_THRESHOLD_S is not None and len(REAL_EXCHANGE_THRESHOLD_SOURCE_IDS) >= MIN_EXCHANGE_STOPS


def unproven_reasons(row: Any) -> list:
    """Option (a): while the threshold is unset, every in-scope exit is non-countable for the exit-lag criterion."""
    if threshold_proven() or gate_status(row) == PRE_DEPLOY:
        return []
    return [UNPROVEN_REASON]


def stop_fill_row_check(row: dict) -> dict:
    """R0 (RK 25 Oct): a paper STOP fill must use the FIRST full book row received at/after the trigger, never the
    decision-time row. Non-stop exits -> NOT_A_STOP_FILL. Missing evidence on a stop fill -> VIOLATION (fail closed)."""
    ev = _events(row)
    reason = _upper(row.get("exit_reason"))
    rule_ = str(_get(row, ev, "exit_book_row_rule") or "")
    if "STOP" not in reason and not is_exchange_stop(row):
        return {"status": STOP_ROW_NA, "book_row_rule": rule_ or None}
    why = None
    if rule_ != FIRST_AFTER_RULE:
        why = "DECISION_TIME_OR_UNKNOWN_ROW_RULE"
    elif _get(row, ev, "exit_first_after_reason", "exit_unverified_reason"):
        why = str(_get(row, ev, "exit_first_after_reason", "exit_unverified_reason"))
    else:
        rx = _num(_get(row, ev, "exit_book_receive_ts"))
        trig = _num(_get(row, ev, "exit_trigger_ts"))
        if rx is None or trig is None:
            why = "BOOK_RECEIVE_OR_TRIGGER_TS_MISSING"
        elif rx < trig - 1e-9:
            why = "BOOK_ROW_RECEIVED_BEFORE_TRIGGER"
    return {"status": STOP_ROW_VIOLATION if why else STOP_ROW_OK, "book_row_rule": rule_ or None, "reason": why}


def real_exchange_stop_samples(chains: Iterable[dict], epoch_ts: Optional[float] = None) -> list:
    """Live exchange-resting stop fills with REAL Bitfinex timestamps: trigger print (exit_trigger_print_at_ts) and
    the exit order's own fill time (exit_fill_at_ts = mtsCreate). Anything missing is skipped, never estimated."""
    out = []
    for c in chains or ():
        if not isinstance(c, dict) or not c.get("closed"):
            continue
        if _upper(c.get("exit_order_class")) != "EXCHANGE_RESIDENT":
            continue
        trig, fill = _num(c.get("exit_trigger_print_at_ts")), _num(c.get("exit_fill_at_ts"))
        if trig is None or fill is None or fill < trig:
            continue
        if epoch_ts is not None and fill < float(epoch_ts):
            continue
        out.append({"id": str(c.get("correlation_id") or ""), "trigger_ts": trig, "fill_ts": fill,
                    "trigger_to_fill_s": round(fill - trig, 6)})
    out.sort(key=lambda x: x["fill_ts"])
    return out


def rk_gate(rows: Iterable[dict], chains: Iterable[dict] = (), epoch_ts: Optional[float] = None,
            required: int = REQUIRED_RUN) -> dict:
    """RK 25 Oct arm gate (pure). Newest-backwards run over MEASURED exits; ADMIN_EXCLUDED / EXCHANGE_FILLED are
    skipped (listed), UNMEASURED breaks it, and so does any measured lag above the real exchange figure."""
    post, admin_ids, xfill_ids, stop_checks = [], [], [], []
    for r in rows or ():
        st = gate_status(r)
        if st == PRE_DEPLOY:
            continue
        dec, fill = decision_fill(r)
        tid = str(r.get("trade_id") or "")
        if st == ADMIN_EXCLUDED:
            admin_ids.append(tid)
            continue
        if st == EXCHANGE_FILLED:
            xfill_ids.append(tid)
            stop_checks.append({"trade_id": tid, "gate_status": st, **stop_fill_row_check(r)})
            continue
        lag = lag_s(r)
        if st == MEASURED:
            stop_checks.append({"trade_id": tid, "gate_status": st, **stop_fill_row_check(r)})
        # UNTIMED sorts newest (inf) so it is always the first row the backwards run meets -> it always breaks it.
        post.append((fill or dec or math.inf, tid, st, lag))
    post.sort()
    samples = real_exchange_stop_samples(chains, epoch_ts)
    thr = REAL_EXCHANGE_THRESHOLD_S if threshold_proven() else None
    run, breaker = 0, None
    for ts, tid, st, lag in reversed(post):
        ok = st == MEASURED and thr is not None and lag is not None and lag <= thr
        if st != MEASURED:
            breaker = {"trade_id": tid, "status": st, "lag_s": lag, "fill_ts": None if ts == math.inf else ts,
                       "why": "NO_EXIT_TS" if st == UNTIMED_STATUS else "NO_RECEIVE_TS"}
            break
        if thr is None:
            run += 1  # counted for display only; no pass possible while UNPROVEN
            continue
        if not ok:
            breaker = {"trade_id": tid, "status": st, "lag_s": lag, "fill_ts": ts, "why": "LAG_ABOVE_REAL_EXCHANGE"}
            break
        run += 1
    violations = [c["trade_id"] for c in stop_checks if c["status"] == STOP_ROW_VIOLATION]
    if thr is None:
        gate = GATE_UNPROVEN
    else:
        gate = GATE_PASS if run >= int(required) and not violations else GATE_FAIL
    xs = sorted(s["trigger_to_fill_s"] for s in samples)
    return {"schema": "exit_lag_arm_gate_v2", "gate": gate, "arm_eligible": gate == GATE_PASS,
            "gs01_arm_blocked_by_exit_lag": False if (gate == GATE_UNPROVEN and GS01_UNPROVEN_ARM_ALLOWED) else gate != GATE_PASS,
            "gs01_unproven_policy": {"allowed": GS01_UNPROVEN_ARM_ALLOWED, "tile": "GS01", "max_notional_usd": GS01_MAX_NOTIONAL_USD,
                                     "non_countable_reason": UNPROVEN_REASON,
                                     "ruling": "SHARED-TRACKER 2026-10-25 03:50 AEDT 'EXIT_LAG arm gate per RK ruling' (RK verbatim text pasted there)"},
            "consecutive_measured_within_threshold": run if thr is not None else 0,
            "consecutive_measured_unjudged": run if thr is None else None, "required": int(required),
            "threshold_s": thr, "threshold_source_ids": list(REAL_EXCHANGE_THRESHOLD_SOURCE_IDS),
            "n_exchange_stops": len(samples), "min_exchange_stops": MIN_EXCHANGE_STOPS,
            "real_exchange_figure": {"n": len(samples), "max_s": xs[-1] if xs else None,
                                     "median_s": xs[len(xs) // 2] if xs else None,
                                     "source_ids": [s["id"] for s in samples], "samples": samples[-50:],
                                     "status": "CANDIDATE_FOR_HM" if len(samples) >= MIN_EXCHANGE_STOPS else "INSUFFICIENT_N"},
            "measure": "exit_decision_ts - exit_trigger_receive_ts (WS_FRAME)",
            "scope": {"since_ts": GATE_SCOPE_START_TS, "reason": GATE_SCOPE_REASON},
            "counts": {s: sum(1 for p in post if p[2] == s) for s in (MEASURED, UNMEASURED_STATUS, UNTIMED_STATUS)},
            "admin_excluded_ids": admin_ids, "exchange_filled_ids": xfill_ids, "run_broken_by": breaker,
            "stop_fill_row_checks": stop_checks, "stop_fill_row_violations": violations,
            "last": [{"trade_id": t, "status": s, "lag_s": l, "fill_ts": None if ts == math.inf else ts}
                     for ts, t, s, l in post[-20:]]}


def arm_block_reason(gate: Any, lane_tile_key: Optional[str]) -> Optional[str]:
    """HM R2 (a): the reason an arm / live entry is refused by the exit-lag gate, or None. Fail closed: an unreadable
    gate (None, not a dict, wrong schema, flag missing or not a real bool) refuses. GS-01 reads
    gs01_arm_blocked_by_exit_lag; every other tile needs arm_eligible is True."""
    if not isinstance(gate, dict) or gate.get("schema") != "exit_lag_arm_gate_v2":
        return ARM_BLOCK_UNREADABLE
    if str(lane_tile_key or "").lower() == GS01_TILE_KEY:
        flag = gate.get("gs01_arm_blocked_by_exit_lag")
        if not isinstance(flag, bool):
            return ARM_BLOCK_UNREADABLE
        return ARM_BLOCK_GS01 if flag else None
    elig = gate.get("arm_eligible")
    if not isinstance(elig, bool):
        return ARM_BLOCK_UNREADABLE
    return None if elig else ARM_BLOCK_NOT_ELIGIBLE


def gs01_size_block_reason(lane_tile_key: Optional[str], margin_usd: Any) -> Optional[str]:
    """HM R2 (a): GS-01 orders above GS01_MAX_NOTIONAL_USD ($0.25 margin) are refused; unreadable size refuses."""
    if str(lane_tile_key or "").lower() != GS01_TILE_KEY:
        return None
    m = _num(margin_usd)
    if m is None or m <= 0:
        return GS01_SIZE_UNREADABLE
    return GS01_SIZE_BLOCK if m > GS01_MAX_NOTIONAL_USD + 1e-12 else None


LIVE_EXIT_STAMPS = ("exit_trigger_receive_ts", "exit_decision_ts", "exit_sent_ts", "exit_ack_ts")


def live_exit_stamp_check(row: dict) -> dict:
    """Bot-driven live exits (TP, time, admin; not EXCHANGE_FILLED) must carry receive_ts, decision, send and ack."""
    ev = _events(row)
    if is_exchange_stop(row):
        return {"applies": False, "missing": []}
    missing = [k for k in LIVE_EXIT_STAMPS
               if _num(_get(row, ev, k, *(("exit_ack_at_ts",) if k == "exit_ack_ts" else ()))) is None]
    return {"applies": True, "missing": missing, "ok": not missing}
