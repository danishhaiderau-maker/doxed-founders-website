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
