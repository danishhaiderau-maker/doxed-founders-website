"""R0 countability (RK PASS 24 Oct, item 7): EXIT_LAG_REGRESSION.

An exit triggered by the bot at or after the 346ef2bf deploy (Fly machine update 2026-10-24 08:20:43 UTC =
19:20:43 AEDT, epoch 1792830043) whose trigger -> decision lag (event_timestamps.exit_decision_ts - exit_trigger_ts)
exceeds the PRE-deploy p95 is NON-COUNTABLE with reason EXIT_LAG_REGRESSION. The same rule applies to every later
exit. The rule is pure: it reads only the row and needs no I/O.

OLD_P95_S comes from the data. It is the nearest-rank p95 of exit_decision_ts - exit_trigger_ts over the current-epoch
closed rows of /app/data/runtime/trades_3factor.csv with exit_fill_ts < DEPLOY_TS, read on Fly at 21:30 AEDT 24 Oct.
It excludes the 995 R17 discard ids (n = 166, p95 = 0.062 s, max 1.196 s over all 1131). With the 995 included the
p95 is 0.082 s. The stricter 0.062 s is pinned.
"""
from __future__ import annotations

import ast
import math
from typing import Any, Optional

REASON = "EXIT_LAG_REGRESSION"
DEPLOY_TS = 1792830043.0          # 346ef2bf on Fly: 2026-10-24T08:20:43Z
OLD_P95_S = 0.062                 # pre-deploy trigger->decision p95 (n=166, excl. R17 995)
OLD_P95_S_INCL_R17 = 0.082        # same, all 1131 pre-deploy rows (reported, not used)
OLD_P95_N = 166


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


def trigger_decision(row: dict):
    """(trigger_ts, decision_ts, fill_ts) from the row's event_timestamps, falling back to the flat columns."""
    ev = _events(row) if isinstance(row, dict) else {}
    g = lambda k, *alt: next((x for x in (_num(ev.get(k)), *(_num(row.get(a)) for a in alt)) if x), None)  # noqa: E731
    return (g("exit_trigger_ts", "exit_trigger_ts"),
            g("exit_decision_ts", "exit_trigger_eval_ts", "exit_decision_ts"),
            g("exit_fill_ts", "exit_fill_ts", "exit_ts"))


def lag_s(row: dict) -> Optional[float]:
    trig, dec, _ = trigger_decision(row)
    return round(dec - trig, 6) if trig and dec else None


def is_regression(row: Any) -> bool:
    if not isinstance(row, dict):
        return False
    trig, dec, fill = trigger_decision(row)
    if not (trig and dec):
        return False
    since = fill or dec
    return since >= DEPLOY_TS and (dec - trig) > OLD_P95_S


def rule() -> dict:
    return {"reason": REASON, "deploy_ts": DEPLOY_TS, "old_p95_s": OLD_P95_S, "old_p95_n": OLD_P95_N,
            "old_p95_s_incl_r17": OLD_P95_S_INCL_R17, "lag": "exit_decision_ts - exit_trigger_ts"}
