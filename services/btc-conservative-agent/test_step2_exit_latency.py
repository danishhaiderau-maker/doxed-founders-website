"""Step 2: protective exit latency, OFF-tile skip, rejected-signal coalescing,
conditional early cut, pre-entry dead-letter, trade event timestamps."""
import threading
import time

import pytest

import bot
import gs_regime_exit_stack as stack
import combo_pathway_config as cfg

LANE = "FAMILY_DANISH_REGIME_ROUTER"


def _pos(trade_id="t1", entry=60000.0, direction="LONG", age=10.0, now=None):
    now = time.time() if now is None else now
    return {
        "trade_id": trade_id, "research_lane": LANE, "entry": entry, "dir": direction,
        "entry_ts": now - age, "leverage": 100, "qty": 0.01, "status": "OPEN",
        "adaptive_entry_decision": {"regime": "QUIET"},
    }


@pytest.fixture
def isolated(monkeypatch):
    positions = []
    monkeypatch.setattr(bot, "open_positions", positions)
    monkeypatch.setattr(bot, "_protective_exit_last_eval", {})
    monkeypatch.setattr(bot, "_position_exit_claims", set())
    monkeypatch.setattr(bot, "_exit_close_threads", {})
    return positions


# ---------------------------------------------------------------- exit path

def test_protective_levels_follow_the_registry_profile():
    hard, time_sec = bot._protective_exit_levels(_pos())
    assert (hard, time_sec) == (40.0, 5400.0)


def test_due_detects_hard_stop_and_time_backstop_only():
    now = time.time()
    pos = _pos(now=now)
    assert bot._protective_exit_due(pos, 60000.0 * (1 - 39.9e-4), now) is None
    assert bot._protective_exit_due(pos, 60000.0 * (1 - 40.1e-4), now) == "HARD_STOP"
    short = _pos(direction="SHORT", now=now)
    assert bot._protective_exit_due(short, 60000.0 * (1 + 40.1e-4), now) == "HARD_STOP"
    old = _pos(age=5400.5, now=now)
    assert bot._protective_exit_due(old, 60000.0, now) == "TIME_BACKSTOP"


def test_scan_fires_hard_stop_in_one_pass_and_books_the_trigger_tick(isolated, monkeypatch):
    now = time.time()
    pos = _pos(now=now)
    isolated.append(pos)
    mark = 60000.0 * (1 - 41e-4)
    monkeypatch.setattr(bot, "get_mark_price", lambda *_a, **_k: mark)
    closed = []
    done = threading.Event()

    def fake_close(p, reason):
        closed.append((dict(p), reason, time.time()))
        done.set()

    monkeypatch.setattr(bot, "close_position", fake_close)
    assert bot.protective_exit_scan(now=now) == 1
    assert done.wait(2.0)
    booked, reason, closed_at = closed[0]
    assert "HARD_STOP" in reason
    assert booked["_exit_eval_price"] == pytest.approx(mark)
    overshoot_bp = (-(booked["_exit_eval_price"] - 60000.0) / 60000.0 * 1e4) - 40.0
    assert overshoot_bp <= 5.0
    assert booked["exit_trigger_source"] == "PROTECTIVE_EXIT_WORKER"
    assert booked["exit_trigger_price"] == pytest.approx(mark)
    assert closed_at - booked["exit_trigger_eval_ts"] < 2.0


def test_slow_close_does_not_delay_other_exits(isolated, monkeypatch):
    now = time.time()
    for i in range(5):
        isolated.append(_pos(trade_id=f"t{i}", now=now))
    monkeypatch.setattr(bot, "get_mark_price", lambda *_a, **_k: 60000.0 * (1 - 45e-4))
    started = {}
    release = threading.Event()

    def slow_close(p, reason):
        started[p["trade_id"]] = time.time()
        release.wait(5.0)

    monkeypatch.setattr(bot, "close_position", slow_close)
    t0 = time.time()
    assert bot.protective_exit_scan(now=now) == 5
    assert time.time() - t0 < 1.0  # the scan never waits for a close
    deadline = time.time() + 2.0
    while len(started) < 5 and time.time() < deadline:
        time.sleep(0.01)
    assert len(started) == 5
    assert max(started.values()) - t0 < 1.0
    assert bot.protective_exit_scan(now=now + 2) == 0  # pending closes are not re-fired
    release.set()


def test_claim_prevents_concurrent_evaluation(isolated, monkeypatch):
    pos = _pos()
    calls = []
    monkeypatch.setattr(bot, "_apply_position_exits", lambda *a, **k: calls.append(1) or False)
    with bot._position_exit_claim(pos) as claimed:
        assert claimed
        assert bot._evaluate_position_exit(pos, 60000.0, time.time(), exit_source="WS_TICK") is False
    assert calls == []
    bot._evaluate_position_exit(pos, 60000.0, time.time(), exit_source="WS_TICK")
    assert calls == [1]


def test_no_exit_when_nothing_is_due(isolated, monkeypatch):
    isolated.append(_pos())
    monkeypatch.setattr(bot, "get_mark_price", lambda *_a, **_k: 60010.0)
    monkeypatch.setattr(bot, "_apply_position_exits",
                        lambda *a, **k: pytest.fail("full evaluation must not run"))
    assert bot.protective_exit_scan() == 0


# ------------------------------------------------------------- early cut

def _run(profile, path):
    state = stack.new_state()
    for age, cur in path:
        hit = stack.evaluate_tick(profile, state, cur_bp=cur, age_sec=age, atr_bp=4.0)
        if hit:
            return hit["rule"]
    return None


def test_conditional_cut_fires_when_mfe_never_exceeded_2bp():
    profile = cfg._DNR_FADE_PROFILE
    assert profile["cut_max_peak_bp"] == 2.0
    assert _run(profile, [(0, 0.0), (5, 1.5), (10, -6.0), (20, -12.5)]) == "THESIS_CUT"


def test_conditional_cut_skipped_after_a_3bp_run():
    profile = cfg._DNR_FADE_PROFILE
    assert _run(profile, [(0, 0.0), (5, 3.0), (10, -6.0), (20, -12.5)]) is None
    # the hard stop still protects the trade
    assert _run(profile, [(0, 0.0), (5, 3.0), (20, -40.5)]) == "HARD_STOP"


def test_profiles_without_the_key_keep_the_unconditional_cut():
    profile = {k: v for k, v in cfg._DNR_FADE_PROFILE.items() if k != "cut_max_peak_bp"}
    assert _run(profile, [(0, 0.0), (5, 3.0), (20, -12.5)]) == "THESIS_CUT"
    assert cfg._FADE_POOL_PROFILE["cut_max_peak_bp"] == 2.0


# ------------------------------------------------- OFF tiles / coalescing

class _Eval:
    SHADOW_FILE = "shadow.jsonl"

    def step(self, **_kw):
        return {}, None, [{"outcome": 1}]


def test_off_tile_rows_are_not_persisted(monkeypatch):
    monkeypatch.setattr(bot, "_XVL_EVALUATORS", {"LANE_ON": _Eval(), "LANE_OFF": _Eval()})
    monkeypatch.setattr(bot, "is_research_lane_enabled", lambda lane: lane == "LANE_ON")
    monkeypatch.setattr(bot._AI_SHADOW_TAPE, "tail", lambda *_a: [])
    written = []
    monkeypatch.setattr(bot, "_xvl_append", lambda row, _f: written.append(row["research_lane"]))
    bot._xvl_tick(time.time(), live={})
    assert written == ["LANE_ON"]


def test_rejected_v3_writes_are_coalesced_per_window(monkeypatch):
    monkeypatch.setattr(bot, "_xvl_rejected_v3_last", {})
    monkeypatch.setattr(bot, "XVL_REJECTED_V3_MIN_INTERVAL_SEC", 60.0)
    lane = "FAMILY_DANISH_REGIME_ROUTER"
    assert bot._xvl_rejected_v3_due(lane, "STAND_ASIDE", "LONG", now=1000.0)
    assert not bot._xvl_rejected_v3_due(lane, "STAND_ASIDE", "LONG", now=1030.0)
    assert bot._xvl_rejected_v3_due(lane, "STAND_ASIDE", "SHORT", now=1030.0)
    assert bot._xvl_rejected_v3_due(lane, "OTHER", "LONG", now=1030.0)
    assert bot._xvl_rejected_v3_due(lane, "STAND_ASIDE", "LONG", now=1061.0)


# ------------------------------------------------------- pre-entry evidence

def test_barrier_timeouts_dead_letter_the_stuck_receipt(monkeypatch):
    rows = []
    monkeypatch.setattr(bot, "_append_durable_handoff_row", lambda _p, row, *_a: rows.append(row) or True)
    receipt = {"receipt_id": "r" * 64, "kind": "V3_LANE_DECISION", "keys": ["k"]}
    monkeypatch.setattr(bot, "_preentry_evidence_pending", {receipt["receipt_id"]: receipt})
    for _ in range(bot.PREENTRY_EVIDENCE_MAX_ATTEMPTS - 1):
        assert bot._preentry_receipt_attempt_failed(receipt, reason="BARRIER_TIMEOUT") is False
    assert bot._preentry_receipt_attempt_failed(receipt, reason="BARRIER_TIMEOUT") is True
    assert receipt["receipt_id"] not in bot._preentry_evidence_pending
    assert rows[-1]["status"] == "DEAD_LETTERED" and rows[-1]["reason"] == "BARRIER_TIMEOUT"


def test_stale_receipt_is_redriven(monkeypatch):
    receipt = {"receipt_id": "s" * 64, "kind": "V3_LANE_DECISION", "keys": ["k"], "enqueued_ts": 100.0}
    monkeypatch.setattr(bot, "_preentry_evidence_pending", {receipt["receipt_id"]: receipt})
    drained = []
    monkeypatch.setattr(bot, "_drain_preentry_evidence", lambda **kw: drained.append(kw) or True)
    assert bot._retry_stale_preentry_evidence(now=110.0) is False
    assert bot._retry_stale_preentry_evidence(now=200.0) is True
    assert drained == [{"through": receipt["receipt_id"]}]


# ------------------------------------------------------------ timestamps

def test_trade_event_timestamps_are_recorded_not_inferred():
    pos = {"signal_created_ts": 100.0, "order_created_ts": 100.4, "entry_ts": 101.0,
           "exit_trigger_ts": 200.0, "exit_trigger_eval_ts": 200.05,
           "exit_trigger_source": "PROTECTIVE_EXIT_WORKER", "exit_trigger_price": 59750.0}
    ts = bot._trade_event_timestamps(pos, {}, exit_fill_ts=200.3)
    assert ts["schema"] == "trade_event_timestamps_v1"
    assert (ts["signal_ts"], ts["order_sent_ts"], ts["fill_ts"]) == (100.0, 100.4, 101.0)
    assert ts["exit_trigger_to_fill_sec"] == 0.3
    assert ts["signal_to_order_sent_sec"] == 0.4
    empty = bot._trade_event_timestamps({}, None, exit_fill_ts=5.0)
    assert empty["signal_ts"] is None and empty["exit_trigger_to_fill_sec"] is None
