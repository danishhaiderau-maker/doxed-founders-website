"""C1 (HM #843): positive v22 events are formally retired under R17.

A terminal event that is replay-ineligible only because R17 stopped storing
its 1m path is refused with V22_RETIRED_R17, counted, and its pending and
provisional entries are always cleared, so nothing is left to re-mature.
"""
import os
import tempfile
import time
from unittest import mock

import bot
import collector_v22
from collector_v22_provisional import load_provisional_events, upsert_provisional_event


def _bars(signal_ts):
    rows = []
    for minute in range(-60, 181):
        ts = signal_ts + minute * 60
        rows.append([int(ts * 1000), 100000.0, 100000.0, 99990.0, 100000.0, 1.0])
    return rows


def test_terminal_r17_event_leaves_no_pending_or_provisional():
    signal_ts = float(int(time.time() // 60 * 60) - 4 * 3600)
    tid = "r17-no-pending-left"
    source = {
        "trade_id": tid, "created_ts_ts": signal_ts, "signal_price": 100000.0,
        "status": "CLOSED", "ticket_closed": True, "final_direction": "SHORT",
    }
    saved_pending = dict(bot._order_multiverse_pending_src)
    saved_state = dict(bot._order_multiverse_state)
    saved_written = set(bot._order_multiverse_written)
    previous = os.getcwd()
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as root:
        os.chdir(root)
        try:
            epoch = bot._collector_v22_epoch_id()
            upsert_provisional_event(tid, dict(source), epoch_id=epoch, data_dir=root)
            bot._order_multiverse_pending_src[tid] = dict(source)
            before = collector_v22.v22_retired_r17_count()
            with mock.patch.object(bot, "_collector_source_in_current_epoch", return_value=True), \
                 mock.patch.object(bot, "storage_blocks_new_events", return_value=False), \
                 mock.patch.object(bot, "_execution_trade_is_terminal", return_value=False), \
                 mock.patch.object(bot, "_data_sync_runtime_root", return_value=root), \
                 mock.patch.object(bot, "_resolve_entry_grid_anchor", return_value=None), \
                 mock.patch.object(bot, "_collector_path_candles_1m",
                                   return_value=(_bars(signal_ts), {"source": "test"})):
                record = bot._sync_order_multiverse(dict(source), path_complete=True)
            assert record is not None
            assert record["canonical_tape"]["path_1m_status"] == collector_v22.RETIRED_R17
            assert collector_v22.v22_retired_r17_count() == before + 1
            assert tid not in bot._order_multiverse_pending_src
            assert tid not in bot._order_multiverse_written
            assert load_provisional_events(epoch_id=epoch, data_dir=root) == {}
            assert not os.path.exists(os.path.join(root, collector_v22.RESEARCH_EVENTS_FILE))
        finally:
            os.chdir(previous)
            bot._order_multiverse_pending_src.clear()
            bot._order_multiverse_pending_src.update(saved_pending)
            bot._order_multiverse_state.clear()
            bot._order_multiverse_state.update(saved_state)
            bot._order_multiverse_written.clear()
            bot._order_multiverse_written.update(saved_written)


def _run_sync(root, source, signal_ts, *, in_epoch=True, pressure=False):
    with mock.patch.object(bot, "_collector_source_in_current_epoch", return_value=in_epoch), \
         mock.patch.object(bot, "storage_blocks_new_events", return_value=pressure), \
         mock.patch.object(bot, "event_already_written", return_value=False), \
         mock.patch.object(bot, "_execution_trade_is_terminal", return_value=False), \
         mock.patch.object(bot, "_data_sync_runtime_root", return_value=root), \
         mock.patch.object(bot, "_resolve_entry_grid_anchor", return_value=None), \
         mock.patch.object(bot, "_collector_path_candles_1m",
                           return_value=(_bars(signal_ts), {"source": "test"})):
        return bot._sync_order_multiverse(dict(source), path_complete=True)


def _isolated(fn):
    saved = (dict(bot._order_multiverse_pending_src), dict(bot._order_multiverse_state),
             set(bot._order_multiverse_written))
    previous = os.getcwd()
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as root:
        os.chdir(root)
        try:
            fn(root)
        finally:
            os.chdir(previous)
            for live, old in zip((bot._order_multiverse_pending_src, bot._order_multiverse_state,
                                  bot._order_multiverse_written), saved):
                live.clear()
                live.update(old)


def test_c1_resync_of_a_retired_tid_counts_once():
    signal_ts = float(int(time.time() // 60 * 60) - 4 * 3600)
    source = {"trade_id": "r17-resync-once", "created_ts_ts": signal_ts, "signal_price": 100000.0,
              "status": "CLOSED", "ticket_closed": True, "final_direction": "LONG"}

    def body(root):
        before = collector_v22.v22_retired_r17_count()
        stats = []
        with mock.patch.object(bot, "_collector_collection_stat", side_effect=lambda n, a=1: stats.append(n)):
            for _ in range(3):
                assert _run_sync(root, source, signal_ts) is not None
        assert collector_v22.v22_retired_r17_count() == before + 1
        assert stats.count("v22_retired_r17") == 1

    _isolated(body)


def test_c1_write_once_reports_repeat_for_an_already_retired_id():
    record = {"event_id": "r17-repeat-direct", "observation_status": "COMPLETE",
              "canonical_tape": {"path_1m_status": collector_v22.RETIRED_R17}}
    with mock.patch.object(collector_v22, "terminal_observation", return_value=True), \
         mock.patch.object(collector_v22, "event_replay_eligibility", return_value={"eligible": False}):
        first = collector_v22.write_research_event_once(record, data_dir=tempfile.gettempdir())
        second = collector_v22.write_research_event_once(record, data_dir=tempfile.gettempdir())
    assert first == (False, collector_v22.V22_RETIRED_R17)
    assert second == (False, collector_v22.V22_RETIRED_R17_REPEAT)


def test_c1_refused_early_returns_release_the_provisional_entry():
    signal_ts = float(int(time.time() // 60 * 60) - 4 * 3600)

    def body(root):
        epoch = bot._collector_v22_epoch_id()
        for tid, kwargs in (("r17-stale-epoch", {"in_epoch": False}), ("r17-pressure", {"pressure": True})):
            source = {"trade_id": tid, "created_ts_ts": signal_ts, "signal_price": 100000.0,
                      "status": "PENDING", "final_direction": "SHORT"}
            upsert_provisional_event(tid, dict(source), epoch_id=epoch, data_dir=root)
            bot._order_multiverse_pending_src[tid] = dict(source)
            assert _run_sync(root, source, signal_ts, **kwargs) is None
            assert tid not in bot._order_multiverse_pending_src
            assert tid not in load_provisional_events(epoch_id=epoch, data_dir=root)

    _isolated(body)


def test_c1_provisional_journal_has_a_hard_cap_oldest_evicted(monkeypatch):
    import json
    import collector_v22_provisional as prov
    monkeypatch.setattr(prov, "PROVISIONAL_MAX_EVENTS", 5)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as root:
        for i in range(8):
            prov.upsert_provisional_event(f"e{i}", {"created_ts_ts": 1000 + i}, epoch_id="ep", data_dir=root)
        kept = prov.load_provisional_events(epoch_id="ep", data_dir=root)
        assert sorted(kept) == ["e3", "e4", "e5", "e6", "e7"]
        raw = json.loads(open(os.path.join(root, prov.PROVISIONAL_STORE_FILE)).read())
        assert raw["cap_evicted_total"] == 3 and raw["cap_max_events"] == 5
