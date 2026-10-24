"""RK PASS (7): EXIT_LAG_REGRESSION. OLD_P95_S is recomputed here from the recorded pre-deploy lags, and the
post-346ef2bf exits above it are listed."""
from __future__ import annotations

import json
import math
import os

import exit_lag_regression as elr
import r17_exclusion as r17

FX = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "exit_lag_20261024.json")))
AFFECTED = {"rnd-ed34ebc12d46", "rnd-0740577637e4", "rnd-7c2e9be15799", "rnd-1b58694c7fbd", "rnd-27d413229688",
            "gs6-fa6b47417611", "gb2-ae158ae85b19", "gs6-818ee460f682", "gb2-975899b9f2d0", "rnd-b83d097fee56",
            "rnd-d69837b51d4e", "gs1-c665cd45e611", "gb2-4006a5f3e88a", "rnd-e378e885ff55", "gb1-8dcc088b3560",
            "rnd-8547b9c6c898"}


def _p95(xs):
    xs = sorted(xs)
    return xs[max(0, math.ceil(0.95 * len(xs)) - 1)]


def test_old_p95_is_the_data():
    assert len(FX["pre_deploy_lags_excl_r17"]) == elr.OLD_P95_N
    assert round(_p95(FX["pre_deploy_lags_excl_r17"]), 3) == elr.OLD_P95_S
    assert round(_p95(FX["pre_deploy_lags_all"]), 3) == elr.OLD_P95_S_INCL_R17


def _row(tid, trig, dec, fill, as_repr=False):
    ev = {"exit_trigger_ts": trig, "exit_decision_ts": dec, "exit_fill_ts": fill}
    return {"trade_id": tid, "event_timestamps": repr(ev) if as_repr else ev}


def test_affected_ids_since_deploy():
    got = {tid for tid, t, d, f in FX["post_deploy"] if elr.is_regression(_row(tid, t, d, f, as_repr=True))}
    assert got == AFFECTED


def test_pre_deploy_never_flagged_and_going_forward_applies():
    assert not elr.is_regression(_row("x", elr.DEPLOY_TS - 100, elr.DEPLOY_TS - 90, elr.DEPLOY_TS - 89))
    assert elr.is_regression(_row("y", elr.DEPLOY_TS + 1e6, elr.DEPLOY_TS + 1e6 + 0.5, elr.DEPLOY_TS + 1e6 + 2))
    assert not elr.is_regression(_row("z", elr.DEPLOY_TS + 1e6, elr.DEPLOY_TS + 1e6 + 0.05, elr.DEPLOY_TS + 1e6 + 2))
    assert not elr.is_regression({"trade_id": "m"})  # unmeasured: not this rule's call


def test_r17_reason_carries_exit_lag_regression():
    tid, t, d, f = FX["post_deploy"][0]
    row = _row(tid, t, d, f)
    assert elr.REASON in r17.proof_failures(row)
    row.update({"non_countable": True, "non_countable_reason": "DEFAULT_DELAY_EXIT"})
    reason = r17.non_countable_reason(row, ids=set(), load_errors=[])
    assert reason == "DEFAULT_DELAY_EXIT|" + elr.REASON
