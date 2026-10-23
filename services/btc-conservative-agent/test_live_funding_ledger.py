"""#849 RK ruling 24 Oct (PASS + 4 amendments): live-row funding read-back from the Bitfinex funding ledger."""
import json
import logging

import pytest

import live_funding_ledger as lfl
import live_vs_paper as lv
import r17_exclusion as rx
import tile_fill

S = 1792310400.0                       # a 00/08/16 UTC settlement instant
assert S % tile_fill.FUNDING_INTERVAL_S == 0
POS = (194481758, S - 600.0, S + 600.0, -0.025)  # the Bitfinex position open across S (+ its positions/hist FUNDING)


def _pos(funding):
    return POS[:3] + (funding,)
KEY, SECRET = "k" * 43, "s" * 43


def _row(lid, ts, amount, rate=0.0001, desc=None, currency="USTF0"):
    return [lid, currency, "margin", int(ts * 1000), None, amount, 250.0, None,
            desc or f"Funding Event BTCF0:USTF0 ({rate:.8f}) on wallet margin"]


def test_no_settlement_in_window_is_a_countable_zero_without_any_entry():
    out = lfl.verify(S + 10, S + 3600, ledger_rows=[], read_at_ts=S + 4000, paper_funding_usd=0.0)
    assert out["funding_status"] == "NO_CROSSING" and out["funding_verified"] is True
    assert out["funding_usd_raw"] == 0.0 and out["funding_ts_crossed"] == [] and out["funding_ts_fallbacks"] == []
    assert out["funding_model"] == "LIVE_LEDGER_V1" and out["funding_readback_at_ts"] == S + 4000
    assert rx.funding_failure(out, live=True) is None
    # the proof is the paper pricer's own schedule (bot.py:32778 -> tile_fill.settlements_crossed)
    assert tile_fill.settlements_crossed(S + 10, S + 3600) == []


def test_settlement_with_its_entry_uses_the_ledger_value_and_records_the_gap():
    rows = [_row(10500585082, S + 4, -0.025, rate=0.0001)]           # paid 0.025 USD funding (ledger amount < 0)
    out = lfl.verify(S - 60, S + 60, ledger_rows=rows, read_at_ts=S + 120, position=POS, paper_funding_usd=0.02,
                     live_notional_usd=250.0)
    assert out["funding_status"] == "PRICED" and out["funding_verified"] is True
    assert out["funding_usd_raw"] == 0.025                            # cost-positive, the paper convention
    assert out["funding_ledger_ids"] == [10500585082] and out["funding_ledger_mts"] == [S + 4]
    assert out["funding_usd_by_settlement"][0]["settlement_ts"] == S
    assert out["funding_live_minus_paper_usd"] == pytest.approx(0.005)
    assert out["funding_position_id"] == 194481758 and rx.funding_failure(out, live=True) is None


def test_settlement_without_an_entry_is_unverified():
    out = lfl.verify(S - 60, S + 60, ledger_rows=[], read_at_ts=S + 120, position=POS)   # no rate read back
    assert out["funding_status"] == "UNVERIFIED_ZERO_RATE_READ_FAILED" and out["funding_verified"] is False
    assert out["funding_settlement_status"] == ["RATE_READ_FAILED"] and rx.funding_failure(out, live=True) == "FUNDING_UNVERIFIED"


def test_untied_entries_are_unverified():
    rows = [_row(1, S + 4, -0.025)]
    out = lfl.verify(S - 60, S + 60, ledger_rows=rows, position=None)                 # no Bitfinex position
    assert out["funding_status"] == "UNVERIFIED_POSITION_UNRESOLVED"
    out = lfl.verify(S - 60, S + 60, ledger_rows=rows, position=(9, S + 1, S + 60))   # position opened after S
    assert out["funding_status"] == "UNVERIFIED_ENTRY_UNTIED"
    out = lfl.verify(S - 60, S + 60, ledger_rows=[_row(1, S + 4, -0.05, rate=0.0001)], position=_pos(-0.05),
                     live_notional_usd=250.0)                                         # charged on 500 USD: merged lot
    assert out["funding_status"] == "UNVERIFIED_ENTRY_UNTIED"
    out = lfl.verify(S - 60, S + 60, ledger_rows=[_row(1, S + 4, -0.02), _row(2, S + 5, -0.02)], position=POS)
    assert out["funding_status"] == "UNVERIFIED_AMBIGUOUS_ENTRY"
    out = lfl.verify(S + 10, S + 3600, ledger_rows=[_row(3, S + 1800, -0.02)])        # off-schedule entry in the hold
    assert out["funding_status"] == "UNVERIFIED_ENTRY_UNTIED" and out["funding_verified"] is False


def test_entries_of_other_settlements_or_symbols_are_ignored():
    rows = [_row(1, S + 4, -0.02), _row(2, S + 8 * 3600 + 4, -0.02, desc="Funding Event ETHF0:USTF0 (0.0001) on wallet margin")]
    out = lfl.verify(S + 10, S + 3600, ledger_rows=rows)   # S is before the real entry fill: another position's
    assert out["funding_status"] == "NO_CROSSING" and out["funding_usd_raw"] == 0.0


def test_read_failure_is_unverified():
    out = lfl.verify(S - 60, S + 60, read_error="HTTP_500", read_at_ts=S + 120)
    assert out["funding_status"] == "UNVERIFIED_LEDGER_READ_FAILED" and out["funding_readback_error"] == "HTTP_500"
    assert rx.funding_failure(out, live=True) == "FUNDING_UNVERIFIED"
    out = lfl.verify(S + 10, S + 60, read_error="READ_KEY_MISSING")   # even with no crossing: a failed read is unproven
    assert out["funding_verified"] is False


def test_fee_entry_is_flagged_never_applied():
    rows = [_row(1, S + 4, -0.025, rate=0.0001), _row(77, S + 30, -0.5, desc="Trading fees for 0.001 BTCF0 (BTCF0:USTF0)")]
    out = lfl.verify(S - 60, S + 60, ledger_rows=rows, position=POS, live_notional_usd=250.0)
    assert out["funding_fee_anomaly"] is True and out["funding_fee_anomaly_ledger_ids"] == [77]
    assert out["funding_usd_raw"] == 0.025 and out["funding_ledger_ids"] == [1]       # the fee is not in the funding
    assert out["funding_status"] == "PRICED" and rx.funding_failure(out, live=True) is None      # a flag for HM, not a gate


def test_paper_mismatch_does_not_exclude_the_live_row():
    rows = [_row(1, S + 4, -0.9, rate=0.0036)]
    fund = lfl.verify(S - 60, S + 60, ledger_rows=rows, position=_pos(-0.9), paper_funding_usd=0.01, live_notional_usd=250.0)
    assert fund["funding_live_minus_paper_usd"] == pytest.approx(0.89) and fund["funding_verified"] is True
    chain = {"correlation_id": "c1", "fill_price": 100.0, "fill_at_ts": S - 60, "exit_price": 100.5, "closed": True}
    assert lv.live_funding_failure(chain, fund) is None
    r = lv.pair_row("c1", chain, {"side": "LONG", "entry_price": 100.0, "exit_price": 100.4, "funding_usd_raw": 0.01},
                    live_funding=fund)
    assert r["funding_usd_live"] == 0.9 and r["funding_usd_paper"] == 0.01
    assert r["funding_live_minus_paper_usd"] == pytest.approx(0.89)
    assert not any("FUNDING" in x for x in r["non_countable_reasons"] or [])


def test_unverified_live_funding_makes_only_the_live_leg_non_countable():
    chain = {"correlation_id": "c1", "fill_price": 100.0, "fill_at_ts": S - 60, "exit_price": 100.5, "closed": True}
    twin = {"side": "LONG", "entry_price": 100.0, "exit_price": 100.4}
    bad = lfl.verify(S - 60, S + 60, ledger_rows=[], position=POS)
    r = lv.pair_row("c1", chain, twin, live_funding=bad)
    assert r["live_non_countable"] is True and r["paper_twin_non_countable"] is False
    assert "LIVE_FUNDING_UNVERIFIED:UNVERIFIED_ZERO_RATE_READ_FAILED" in r["non_countable_reasons"]
    assert r["funding_usd_live"] is None
    r = lv.pair_row("c1", chain, twin)                          # no read-back at all fails closed
    assert "LIVE_FUNDING_UNVERIFIED:NO_READBACK" in r["non_countable_reasons"]
    assert lv.live_funding_failure(dict(chain, closed=False), None) is None   # open row: not applicable yet


def test_non_fill_exit_stamp_next_to_a_settlement_is_a_ts_fallback():
    out = lfl.verify(S - 600, S + 30, ledger_rows=[_row(1, S + 4, -0.02)], position=POS, exit_ts_source="exit_at_ts")
    assert out["funding_ts_fallbacks"] == ["exit_ts:exit_at_ts"] and rx.funding_failure(out, live=True) == "FUNDING_TS_FALLBACK"


def test_position_match_is_unique_or_none():
    r = ["tBTCF0:USTF0", "CLOSED", 0, 100.0, 0, 0, None, None, None, None, None, 5, int((S - 60) * 1000), int((S + 60) * 1000)]
    assert lfl.match_position([r], S - 60, S + 60) == (5, S - 60, S + 60, 0.0)
    assert lfl.match_position([r, list(r[:11]) + [6] + r[12:]], S - 60, S + 60) is None
    assert lfl.match_position([r], S - 600, S + 60) is None


PERMS_RO = [["account", 1, 0], ["history", 1, 0], ["orders", 1, 0], ["positions", 1, 0], ["funding", 1, 0],
            ["settings", 1, 0], ["wallets", 1, 0], ["withdraw", 0, 0], ["ui_withdraw", 0, 0]]   # real shape (24 Oct probe)
_PROW = ["tBTCF0:USTF0", "CLOSED", 0, 100.0, -0.025, 0, None, None, None, None, None, 5, int((S - 60) * 1000),
         int((S + 60) * 1000)]
CHAIN = {"correlation_id": "c1", "fill_price": 100.0, "fill_qty": 2.5, "fill_at_ts": S - 60,
         "exit_fill_record_at_ts": S + 60, "closed": True}


def _fake(perms=PERMS_RO, sent=None, prow=_PROW):
    def post(path, body, creds):
        (sent if sent is not None else []).append((path, body))
        if path == lfl.PERMISSIONS_PATH:
            if isinstance(perms, Exception):
                raise perms
            return perms
        if path == lfl.LEDGER_PATH:
            return [_row(1, S + 4, -0.025)]
        return [prow]
    return post


def _ok_creds():
    return (KEY, SECRET, "BFX_FUNDING_READ_KEY"), None


def _no_get(path, params):
    raise AssertionError("unexpected deriv-status read")


def _job(**kw):
    kw.setdefault("get", _no_get)
    kw.setdefault("budget", lfl.RateBudget(100))
    kw.setdefault("creds_status_fn", _ok_creds)
    kw.setdefault("clock", lambda: S + 500)
    return lfl.FundingReadbackJob(**kw)


def _read(job, chain=CHAIN, paper=None):
    """The background tick, then the GET-side cache lookup."""
    job.submit(chain)
    job.run_once()
    return job.lookup(chain, paper)


def test_key_must_be_the_separate_funding_read_key():
    assert lfl.credential_status({"BITFINEX_API_KEY": KEY, "BITFINEX_API_SECRET": SECRET}) == (None, "READ_KEY_MISSING")
    assert lfl.credential_status({"BFX_READ_API_KEY": KEY, "BFX_READ_API_SECRET": SECRET}) == (None, "READ_KEY_MISSING")
    ok = lfl.credential_status({"BFX_FUNDING_READ_KEY": KEY, "BFX_FUNDING_READ_SECRET": SECRET, "BITFINEX_API_KEY": "other"})
    assert ok[1] is None and ok[0][2] == "BFX_FUNDING_READ_KEY"
    same = lfl.credential_status({"BFX_FUNDING_READ_KEY": KEY, "BFX_FUNDING_READ_SECRET": SECRET, "BITFINEX_API_KEY": KEY})
    assert same == (None, "READ_KEY_NOT_SEPARATE")
    out = _read(_job(creds_status_fn=lambda: (None, "READ_KEY_NOT_SEPARATE")))
    assert out["funding_status"] == "UNVERIFIED_LEDGER_READ_FAILED" and out["funding_key_gate"] == "READ_KEY_NOT_SEPARATE"


def test_key_with_any_write_right_refuses_to_run_and_stamps_unverified():
    for scope in ("orders", "wallets", "withdraw", "positions", "funding"):
        perms = [[s_, 1, 1 if s_ == scope else 0] for s_, _, _ in PERMS_RO]
        sent = []
        job = _job(post=_fake(perms, sent))
        out = _read(job)
        assert out["funding_verified"] is False and out["funding_key_gate"] == f"KEY_HAS_WRITE:{scope}"
        assert out["funding_readback_error"] == f"KEY_GATE:KEY_HAS_WRITE:{scope}"
        assert [p for p, _ in sent] == [lfl.PERMISSIONS_PATH]          # no ledger read at all
        job._gate_at = -1e12                                              # write rights refuse for good (no re-check)
        _read(job)
        assert [p for p, _ in sent] == [lfl.PERMISSIONS_PATH]
    assert lfl.check_permissions(("k", "s", "x"), post=lambda *a: PERMS_RO) is None


def test_permission_read_failure_refuses_to_run():
    for bad in (lfl.ReadError("HTTP_500"), [], [["orders"]], {"x": 1}):
        sent = []
        out = _read(_job(post=_fake(bad, sent)))
        assert out["funding_verified"] is False and out["funding_key_gate"].startswith("KEY_PERMISSIONS_READ_FAILED:")
        assert [p for p, _ in sent] == [lfl.PERMISSIONS_PATH]


def test_rate_budget_is_this_keys_own_global_cap():
    t = [0.0]
    b = lfl.RateBudget(3, clock=lambda: t[0])
    assert [b.acquire() for _ in range(4)] == [True, True, True, False]
    t[0] = 61.0
    assert b.acquire() is True
    assert lfl.GLOBAL_MAX_REQ_PER_MIN == 12 and isinstance(lfl.GLOBAL_BUDGET, lfl.RateBudget)
    assert lfl.FundingReadbackJob().__dict__["_budget_rl"] is lfl.GLOBAL_BUDGET        # one process-wide budget
    sent = []
    job = _job(post=_fake(sent=sent), budget=lfl.RateBudget(1))
    out = _read(job)                                                  # permissions used the 1 slot
    assert out["funding_status"] == "PENDING_READBACK" and out["funding_verified"] is False
    assert [p for p, _ in sent] == [lfl.PERMISSIONS_PATH]            # the row stays queued, nothing stamped failed
    # the cap lives in this module only, never in the relay path (no relay import here)
    src = open(lfl.__file__).read()
    assert "relay" not in "".join(l for l in src.splitlines() if l.startswith(("import", "from")))


def test_budget_holds_under_concurrent_threads():
    import threading
    b = lfl.RateBudget(12, clock=lambda: 1000.0)
    got, start = [], threading.Barrier(32)

    def worker():
        start.wait()
        for _ in range(20):
            got.append(b.acquire())
    ts = [threading.Thread(target=worker) for _ in range(32)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sum(got) == 12 and len(got) == 640


def test_job_spends_at_most_the_budget_while_gets_hammer_the_cache():
    """Owner thread + 16 concurrent GET threads: the transport sees <= 12 calls in the minute, GETs never call it."""
    import threading
    calls, getter_calls = [], []
    owner = []

    def post(path, body, creds):
        calls.append(threading.get_ident())
        return _fake()(path, body, creds)
    job = _job(post=post, budget=lfl.RateBudget(12, clock=lambda: 1000.0))
    chains = [dict(CHAIN, correlation_id=f"c{i}") for i in range(40)]
    stop = threading.Event()

    def getter():
        while not stop.is_set():
            for ch in chains:
                job.lookup(ch, None)

    gs = [threading.Thread(target=getter) for _ in range(16)]
    [g.start() for g in gs]
    job.TICK_S = 0.001
    job.start()
    import time as _t
    _t.sleep(0.3)
    stop.set()
    [g.join() for g in gs]
    job.stop()
    owner = {job._thread.ident}
    assert 0 < len(calls) <= 12 and set(calls) <= owner          # all I/O on the single owner thread, within budget
    done = [job.lookup(ch, None)["funding_status"] for ch in chains]
    assert done.count("PRICED") == (12 - 1) // 2 and done.count("PENDING_READBACK") == 40 - (12 - 1) // 2


def test_429_backs_off_and_resumes():
    t = [S + 500]
    state = {"n": 0}

    def post(path, body, creds):
        if path == lfl.LEDGER_PATH and state["n"] == 0:
            state["n"] = 1
            raise lfl.ReadError("HTTP_429")
        return _fake()(path, body, creds)
    sent = []

    def rec(path, body, creds):
        sent.append(path)
        return post(path, body, creds)
    job = _job(post=rec, clock=lambda: t[0])
    out = _read(job)
    assert out["funding_status"] == "PENDING_READBACK" and job._backoff_until == S + 500 + job.BACKOFF_BASE_S
    n = len(sent)
    t[0] += 30
    job.run_once()
    assert len(sent) == n                                         # still backing off: no call at all
    t[0] += 31
    out = _read(job)
    assert out["funding_status"] == "PRICED" and job._backoff_n == 0
    import urllib.request

    class R:
        def __init__(self, b): self.b = b
        def read(self): return self.b
        def __enter__(self): return self
        def __exit__(self, *a): return False
    orig = urllib.request.urlopen
    try:
        urllib.request.urlopen = lambda req, timeout=0: R(b'["error",11010,"ratelimit: error"]')
        with pytest.raises(lfl.ReadError) as ei:
            lfl._post(lfl.LEDGER_PATH, {}, (KEY, SECRET, "x"))
        assert str(ei.value) == "HTTP_429"
    finally:
        urllib.request.urlopen = orig


def test_get_route_makes_zero_network_calls(monkeypatch):
    """HM (B)/(C): the live-vs-paper GET only reads the job's cache; the transport is mocked and never called."""
    import urllib.request
    from flask import Flask
    import live_copy_monitor_api as api
    calls = []

    def boom(*a, **k):
        calls.append(a)
        raise AssertionError("network call inside a GET")
    monkeypatch.setattr(urllib.request, "urlopen", boom)
    monkeypatch.setattr(lfl, "_post", boom)
    job = lfl.FundingReadbackJob(creds_status_fn=_ok_creds, post=boom, get=boom, budget=lfl.RateBudget(100))
    monkeypatch.setattr(lfl, "DEFAULT_JOB", job)
    ctx = {"approvals": [{"correlation_id": "g1", "research_lane": "FAMILY_GS01_XV", "signal_at_ts": S - 70, "event": "ORDER_PLACED"}],
           "reports": [{"correlation_id": "g1", "account": "A", "lane": "FAMILY_GS01_XV", "type": "ORDER_FILLED",
                        "fill": {"price": 100.0}, "timeline": {"order_sent_at_ts": S - 61, "fill_at_ts": S - 60}},
                       {"correlation_id": "g1", "account": "A", "lane": "FAMILY_GS01_XV", "type": "EXIT_FILLED",
                        "timeline": {"exit_ack_at_ts": S + 60}, "closed": True}],
           "paper_twins": {"g1": {"side": "LONG", "entry_price": 100.0}}}
    api.wire(lambda: ctx, lambda: True)
    app = Flask(__name__)
    app.register_blueprint(api.blueprint)
    for _ in range(3):
        r = app.test_client().get("/api/monitor/live-vs-paper")
        assert r.status_code == 200, r.data
    assert calls == [] and job.requests_made == 0
    for ch in [dict(CHAIN), dict(CHAIN, correlation_id="z")]:
        out = job.lookup(ch, None)
        assert out["funding_status"] == "PENDING_READBACK" and lv.live_funding_failure(ch, out) == \
            "LIVE_FUNDING_UNVERIFIED:PENDING_READBACK"
    assert calls == [] and len(job._pending) >= 2                  # queued for the owner thread, nothing read


def test_ledger_vs_positions_hist_mismatch_is_non_countable():
    rows = [_row(1, S + 4, -0.025)]
    out = lfl.verify(S - 60, S + 60, ledger_rows=rows, position=_pos(-0.04), live_notional_usd=250.0)
    assert out["funding_status"] == "UNVERIFIED_LEDGER_POSITION_MISMATCH" and rx.funding_failure(out, live=True) == "FUNDING_UNVERIFIED"
    out = lfl.verify(S - 60, S + 60, ledger_rows=rows, position=POS[:3] + (None,), live_notional_usd=250.0)
    assert out["funding_status"] == "UNVERIFIED_POSITION_FUNDING_MISSING"
    out = lfl.verify(S + 10, S + 3600, ledger_rows=[], position=(5, S + 10, S + 3600, -0.3))   # no crossing yet funded
    assert out["funding_status"] == "UNVERIFIED_LEDGER_POSITION_MISMATCH"
    out = lfl.verify(S + 10, S + 3600, ledger_rows=[], position=(5, S + 10, S + 3600, 0.0))
    assert out["funding_status"] == "NO_CROSSING" and out["funding_verified"] is True
    out = _read(_job(post=_fake(prow=_PROW[:4] + [-0.07] + _PROW[5:])))
    assert out["funding_status"] == "UNVERIFIED_LEDGER_POSITION_MISMATCH"


def test_job_happy_path_caches_and_never_logs_secrets(caplog, monkeypatch):
    for k in ("BITFINEX_API_KEY", "BFX_READ_API_KEY", "BFX_FUNDING_READ_KEY"):
        monkeypatch.delenv(k, raising=False)
    caplog.set_level(logging.DEBUG)
    sent = []
    job = _job(post=_fake(sent=sent))
    assert job.lookup(CHAIN, 0.02)["funding_status"] == "PENDING_READBACK" and sent == []
    job.run_once()
    out = job.lookup(CHAIN, 0.02)
    assert out["funding_status"] == "PRICED" and out["funding_readback_at_ts"] == S + 500 and out["funding_key_gate"] == "OK"
    assert out["funding_ledger_ids"] == [1] and out["funding_position_id"] == 5 and out["funding_position_funding"] == -0.025
    assert [p for p, _ in sent] == [lfl.PERMISSIONS_PATH, lfl.LEDGER_PATH, lfl.POSITIONS_PATH]
    assert sent[1][1]["category"] == 29
    job.submit(CHAIN)
    job.run_once()
    job.lookup(CHAIN, 0.02)
    assert len(sent) == 3                                       # permissions once, verified read-back cached
    out2 = _read(_job(post=_fake(lfl.ReadError("HTTP_401"))))
    out3 = _read(_job(creds_status_fn=lambda: (None, "READ_KEY_MISSING")))
    assert out3["funding_readback_error"] == "KEY_GATE:READ_KEY_MISSING"
    blob = json.dumps([out, out2, out3]) + caplog.text
    assert KEY not in blob and SECRET not in blob


def test_failed_row_read_is_unverified_then_retried():
    t = [S + 500]
    state = {"fail": True}

    def post(path, body, creds):
        if path == lfl.LEDGER_PATH and state["fail"]:
            raise lfl.ReadError("HTTP_500")
        return _fake()(path, body, creds)
    job = _job(post=post, clock=lambda: t[0])
    out = _read(job)
    assert out["funding_status"] == "UNVERIFIED_LEDGER_READ_FAILED" and out["funding_readback_error"] == "HTTP_500"
    state["fail"] = False
    t[0] += job.RETRY_S + 1
    assert _read(job)["funding_status"] == "PRICED"


def test_A_position_id_still_window_checked_and_notional_mandatory():
    r = list(_PROW)
    assert lfl.match_position([r], S - 60, S + 60, position_id=5)[0] == 5
    assert lfl.match_position([r], S - 60, S + 60, position_id="5")[0] == 5
    assert lfl.match_position([r], S - 60, S + 60, position_id=6) is None                # another id
    assert lfl.match_position([r], S - 900, S + 60, position_id=5) is None               # id given, window mismatched
    other = list(r[:11]) + [6] + r[12:]
    assert lfl.match_position([r, other], S - 60, S + 60) is None                         # ambiguous without the id
    assert lfl.match_position([r, other], S - 60, S + 60, position_id=6)[0] == 6
    out = _read(_job(post=_fake(prow=r[:12] + [int((S - 900) * 1000)] + r[13:])), dict(CHAIN, bitfinex_position_id=5))
    assert out["funding_status"] == "UNVERIFIED_POSITION_UNRESOLVED"                     # never trusted on the id alone
    rows = [_row(1, S + 4, -0.025, rate=0.0001)]
    for notional in (None, 0.0):                                                          # qty or price missing
        out = lfl.verify(S - 60, S + 60, ledger_rows=rows, position=POS, live_notional_usd=notional)
        assert out["funding_status"] == "UNVERIFIED_ENTRY_UNTIED" and out["funding_settlement_status"] == ["UNTIED"]
    out = lfl.verify(S - 60, S + 60, ledger_rows=[_row(1, S + 4, -0.025, desc="Funding Event BTCF0:USTF0 (0) on wallet margin")],
                     position=POS, live_notional_usd=250.0)                               # rate missing / zero
    assert out["funding_status"] == "UNVERIFIED_ENTRY_UNTIED"
    out = _read(_job(post=_fake()), {k: v for k, v in CHAIN.items() if k != "fill_qty"})
    assert out["funding_status"] == "UNVERIFIED_ENTRY_UNTIED"


def test_D_paper_rows_reject_the_live_ledger_source():
    live = lfl.verify(S + 10, S + 3600, ledger_rows=[], read_at_ts=S + 4000)
    assert live["funding_model"] == "LIVE_LEDGER_V1" and rx.funding_failure(live, live=True) is None
    assert rx.funding_failure(live) == "FUNDING_UNVERIFIED"                                # same dict as a PAPER row
    paper = dict(live, funding_model="SETTLEMENT_EXACT_V2")
    assert rx.funding_failure(paper) is None and rx.funding_failure(paper, live=True) == "FUNDING_UNVERIFIED"
    import r17_fields as F
    assert "LIVE_LEDGER_V1" not in F.FUNDING_MODEL_OK and "LIVE_LEDGER_V1" not in F.PAPER_FUNDING_MODEL_OK
    assert lv.live_funding_failure(CHAIN, paper) == "LIVE_FUNDING_UNVERIFIED:NO_CROSSING"


def test_E_missing_entry_fill_ts_is_an_explicit_fallback_and_non_countable():
    out = lfl.verify(None, S + 60, ledger_rows=[], read_at_ts=S + 120)
    assert out["funding_ts_fallbacks"] == ["entry_ts:fill_at_ts_missing"] and out["funding_status"] == "UNVERIFIED_FILL_TS_MISSING"
    assert rx.funding_failure(out, live=True) == "FUNDING_TS_FALLBACK"
    out = lfl.verify(S - 60, None)
    assert out["funding_ts_fallbacks"] == ["exit_ts:exit_fill_missing"]
    sent = []
    job = _job(post=_fake(sent=sent))
    ch = {k: v for k, v in CHAIN.items() if k != "fill_at_ts"}
    out = _read(job, ch)
    assert "entry_ts:fill_at_ts_missing" in out["funding_ts_fallbacks"] and out["funding_verified"] is False
    assert lv.live_funding_failure(dict(ch, fill_price=100.0), out) == "LIVE_FUNDING_TS_FALLBACK:UNVERIFIED_FILL_TS_MISSING"
    assert lfl.LEDGER_PATH not in [p for p, _ in sent]                                     # never read on a guessed window


def test_real_post_redacts_transport_errors(monkeypatch):
    import urllib.request

    def fail(req, timeout=0):
        raise OSError(f"connection reset {KEY} {SECRET}")
    monkeypatch.setattr(urllib.request, "urlopen", fail)
    with pytest.raises(lfl.ReadError) as ei:
        lfl._post(lfl.LEDGER_PATH, {"category": 29}, (KEY, SECRET, "BFX_READ_API_KEY"))
    assert str(ei.value) == "TRANSPORT_OSError" and KEY not in repr(ei.value) and SECRET not in repr(ei.value)


def test_build_wires_the_readback_and_a_raising_reader_fails_closed():
    chain = {"correlation_id": "c1", "fill_price": 100.0, "fill_at_ts": S + 10, "exit_fill_record_at_ts": S + 60, "closed": True}
    twin = {"side": "LONG", "entry_price": 100.0, "exit_price": 100.4, "funding_usd_raw": 0.0}
    seen = []
    ok = lv.build([chain], {"c1": twin}, funding_readback_for=lambda ch, paper: (
        seen.append(paper), lfl.verify(S + 10, S + 60, ledger_rows=[], paper_funding_usd=paper))[1])
    assert seen == [0.0] and ok["rows"][0]["funding_usd_live"] == 0.0
    assert not any("FUNDING" in x for x in ok["rows"][0]["non_countable_reasons"] or [])
    bad = lv.build([chain], {"c1": twin}, funding_readback_for=lambda ch, paper: 1 / 0)
    assert "LIVE_FUNDING_UNVERIFIED:NO_READBACK" in bad["rows"][0]["non_countable_reasons"]


# ---- RK zero-rate ruling (24 Oct): a no-entry settlement is 0 only with the EXACT settlement rate == 0 as returned
# (public deriv status history, read in the background job under the same budget) AND positions/hist FUNDING == 0.
from decimal import Decimal

T_MS = int(S * 1000)


def _drow(mts_s, next_ts_ms, rate):
    r = [int(mts_s * 1000), None, Decimal("81623.72"), Decimal("81577.5"), None, Decimal("61068082.5"), None, next_ts_ms,
         Decimal("0.00055066"), 9189, None, rate, None, None]
    return r


def _dhist(rate, t=S, flip=True, before=True):
    """Real shape (24 Oct probe): rows ~1/min, [7] = next settlement ms, [11] = CURRENT_FUNDING; [7] flips after t."""
    tm = int(t * 1000)
    rows = []
    if before:
        rows += [_drow(t - 124, tm, Decimal("0.00011")), _drow(t - 4, tm, rate)]
    if flip:
        rows += [_drow(t + 57, tm + 8 * 3600 * 1000, Decimal("0.00009"))]
    return rows


def _rate(rate, err=None, row_mts=T_MS - 4000):
    return {S: {"rate": rate, "row_mts": None if err else row_mts, "read_at_ts": S + 500,
                "endpoint": lfl.API_PUB + lfl.DERIV_HIST_PATH, "error": err}}


def test_zero_rate_exact_and_position_zero_counts_as_zero():
    for rate in (Decimal("0"), Decimal("0.00000000"), 0):
        out = lfl.verify(S - 60, S + 60, ledger_rows=[], read_at_ts=S + 500, position=_pos(0.0),
                         settlement_rates=_rate(rate))
        assert out["funding_status"] == "PRICED" and out["funding_verified"] is True and out["funding_usd_raw"] == 0.0
        assert out["funding_settlement_status"] == ["ZERO_RATE"] and rx.funding_failure(out, live=True) is None
        proof = out["funding_settlement_rate_readbacks"][0]
        assert proof["settlement_ts"] == S and proof["read_at_ts"] == S + 500 and proof["error"] is None
        assert proof["endpoint"] == "https://api-pub.bitfinex.com/v2/status/deriv/tBTCF0:USTF0/hist"
        assert proof["rate"] in ("0", "0.00000000") and proof["row_mts"] == T_MS - 4000
    out = lfl.verify(S - 60, S + 60, ledger_rows=[], position=_pos(5e-9), settlement_rates=_rate(Decimal("0")))
    assert out["funding_status"] == "PRICED"                                              # within 1e-8


def test_zero_rate_nonzero_rate_with_no_entry_is_unverified():
    for rate in (Decimal("0.00000001"), Decimal("1E-12"), Decimal("-0.0001"), 0.0, "0"):   # never rounded / inferred
        out = lfl.verify(S - 60, S + 60, ledger_rows=[], position=_pos(0.0), settlement_rates=_rate(rate))
        assert out["funding_status"] == "UNVERIFIED_SETTLEMENT_NO_ENTRY" and out["funding_verified"] is False
        assert out["funding_settlement_status"] == ["RATE_NONZERO"]
        assert lv.live_funding_failure(CHAIN, out) == "LIVE_FUNDING_UNVERIFIED:UNVERIFIED_SETTLEMENT_NO_ENTRY"


def test_zero_rate_read_error_is_unverified():
    for rates in (None, {}, _rate(None, err="HTTP_500"), _rate(None, err="TRANSPORT_URLError"), _rate(None, err="API_ERROR")):
        out = lfl.verify(S - 60, S + 60, ledger_rows=[], position=_pos(0.0), settlement_rates=rates)
        assert out["funding_status"] == "UNVERIFIED_ZERO_RATE_READ_FAILED" and out["funding_verified"] is False
        assert out["funding_settlement_status"] == ["RATE_READ_FAILED"]


def test_zero_rate_missing_settlement_is_unverified_and_never_inferred_from_neighbours():
    assert lfl.read_settlement_rate(S, get=lambda p, q: _dhist(Decimal("0")))["rate"] == Decimal("0")
    # only rows of the NEIGHBOURING settlement (their next-funding ts is S - 8h): never borrowed
    prev = S - 8 * 3600
    rows = [_drow(S - 124, int(prev * 1000), Decimal("0")), _drow(S - 4, int(prev * 1000), Decimal("0"))]
    assert lfl.read_settlement_rate(S, get=lambda p, q: rows)["error"] == "SETTLEMENT_ROW_MISSING"
    assert lfl.read_settlement_rate(S, get=lambda p, q: _dhist(Decimal("0"), before=False))["error"] == "SETTLEMENT_ROW_MISSING"
    assert lfl.read_settlement_rate(S, get=lambda p, q: _dhist(Decimal("0"), flip=False))["error"] == "SETTLEMENT_FLIP_MISSING"
    assert lfl.read_settlement_rate(S, get=lambda p, q: _dhist(None))["error"] == "RATE_NULL"
    assert lfl.read_settlement_rate(S, get=lambda p, q: [])["error"] == "SETTLEMENT_ROW_MISSING"
    for err in ("SETTLEMENT_ROW_MISSING", "SETTLEMENT_FLIP_MISSING", "RATE_NULL"):
        out = lfl.verify(S - 60, S + 60, ledger_rows=[], position=_pos(0.0), settlement_rates=_rate(None, err=err))
        assert out["funding_status"] == "UNVERIFIED_ZERO_RATE_MISSING" and out["funding_settlement_status"] == ["RATE_MISSING"]
    seen = []
    lfl.read_settlement_rate(S, get=lambda p, q: seen.append((p, q)) or [])
    assert seen == [(lfl.DERIV_HIST_PATH, {"start": T_MS - 300000, "end": T_MS + 120000, "limit": 100, "sort": 1})]


def test_zero_rate_position_cross_check_failing_is_unverified():
    for pos in (_pos(-0.0001), _pos(2e-8), _pos(None), None):
        out = lfl.verify(S - 60, S + 60, ledger_rows=[], position=pos, settlement_rates=_rate(Decimal("0")))
        assert out["funding_status"] == "UNVERIFIED_ZERO_RATE_POSITION_CHECK" and out["funding_verified"] is False
        assert out["funding_settlement_status"] == ["ZERO_RATE_POSITION_CHECK"]


def test_zero_rate_no_crossing_stays_a_countable_zero_without_a_rate_read():
    gets = []
    ch = dict(CHAIN, fill_at_ts=S + 10, exit_fill_record_at_ts=S + 3600)
    prow = _PROW[:4] + [0.0] + _PROW[5:12] + [int((S + 10) * 1000), int((S + 3600) * 1000)]
    job = _job(post=_fake(prow=prow), get=lambda p, q: gets.append(p) or [])
    out = _read(job, ch)
    assert out["funding_status"] == "NO_CROSSING" and out["funding_verified"] is True and gets == []
    assert out["funding_settlement_rate_readbacks"] == []


def _ledgerless(prow_funding=0.0, sent=None):
    base = _fake(sent=sent, prow=_PROW[:4] + [prow_funding] + _PROW[5:])

    def post(path, body, creds):
        if path == lfl.LEDGER_PATH:
            (sent if sent is not None else []).append((path, body))
            return []                                   # the settlement booked no entry
        return base(path, body, creds)
    return post


def test_zero_rate_is_read_in_the_background_job_under_the_same_budget():
    sent, gets = [], []

    def get(path, params):
        gets.append((path, params))
        return _dhist(Decimal("0"))
    job = _job(post=_ledgerless(0.0, sent), get=get)
    assert job.lookup(CHAIN, None)["funding_status"] == "PENDING_READBACK" and gets == []   # the GET reads nothing
    job.run_once()
    out = job.lookup(CHAIN, None)
    assert out["funding_status"] == "PRICED" and out["funding_settlement_status"] == ["ZERO_RATE"]
    assert out["funding_usd_raw"] == 0.0 and lv.live_funding_failure(CHAIN, out) is None
    assert out["funding_settlement_rate_readbacks"][0]["read_at_ts"] == S + 500
    assert [p for p, _ in gets] == [lfl.DERIV_HIST_PATH] and job.requests_made == 4    # perms, ledger, positions, rate
    job.run_once()
    assert len(gets) == 1                                                               # cached
    # budget: 3 slots -> the rate read waits (PENDING), never a guessed 0
    job = _job(post=_ledgerless(0.0), get=lambda p, q: _dhist(Decimal("0")), budget=lfl.RateBudget(3))
    assert _read(job)["funding_status"] == "PENDING_READBACK" and job.requests_made == 3


def test_zero_rate_read_429_backs_off_and_error_retries():
    t = [S + 500]
    state = {"n": 0}

    def get(path, params):
        state["n"] += 1
        if state["n"] == 1:
            raise lfl.ReadError("HTTP_429")
        if state["n"] == 2:
            raise lfl.ReadError("HTTP_500")
        return _dhist(Decimal("0"))
    job = _job(post=_ledgerless(0.0), get=get, clock=lambda: t[0])
    assert _read(job)["funding_status"] == "PENDING_READBACK" and job._backoff_until > t[0]
    t[0] += job.BACKOFF_BASE_S + 1
    out = _read(job)
    assert out["funding_status"] == "UNVERIFIED_ZERO_RATE_READ_FAILED"
    assert out["funding_settlement_rate_readbacks"][0]["error"] == "HTTP_500"
    t[0] += job.RETRY_S + 1
    assert _read(job)["funding_status"] == "PRICED" and state["n"] == 3
    # a non-zero rate read back through the job stays unverified
    job = _job(post=_ledgerless(0.0), get=lambda p, q: _dhist(Decimal("0.00001")))
    assert _read(job)["funding_status"] == "UNVERIFIED_SETTLEMENT_NO_ENTRY"
    job = _job(post=_ledgerless(-0.01), get=lambda p, q: _dhist(Decimal("0")))
    assert _read(job)["funding_status"] == "UNVERIFIED_ZERO_RATE_POSITION_CHECK"


def test_public_get_parses_decimal_and_redacts(monkeypatch):
    import urllib.request

    class R:
        def __init__(self, b): self.b = b
        def read(self): return self.b
        def __enter__(self): return self
        def __exit__(self, *a): return False
    body = json.dumps([[T_MS - 4000, None, 1.5, 1.5, None, 1.0, None, T_MS, 0.0005, 1, None, 0, None]]).encode()
    body = body.replace(b"0.0005", b"0.00000000000000000001")
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=0: R(body))
    rows = lfl._get_public(lfl.DERIV_HIST_PATH, {"start": 1})
    assert rows[0][8] == Decimal("1E-20") and rows[0][8] != 0                           # never underflows to 0
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=0: R(b'["error",11010,"ratelimit: error"]'))
    with pytest.raises(lfl.ReadError) as ei:
        lfl._get_public(lfl.DERIV_HIST_PATH, {})
    assert str(ei.value) == "HTTP_429"
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=0: R(b'["error",10020,"time_interval: invalid"]'))
    assert lfl.read_settlement_rate(S, get=lfl._get_public)["error"] == "API_ERROR"


# ---- RK amendment (24 Oct 04:07): condition 2 = positions/hist FUNDING == sum of the TIED ledger entries within 1e-8;
# every crossed settlement needs exactly one proof (tied entry, or exact-0 rate read when there is no entry).
S2 = S + 8 * 3600
MIXED_POS = (194481758, S - 600.0, S2 + 600.0)


def _mixed(pf, rates=None, rows=None):
    rows = [_row(1, S + 4, -0.025, rate=0.0001)] if rows is None else rows
    rates = {S2: _rate(Decimal("0"))[S]} if rates is None else rates
    return lfl.verify(S - 60, S2 + 60, ledger_rows=rows, read_at_ts=S2 + 500, position=MIXED_POS + (pf,),
                      live_notional_usd=250.0, settlement_rates=rates)


def test_amend_mixed_hold_with_matching_sum_counts():
    for pf in (-0.025, 0.025, -0.025 + 5e-9):                    # sign of positions/hist FUNDING not yet observed
        out = _mixed(pf)
        assert out["funding_status"] == "PRICED" and out["funding_verified"] is True, out["funding_status"]
        assert out["funding_settlement_status"] == ["LEDGER", "ZERO_RATE"] and out["funding_usd_raw"] == 0.025
        assert out["funding_ledger_ids"] == [1] and [r["settlement_ts"] for r in out["funding_settlement_rate_readbacks"]] == [S2]
        assert [x["funding_usd"] for x in out["funding_usd_by_settlement"]] == [0.025, 0.0]
        assert rx.funding_failure(out, live=True) is None


def test_amend_mixed_hold_with_mismatched_sum_is_unverified():
    for pf in (-0.025 - 2e-8, -0.0251, 0.0, None):
        out = _mixed(pf)
        assert out["funding_status"] == "UNVERIFIED_ZERO_RATE_POSITION_CHECK" and out["funding_verified"] is False
        assert out["funding_settlement_status"] == ["LEDGER", "ZERO_RATE_POSITION_CHECK"]
        assert out["funding_settlement_rate_readbacks"][0]["status"] == "ZERO_RATE_POSITION_CHECK"
        chain = dict(CHAIN, fill_at_ts=S - 60, exit_fill_record_at_ts=S2 + 60)
        assert lv.live_funding_failure(chain, out) == "LIVE_FUNDING_UNVERIFIED:UNVERIFIED_ZERO_RATE_POSITION_CHECK"


def test_amend_settlement_with_neither_entry_nor_zero_rate_is_unverified():
    for rates in ({}, {S2: _rate(None, err="HTTP_500")[S]}, {S2: _rate(None, err="SETTLEMENT_ROW_MISSING")[S]},
                  {S: _rate(Decimal("0"))[S]}):                    # a read for ANOTHER settlement does not count
        out = _mixed(-0.025, rates=rates)
        assert out["funding_verified"] is False and out["funding_status"].startswith("UNVERIFIED_ZERO_RATE_")
        assert out["funding_settlement_status"][0] == "LEDGER" and out["funding_settlement_status"][1] in (
            "RATE_READ_FAILED", "RATE_MISSING")


def test_amend_entry_and_zero_rate_for_the_same_settlement_never_double_counts():
    rates = {S: _rate(Decimal("0"))[S], S2: _rate(Decimal("0"))[S]}    # a (redundant) zero read for the entry's settlement
    out = _mixed(-0.025, rates=rates)
    assert out["funding_status"] == "PRICED" and out["funding_settlement_status"] == ["LEDGER", "ZERO_RATE"]
    assert out["funding_usd_raw"] == 0.025 and len(out["funding_usd_by_settlement"]) == 2
    assert [r["settlement_ts"] for r in out["funding_settlement_rate_readbacks"]] == [S2]      # the entry wins, read unused
    # a zero-rate read can never rescue an untied entry
    out = lfl.verify(S - 60, S + 60, ledger_rows=[_row(1, S + 4, -0.025)], position=_pos(-0.025), live_notional_usd=None,
                     settlement_rates=_rate(Decimal("0")))
    assert out["funding_status"] == "UNVERIFIED_ENTRY_UNTIED" and out["funding_settlement_status"] == ["UNTIED"]
    # the job only reads the rate of settlements WITHOUT an entry
    assert lfl.no_entry_settlements(S - 60, S2 + 60, [_row(1, S + 4, -0.025)]) == [S2]
    gets = []

    def get(path, params):
        gets.append(params["start"])
        return _dhist(Decimal("0"), t=S2)
    ch = dict(CHAIN, fill_at_ts=S - 60, exit_fill_record_at_ts=S2 + 60)
    prow = _PROW[:4] + [-0.025] + _PROW[5:12] + [int((S - 60) * 1000), int((S2 + 60) * 1000)]
    out = _read(_job(post=_fake(prow=prow), get=get, clock=lambda: S2 + 500), ch)
    assert out["funding_status"] == "PRICED" and out["funding_settlement_status"] == ["LEDGER", "ZERO_RATE"]
    assert gets == [int(S2 * 1000) - 300000]
