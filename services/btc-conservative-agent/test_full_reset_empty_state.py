"""After a full reset every data file is missing or empty: monitor routes show 0 rows and never 500 (#680 reload too)."""
import json
import time

import pytest

import data_version

ADMIN = "empty-state-admin-token"
MONITOR = "empty-state-monitor-read-token-0123456789"
REMOTE = {"REMOTE_ADDR": "198.51.100.9"}
EPOCH = "ce-20261004-v31-freeze21b"
CUTOFF = time.time()


@pytest.fixture
def bot(monkeypatch, tmp_path):
    import bot as bot_module

    monkeypatch.chdir(tmp_path)  # a wiped runtime: no csv, no jsonl, no shadow file, no book
    monkeypatch.setattr(bot_module, "_BOT_ADMIN_TOKEN", ADMIN)
    monkeypatch.setattr(bot_module, "_MONITOR_READ_TOKEN", MONITOR)
    monkeypatch.setattr(bot_module, "_DASHBOARD_BOOTSTRAP_COMPLETE", True)
    monkeypatch.setattr(bot_module, "BOT_INSTANCE_ID", "empty-state-boot")
    monkeypatch.setattr(bot_module, "_API_RATE_MAX_PER_IP", 10_000)
    monkeypatch.setattr(bot_module, "_current_epoch_boundary", lambda force=False: (CUTOFF, "fresh_start"))
    monkeypatch.setattr(bot_module, "_current_epoch_id_for_display", lambda: EPOCH)
    monkeypatch.setattr(bot_module, "_fresh_start_epoch", lambda: {"epoch_ts": CUTOFF})
    monkeypatch.setattr(bot_module, "trades", [])
    monkeypatch.setattr(bot_module, "open_positions", [])
    monkeypatch.setattr(bot_module, "pending_orders", [])
    monkeypatch.setattr(bot_module, "expired_orders", [])
    monkeypatch.setattr(bot_module, "_api_state_cache", {"payload": {"tile_route_counts": {}}, "built_at": time.time()})
    monkeypatch.setattr(bot_module, "_MONITOR_CACHE", {})
    return bot_module


def _get(bot, path):
    return bot.app.test_client().get(path, headers={"Authorization": f"Bearer {MONITOR}"}, environ_base=REMOTE)


def test_tiles_trades_is_200_with_zero_rows(bot):
    r = _get(bot, "/api/monitor/tiles/trades")
    assert r.status_code == 200, r.data[:200]
    body = r.get_json()
    assert body["total_matching"] == 0 and body.get("rows", body.get("trades", [])) == []


def test_reconcile_is_200_and_every_tile_has_zero_rows(bot, monkeypatch):
    monkeypatch.setattr(bot, "_load_archive_receipt", lambda: {})
    r = _get(bot, "/api/monitor/reconcile")
    assert r.status_code == 200, r.data[:200]
    assert all(t["fly_n"] == 0 for t in r.get_json()["per_tile"])


def test_fill_provenance_is_200_with_missing_shadow_file(bot):
    r = _get(bot, "/api/monitor/fill-provenance")
    assert r.status_code == 200, r.data[:200]
    body = r.get_json()
    assert body["per_tile"] and all(t["closed"] == 0 and t["fresh_eligible"] == 0 for t in body["per_tile"])
    assert body["shadow_truncated"] is False


def test_lanes_is_200_with_zero_closes_and_null_slippage(bot, monkeypatch):
    monkeypatch.setattr(bot, "_showcase_trade_session_start", lambda: 0.0)
    r = _get(bot, "/api/monitor/lanes")
    assert r.status_code == 200, r.data[:200]
    lanes = r.get_json()["lanes"]
    assert lanes and all(row["closes"] == 0 for row in lanes)
    for row in lanes:
        for key, value in row.items():
            if "slippage" in key and not key.startswith("n_"):
                assert value is None, (key, value)  # never a placeholder 0.0


@pytest.mark.parametrize("content", [None, "", "trade_id,entry_ts,exit_status\n"])
def test_typed_reload_handles_missing_empty_and_header_only_csv(bot, monkeypatch, tmp_path, content):
    if content is not None:
        (tmp_path / "trades_3factor.csv").write_text(content, encoding="utf-8")
    monkeypatch.setattr(bot, "_research_data_start_ts", lambda: 1.0)
    assert bot.load_session_trades_from_csv() == 0
    assert bot.trades == []


def test_typed_reload_restores_types_for_a_stamped_row(bot, monkeypatch, tmp_path):
    (tmp_path / "trades_3factor.csv").write_text(
        "trade_id,entry_ts,fresh_eligible,fill_model_version,data_version\nT1,%s,False,1,fse-%d\n" % (time.time(), int(CUTOFF)),
        encoding="utf-8")
    monkeypatch.setattr(bot, "_research_data_start_ts", lambda: 1.0)
    monkeypatch.setattr(bot, "_trade_row_in_session", lambda row, start: True)
    assert bot.load_session_trades_from_csv() == 1
    row = bot.trades[0]
    assert row["fresh_eligible"] is False and row["fill_model_version"] == 1 and row["data_version"] == f"fse-{int(CUTOFF)}"


def test_book_tail_handles_a_missing_or_empty_book_file(tmp_path):
    import paper_fill
    assert paper_fill.BookTail(str(tmp_path / "bfx_book_10l_1s.jsonl")).at(time.time()) is None
    (tmp_path / "empty.jsonl").write_text("")
    assert paper_fill.BookTail(str(tmp_path / "empty.jsonl")).at(time.time()) is None


def test_log_trade_stamps_data_version_only_once_an_epoch_exists(bot, monkeypatch, tmp_path):
    written = []
    monkeypatch.setattr(bot, "dynamic_csv_writer", lambda name, row: written.append(dict(row)))
    base = {"trade_id": "T9", "entry": 1.0, "exit": 1.0, "net_pnl_usd": 0.0, "exit_reason": "X"}
    bot.log_trade(dict(base))
    assert written[0]["data_version"] == f"fse-{int(CUTOFF)}"
    monkeypatch.setattr(bot, "_fresh_start_epoch", lambda: None)
    bot.log_trade(dict(base))
    assert "data_version" not in written[1]
    bot.log_trade({**base, "data_version": "fse-1"})
    assert written[2]["data_version"] == "fse-1"  # an explicit stamp is never overwritten


def test_collector_rows_carry_dv_after_the_epoch_is_stamped(tmp_path, monkeypatch):
    """REAL LAYOUT: the bot writes $BOT_DATA_DIR/fresh_start_epoch.json (/app/data) while the collector's cwd and
    runtime_dir is $BOT_DATA_DIR/runtime. Both must resolve the same file."""
    import strategist_streams_collector as ssc
    data = tmp_path / "data"
    runtime = data / "runtime"
    runtime.mkdir(parents=True)
    monkeypatch.setenv("BOT_DATA_DIR", str(data))
    monkeypatch.chdir(runtime)
    c = ssc.Collector(str(runtime))
    tmp_path = runtime  # book rows land in runtime
    chans = {}
    c.on_bfx({"event": "subscribed", "channel": "book", "chanId": 1}, chans, 100.0)
    snap = [[100 + i, 1, 0.5] for i in range(12)] + [[200 + i, 1, -0.25] for i in range(12)]
    c.on_bfx([1, snap], chans, 100.1)
    c.close_second(100)  # no epoch yet: legacy shape, no key
    (data / "fresh_start_epoch.json").write_text(json.dumps({"epoch_ts": 1792000000.5}))
    assert not (runtime / "fresh_start_epoch.json").exists()  # the epoch is NOT in the collector's own dir
    c.on_bfx([1, snap], chans, 101.1)
    c.close_second(101)
    rows = [json.loads(x) for x in (tmp_path / ssc.BOOK_FILE).read_text().splitlines()]
    assert "dv" not in rows[0] and rows[1]["dv"] == "fse-1792000000"
    assert rows[1]["s"] == 101 and rows[1]["b"] and rows[1]["a"]


def test_stamped_book_rows_still_price_through_the_book_tail(tmp_path):
    import paper_fill
    path = tmp_path / "bfx_book_10l_1s.jsonl"
    path.write_text(json.dumps({"s": 100, "b": [[99.0, 1.0]], "a": [[100.0, 1.0]], "dv": "fse-1"}) + "\n")
    row = paper_fill.BookTail(str(path)).at(102.0)
    # RK/HM #851: a row without the collector's measured receive time still prices, labelled MISSING (non-countable)
    assert row == {"s": 100, "b": [[99.0, 1.0]], "a": [[100.0, 1.0]], "receive_ts": None, "receive_mono": None,
                   "receive_ts_source": "MISSING"}


def test_bot_and_collector_share_one_epoch_path_resolver(bot, monkeypatch, tmp_path):
    monkeypatch.setenv("BOT_DATA_DIR", str(tmp_path / "vol"))
    assert bot._fresh_start_epoch_path() == data_version.epoch_path() == str(tmp_path / "vol" / "fresh_start_epoch.json")
    assert data_version.Stamp().path == bot._fresh_start_epoch_path()
    monkeypatch.delenv("BOT_DATA_DIR")
    monkeypatch.chdir(tmp_path)
    assert bot._fresh_start_epoch_path() == data_version.epoch_path() == str(tmp_path / "fresh_start_epoch.json")


def test_data_version_stamp_reads_the_epoch_file_and_follows_changes(tmp_path):
    stamp = data_version.Stamp(str(tmp_path / data_version.FILE))
    assert stamp.current() is None
    (tmp_path / data_version.FILE).write_text(json.dumps({"epoch_ts": 1792000000}))
    assert stamp.current() == "fse-1792000000"
    (tmp_path / data_version.FILE).write_text(json.dumps({"epoch_ts": 1792009999, "pad": "x"}))
    assert stamp.current() == "fse-1792009999"
    (tmp_path / data_version.FILE).write_text("not json")
    assert stamp.current() is None
    assert data_version.id_from_epoch({"epoch_ts": 0}) is None and data_version.id_from_epoch(None) is None


def test_absent_lane_pnl_ledger_is_read_as_zero_and_recreated_at_zero_on_boot(bot, monkeypatch):
    """HM confirmation #3: the full reset deletes lane_pnl_ledger.json (DATA_STREAM_EXACT). Absent means zero everywhere,
    and the boot-time epoch segregation re-creates it with 0 closes (nothing archived: there is no old file to copy)."""
    import os
    assert not os.path.exists(bot.LANE_PNL_LEDGER_FILE)
    monkeypatch.setattr(bot, "_showcase_trade_session_start", lambda: 0.0)
    r = _get(bot, "/api/monitor/lanes")  # reader with the file absent
    assert r.status_code == 200 and all(row["closes"] == 0 for row in r.get_json()["lanes"])
    result = bot._segregate_lane_pnl_ledger_for_epoch()
    assert result["status"] == "SEGREGATED" and result["epoch_closes"] == 0 and "archived_to" not in result
    doc = json.loads(open(bot.LANE_PNL_LEDGER_FILE, encoding="utf-8").read())
    assert doc["schema"] == "lane_pnl_ledger_v1" and sum(int((b or {}).get("closes") or 0) for b in doc["lanes"].values()) == 0
