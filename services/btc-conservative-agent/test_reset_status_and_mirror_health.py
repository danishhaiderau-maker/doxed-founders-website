"""PR B: honest full-reset status (RESET_COMPLETE -> COMPLETE only after stamp + verify) and mirror health/arm block."""
import json
import re
from pathlib import Path

import full_reset_job as job
import mirror_health as mh

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
T0 = 1_000_000.0


def _finished(tmp_path, started=None):
    st = job.JobStatus(str(tmp_path), "op1")
    st.body["started_at"] = started or time.time()
    st.finish(job.RESET_COMPLETE, epoch_stamped=False)
    return st


import time  # noqa: E402


def _view(tmp_path, **kw):
    return job.read_status(str(tmp_path), now=time.time(), **kw)


def test_reset_complete_is_pending_until_stamp_and_verify(tmp_path):
    _finished(tmp_path)
    v = _view(tmp_path, fresh_epoch=None)
    assert v["outcome"] == "RESET_COMPLETE" and v["state"] == "DONE_PENDING_VERIFY"
    assert v["epoch_stamped"] is False and v["pending"] == ["epoch_stamp", "verify_receipt"]


def test_epoch_stamped_comes_from_the_epoch_file_not_the_job_flag(tmp_path):
    st = _finished(tmp_path)
    started = st.body["started_at"]
    assert _view(tmp_path, fresh_epoch={"epoch_ts": started + 60})["epoch_stamped"] is True
    assert _view(tmp_path, fresh_epoch={"epoch_ts": started - 3600})["epoch_stamped"] is False  # stale epoch from before


def test_complete_only_when_reset_stamp_and_verify_all_pass(tmp_path):
    st = _finished(tmp_path)
    epoch = {"epoch_ts": st.body["started_at"] + 60}
    assert job.record_verify(str(tmp_path), passed=True)["ok"]
    v = _view(tmp_path, fresh_epoch=epoch)
    assert (v["state"], v["outcome"]) == ("DONE", "COMPLETE")
    assert _view(tmp_path, fresh_epoch=None)["outcome"] == "RESET_COMPLETE"  # no stamp: not complete


def test_verify_failure_is_red_and_never_complete(tmp_path):
    st = _finished(tmp_path)
    job.record_verify(str(tmp_path), passed=False, detail="fresh_epoch_id not set")
    v = _view(tmp_path, fresh_epoch={"epoch_ts": st.body["started_at"] + 1})
    assert v["outcome"] == "VERIFY_FAILED" and v["alert"]["level"] == "RED"


def test_verify_receipt_is_idempotent_and_bound_to_the_operation(tmp_path):
    st = _finished(tmp_path)
    job.record_verify(str(tmp_path), passed=True)
    job.record_verify(str(tmp_path), passed=True)
    receipt = json.loads((job.job_dir(str(tmp_path)) / job.VERIFY_FILE).read_text())
    receipt["operation_id"] = "someone-else"
    (job.job_dir(str(tmp_path)) / job.VERIFY_FILE).write_text(json.dumps(receipt))
    v = _view(tmp_path, fresh_epoch={"epoch_ts": st.body["started_at"] + 1})
    assert v["outcome"] == "RESET_COMPLETE"  # a receipt for another operation never completes this one


def test_verify_refused_unless_reset_complete(tmp_path):
    st = job.JobStatus(str(tmp_path), "op2")
    assert job.record_verify(str(tmp_path), passed=True)["error"] == "RESET_NOT_COMPLETE"
    st.finish("RESET_FAILED")
    assert job.record_verify(str(tmp_path), passed=True)["ok"] is False


def test_workflow_posts_the_receipt_and_polls_reset_complete():
    wf = (ROOT / ".github/workflows/fly-bot-deploy.yml").read_text()
    assert "api/monitor/full-reset/verify" in wf and "steps.verify.conclusion" in wf
    assert 'view.get("outcome") in ("RESET_COMPLETE", "COMPLETE")' in wf
    bot = (HERE / "bot.py").read_text()
    assert "status.finish(full_reset_job.RESET_COMPLETE" in bot and 'status.finish("COMPLETE"' not in bot


# ---- mirror ----------------------------------------------------------------

def _web(accounts, hb=None, reachable=True):
    return {"reachable": reachable, "accounts": accounts, "executor_heartbeat_age_sec": hb}


ACCT = {"account": "acct-cmq6cfwv", "armed": False, "paused": True}


def test_zero_accounts_with_a_known_account_alerts():
    h = mh.evaluate(["acct-cmq6cfwv"], _web([]), T0, {})
    assert h["causes"] == [mh.ZERO_ACCOUNTS] and h["alert"]


def test_missing_known_account_alerts_even_if_another_is_listed():
    h = mh.evaluate(["acct-cmq6cfwv"], _web([{"account": "acct-other"}], hb=3), T0, {})
    assert h["causes"] == [mh.ACCOUNT_MISSING] and h["missing_accounts"] == ["acct-cmq6cfwv"]


PAUSED_EVER_ARMED = {"account": "acct-cmq6cfwv", "armed": False, "paused": True, "armed_at": "2026-10-11T00:00:00Z"}


def test_null_heartbeat_alerts_only_for_an_account_that_was_ever_armed_and_after_ten_minutes():
    state = {}
    k = ["acct-cmq6cfwv"]
    assert mh.evaluate(k, _web([PAUSED_EVER_ARMED], hb=None), T0, state)["causes"] == []
    assert mh.evaluate(k, _web([PAUSED_EVER_ARMED], hb=None), T0 + 601, state)["causes"] == [mh.HEARTBEAT_NULL]
    assert mh.evaluate(k, _web([PAUSED_EVER_ARMED], hb=4), T0 + 700, state)["causes"] == []
    assert "hb_null_since" not in state


def test_never_armed_paused_account_with_null_heartbeat_is_not_an_alert():
    state = {}
    k = ["acct-cmq6cfwv"]
    for t in (T0, T0 + 601, T0 + 5000):
        h = mh.evaluate(k, _web([ACCT], hb=None), t, state)
        assert h["causes"] == [] and h["alert"] is False and h["ever_armed"] is False


def test_healthy_mirror_has_no_alert_and_unknown_accounts_never_alert():
    assert not mh.evaluate(["acct-cmq6cfwv"], _web([ACCT], hb=3), T0, {})["alert"]
    assert not mh.evaluate([], _web([]), T0, {})["alert"]


def _fresh(web, now=T0):
    web = dict(web)
    web["fetched_at_ts"] = now - 5
    return web


def test_arm_block_until_listed_and_flat_check_but_never_armed_needs_no_heartbeat():
    flat = {"authoritative": True, "fresh": True, "flat": True}
    known = ["acct-cmq6cfwv"]
    assert mh.arm_block_reason(mh.evaluate(known, _fresh(_web([])), T0, {}), flat, T0) == mh.ZERO_ACCOUNTS
    assert mh.arm_block_reason(mh.evaluate(known, _fresh(_web([], reachable=False)), T0, {}), flat, T0) == "MIRROR_UNREACHABLE"
    # never armed + null heartbeat: arming is possible (listed + flat check is what is required)
    never = mh.evaluate(known, _fresh(_web([ACCT], hb=None)), T0, {})
    assert mh.arm_block_reason(never, flat, T0) is None
    assert mh.arm_block_reason(never, None, T0) == "LIVE_FLAT_CHECK_UNCONFIRMED"
    assert mh.arm_block_reason(never, {"authoritative": True, "fresh": True, "flat": False}, T0) == "LIVE_FLAT_CHECK_UNCONFIRMED"
    # previously armed account still needs a heartbeat
    was = mh.evaluate(known, _fresh(_web([PAUSED_EVER_ARMED], hb=None)), T0, {})
    assert mh.arm_block_reason(was, flat, T0) == "MIRROR_HEARTBEAT_NULL"
    live = mh.evaluate(known, _fresh(_web([PAUSED_EVER_ARMED], hb=2)), T0, {})
    assert mh.arm_block_reason(live, flat, T0) is None


def test_arm_block_refuses_a_stale_or_unfetched_view():
    flat = {"authoritative": True, "fresh": True, "flat": True}
    known = ["acct-cmq6cfwv"]
    stale = mh.evaluate(known, dict(_web([ACCT], hb=2), fetched_at_ts=T0 - 300), T0, {})
    assert mh.arm_block_reason(stale, flat, T0) == "MIRROR_VIEW_STALE"
    unfetched = mh.evaluate(known, _web([ACCT], hb=2), T0, {})
    assert mh.arm_block_reason(unfetched, flat, T0) == "MIRROR_VIEW_STALE"


def test_known_account_from_env_alerts_and_blocks_with_an_empty_transition_log():
    known = mh.known_accounts_from_rows([], "acct-cmq6cfwv")  # fly.toml LIVE_COPY_KNOWN_ACCOUNTS, no history at all
    assert known == ["acct-cmq6cfwv"]
    h = mh.evaluate(known, _fresh(_web([])), T0, {})
    assert h["alert"] and h["causes"] == [mh.ZERO_ACCOUNTS]
    assert mh.arm_block_reason(h, {"authoritative": True, "fresh": True, "flat": True}, T0) == mh.ZERO_ACCOUNTS
    h2 = mh.evaluate(known, _fresh(_web([{"account": "acct-zzz"}], hb=2)), T0, {})
    assert h2["causes"] == [mh.ACCOUNT_MISSING]


def test_fly_toml_sets_the_known_account():
    toml = (HERE / "fly.toml").read_text(encoding="utf-8")
    assert 'LIVE_COPY_KNOWN_ACCOUNTS = "acct-cmq6cfwv"' in toml


def test_known_accounts_come_from_the_transition_log_and_env():
    rows = [{"account": "acct-a"}, {"account": "acct-b"}, {"x": 1}]
    assert mh.known_accounts_from_rows(rows, "acct-c, acct-a") == ["acct-a", "acct-b", "acct-c"]


def test_both_arm_paths_consult_the_mirror_block_and_monitor_goes_red():
    bot = (HERE / "bot.py").read_text()
    assert bot.count("_mirror_arm_block(") >= 3  # def + _arm_live_control + /api/live-copy/output
    mon = (HERE / "live_copy_monitor.py").read_text()
    assert 'web.get("mirror_health")' in mon


def test_fly_view_lists_every_known_bitfinex_instance_including_disarmed():
    svc = (ROOT / "apps/api/src/trading-agents/trading-agents.service.ts").read_text()
    block = svc[svc.index("async getLiveCopyFlyView"):svc.index("getPublicDashboard")]
    assert "relayArmedAt' IS NOT NULL\n             OR" not in block
    assert re.search(r"i\.\"exchangeProvider\" = 'bitfinex'\s+AND i\.\"status\"::text IN \('ACTIVE', 'PAUSED'\)", block)
    assert "listed_reason: flyViewListedReason(r)" in block
