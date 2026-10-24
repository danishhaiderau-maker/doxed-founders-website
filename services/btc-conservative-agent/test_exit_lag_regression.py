"""RK PASS (7) + RK ruling 22:32 AEDT: EXIT_LAG measure = decision - trigger row's WS_FRAME receive_ts."""
from __future__ import annotations

import json
import math
import os

import exit_lag_regression as elr
import r17_exclusion as r17

FX = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "exit_lag_20261024.json")))
D = elr.DEPLOY_TS


def _p95(xs):
    xs = sorted(xs)
    return xs[max(0, math.ceil(0.95 * len(xs)) - 1)]


def _row(tid, dec, fill, rx=None, src="WS_FRAME", trig=None, row_s=None):
    ev = {"exit_trigger_ts": trig, "exit_decision_ts": dec, "exit_fill_ts": fill,
          "exit_trigger_receive_ts": rx, "exit_trigger_receive_ts_source": src if rx else None,
          "exit_trigger_row_s": row_s}
    return {"trade_id": tid, "event_timestamps": repr(ev)}


def test_threshold_stays_old_p95_and_recompute_is_reported_impossible():
    assert elr.OLD_P95_S == 0.062 and len(FX["pre_deploy_lags_excl_r17"]) == elr.OLD_P95_N
    assert round(_p95(FX["pre_deploy_lags_excl_r17"]), 3) == elr.OLD_P95_S
    assert round(_p95(FX["pre_deploy_lags_all"]), 3) == elr.OLD_P95_S_INCL_R17
    assert elr.RECEIVE_TS_FIRST_S > D and elr.RECOMPUTED_P95["value_s"] is None and elr.RECOMPUTED_P95["n"] == 0


def test_lag_uses_receive_ts_never_row_s_or_wall_trigger():
    t = D + 1000
    r = _row("a", dec=t + 0.05, fill=t + 1, rx=t, trig=t - 5, row_s=int(t) - 3)
    assert elr.lag_s(r) == 0.05 and elr.classify(r) == elr.OK
    # a whole-second row s / wall trigger alone never measures
    r2 = _row("b", dec=t + 0.05, fill=t + 1, trig=t, row_s=int(t))
    assert elr.lag_s(r2) is None and elr.classify(r2) == elr.UNMEASURED_STATUS
    # receive_ts with a non-WS_FRAME source is not the collector receive_ts
    r3 = _row("c", dec=t + 0.05, fill=t + 1, rx=t, src="MISSING")
    assert elr.lag_s(r3) is None and elr.reasons(r3) == [elr.UNMEASURED]
    assert elr.reasons(_row("d", dec=t + 0.07, fill=t + 1, rx=t)) == [elr.REASON]


def test_missing_receive_ts_is_unmeasured_noncountable():
    r = _row("u", dec=D + 50, fill=D + 51)
    assert elr.UNMEASURED in r17.proof_failures(r)
    r.update(non_countable=True, non_countable_reason="X")
    assert r17.non_countable_reason(r, ids=set(), load_errors=[]) == "X|" + elr.UNMEASURED
    # pre-deploy rows untouched
    assert elr.reasons(_row("p", dec=D - 50, fill=D - 49)) == []


def test_the_16_stay_regression_whatever_they_measure():
    assert len(elr.LEGACY_REGRESSION_IDS) == 16
    got = {tid for tid, t, d, f in FX["post_deploy"]}
    assert got == elr.LEGACY_REGRESSION_IDS
    for tid, t, d, f in FX["post_deploy"]:
        r = _row(tid, dec=d, fill=f, rx=d - 0.001)  # even a fast measured lag
        assert elr.classify(r) == elr.REGRESSION
        r.update(non_countable=True, non_countable_reason="DEFAULT_DELAY_EXIT")
        assert r17.non_countable_reason(r, ids=set(), load_errors=[]) == "DEFAULT_DELAY_EXIT|" + elr.REASON


def test_arm_gate_counts_consecutive_ok_only():
    t = D + 10_000
    rows = [_row("old", dec=D - 10, fill=D - 9, rx=D - 11),            # pre-deploy: ignored
            _row("ok1", dec=t + 0.01, fill=t + 1, rx=t),
            _row("bad", dec=t + 10.5, fill=t + 11, rx=t + 10),         # regression breaks the run
            _row("ok2", dec=t + 20.01, fill=t + 21, rx=t + 20),
            _row("ok3", dec=t + 30.01, fill=t + 31, rx=t + 30)]
    g = elr.arm_gate(rows, required=2)
    assert g["consecutive_under_threshold"] == 2 and g["armable"] and g["run_broken_by"]["trade_id"] == "bad"
    rows.append(_row("unm", dec=t + 40, fill=t + 41))                  # unmeasured: never counts, ends the run
    g = elr.arm_gate(rows, required=2)
    assert g["consecutive_under_threshold"] == 0 and not g["armable"]
    assert g["run_broken_by"]["status"] == elr.UNMEASURED_STATUS
    assert g["counts"] == {"OK": 3, "REGRESSION": 1, "UNMEASURED": 1}
