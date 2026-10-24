"""Read-only tile audit data API: /api/monitor/tiles/{specs,trades,counters,totals} and /api/monitor/tape."""
import contextlib
import json
import time
from pathlib import Path

import pytest

import monitor_api
import monitor_tiles as mt


@pytest.fixture(autouse=True)
def _r17_proof_rule_out_of_scope(monkeypatch):
    """R17 proof rule (countable only with book/delay/walk/level proof) is covered by test_r17_exclusion.py.
    These synthetic rows test other properties, so the proof check is neutralised here; the pinned-list and
    stamped exclusions still apply."""
    import r17_exclusion
    monkeypatch.setattr(r17_exclusion, "proof_failures", lambda row: [])


ADMIN = "tiles-test-admin-token"
MONITOR = "monitor-read-token-for-tiles-0123456789"
REMOTE = {"REMOTE_ADDR": "198.51.100.9"}
ROUTES = ("/api/monitor/tiles/specs", "/api/monitor/tiles/trades", "/api/monitor/tiles/counters",
          "/api/monitor/tiles/totals", "/api/monitor/tape", "/api/monitor/exit-latency",
          "/api/monitor/exit-lag")
GS1 = "FAMILY_GS01_XV_PREMIUM_ATR_TP"
HA = "FAMILY_RANDOM_CONTROL_TAKER_90"
B2 = "FAMILY_GSB2_REGIME_SWITCHER"
GS5 = "FAMILY_GS05_PREMIUM_REGIME_MANAGED"
GS6 = "FAMILY_GS06_COMMITTED_FADE_ATR_TP"
EPOCH = "ce-20261004-v31-freeze21b"
CUTOFF = 1_791_100_000.0


def _closed(tid, lane, *, close_ts, bp=None, net=0.0, reason="GS_ATR_TAKE_PROFIT", direction="LONG",
            entry=85153.0, exit_price=85160.0, dur_min=10.0, max_profit=None, **extra):
    row = {"trade_id": tid, "research_lane": lane, "ts": close_ts, "dir": direction, "entry": entry,
           "exit": exit_price, "net_pnl_usd": round(net, 2), "net_pnl_usd_raw": net, "net_pnl_bp": bp,
           "exit_reason": reason, "leverage": 100, "margin_usdt": 0.25, "duration_min": dur_min,
           "shared_ai_call_ts": close_ts - dur_min * 60 - 1}
    if max_profit is not None:
        row["max_profit"] = max_profit
    row.update(extra)
    return row


def _rows(raws, status="closed", fills=None, be=0.0):
    short = {GS1: "GS-01", HA: "CTRL", B2: "B2", GS5: "GS-05", GS6: "GS-06"}
    return [mt.tile_trade_row(r, status=status, short_names=short, epoch_id=EPOCH,
                              forced_reasons={"ADMIN_MANUAL_CLOSE", "ADMIN_FORCE_FLAT"},
                              fill_evidence=(fills or {}).get(r["trade_id"]), be_band_bp=be) for r in raws]


# ------------------------------------------------------------------ pure helpers

def test_legs_weighted_bp_and_unrounded_usd_from_prices():
    # B1 fixture: TP1 85221.12 on half, TP 85255.18 on the runner, entry 85153 -> 10.0 bp, $0.025.
    qty = 0.00029359
    raw = {"trade_id": "b1-1", "research_lane": GS1, "dir": "LONG", "entry": 85153.0, "exit": 85255.18,
           "execution_qty": qty / 2, "policy_original_qty": qty, "trading_fees_usd": 0.0,
           "partial_exit_receipts": [{"closed_qty": qty / 2, "price": 85221.12, "remaining_fraction": 0.5}],
           "net_pnl_usd": 0.02}
    pnl = mt.trade_pnl(raw)
    assert pnl["bp"] == pytest.approx(10.0, abs=0.01) and pnl["bp_basis"] == "PRICES_AND_LEGS"
    assert pnl["net_usd_cents_booked"] == 0.02 and pnl["class"] == "W"


def test_be_micro_win_is_w_with_band_zero_and_be_with_half_bp_band():
    raw = _closed("t1", GS1, close_ts=CUTOFF + 10, bp=0.23, net=0.000575)
    assert mt.trade_pnl(raw, 0.0)["class"] == "W"
    assert mt.trade_pnl(raw, 0.5)["class"] == "BE"
    assert mt.trade_pnl(raw)["net_usd_cents_booked"] == 0.0


def test_totals_equal_sum_of_trades_and_forced_closes_excluded_by_default():
    raws = [
        _closed("g1", GS1, close_ts=CUTOFF + 100, bp=11.6, net=0.029, max_profit=12.6),
        _closed("g2", GS1, close_ts=CUTOFF + 4000, bp=-5.0, net=-0.0125, direction="SHORT", max_profit=11.0),
        _closed("g3", GS1, close_ts=CUTOFF + 4100, bp=-30.0, net=-0.075, reason="ADMIN_MANUAL_CLOSE"),
    ]
    rows = _rows(raws)
    view = mt.totals_view({GS1: rows}, include_forced=False, be_band_bp=0.0,
                          route_counts={GS1: {"closed": 2}})
    t = view["lanes"][GS1]
    assert (t["closes"], t["wins"], t["losses"], t["be"], t["forced_closes"]) == (2, 1, 1, 0, 1)
    assert t["net_usd"] == pytest.approx(0.0165) and t["sum_bp"] == pytest.approx(6.6)
    assert t["long_closes"] == 1 and t["short_closes"] == 1 and t["n_eff_hours"] == 2
    assert t["giveback_share"] == 0.5  # g2: MFE 11 bp, closed <= 0
    assert view["parity"][GS1]["totals_equal_sum_of_trades"] is True
    assert view["parity"][GS1]["tile_route_counts_match"] is True
    forced = mt.totals_view({GS1: rows}, include_forced=True, be_band_bp=0.0)["lanes"][GS1]
    assert forced["closes"] == 3 and forced["forced_closes_counted"] == 1


def test_window_excludes_rows_closed_before_since():
    rows = _rows([_closed("old", HA, close_ts=CUTOFF - 600, bp=1.0, net=0.01),
                  _closed("new", HA, close_ts=CUTOFF + 60, bp=1.0, net=0.01)])
    kept = [r["trade_id"] for r in rows if mt.row_in_window(r, CUTOFF, None)]
    assert kept == ["new"]


def test_trade_pages_are_whole_rows_round_trip_without_gaps_or_duplicates():
    raws = [_closed(f"t{i:05d}", GS1, close_ts=CUTOFF + i, bp=1.0, net=0.0025, entry_path="X" * 200)
            for i in range(5000)]
    rows = _rows(raws)
    seen, cursor, pages = [], None, 0
    while True:
        page = mt.trades_page(rows, cursor=cursor, limit=500, max_bytes=mt.MAX_TILE_TRADES_BYTES, epoch=EPOCH)
        body = json.dumps(page).encode()
        assert len(body) <= mt.MAX_TILE_TRADES_BYTES
        seen.extend(r["trade_id"] for r in page["rows"])
        pages += 1
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert seen == sorted(r["trade_id"] for r in rows) and len(set(seen)) == 5000 and pages >= 10
    with pytest.raises(mt.BadRequest):
        mt.trades_page(rows, cursor=mt.encode_cursor(0, "x", "other-epoch"), limit=10, max_bytes=10_000, epoch=EPOCH)
    with pytest.raises(mt.BadRequest):
        mt.decode_cursor("!!not-a-cursor")


def test_fill_basis_is_never_null_for_a_filled_row():
    raws = [_closed("f1", GS1, close_ts=CUTOFF + 5, bp=1.0, net=0.0025),
            _closed("f2", GS1, close_ts=CUTOFF + 6, bp=1.0, net=0.0025),
            _closed("f3", GS1, close_ts=CUTOFF + 7, bp=1.0, net=0.0025)]
    fills = {"f1": {"fill_basis": "MARKETABLE_AT_PLACEMENT", "fill_ts": CUTOFF}, "f2": {"fill_basis": None}}
    rows = _rows(raws, fills=fills)
    assert [r["fill"]["fill_basis"] for r in rows] == [
        "MARKETABLE_AT_PLACEMENT", mt.FILL_BASIS_UNCLASSIFIED, mt.FILL_BASIS_NOT_INDEXED]
    counters = mt.counters_view(GS1, rows=rows, route_counts={"closed": 3, "expired": 1, "selected_calls": 4},
                                xvl_lane=None, opportunity=None, boot_id="b")
    assert counters["integrity"]["unclassified_fills"] == 1
    assert counters["integrity"]["fills_not_indexed_since_boot"] == 1
    assert counters["epoch"]["fills"] == 3 and counters["epoch"]["fill_rate"] == 0.75


def test_counters_surface_the_b2_accepted_without_order_regression():
    xvl = {"paper": {"attempts": 1, "skips": {"PRE_ENTRY_EVIDENCE_UNAVAILABLE": 1},
                     "last_attempt": {"outcome": "PRE_ENTRY_EVIDENCE_UNAVAILABLE"}}}
    out = mt.counters_view(B2, rows=[], route_counts={"selected_calls": 1, "approved_no_order": 1, "closed": 0,
                                                     "expired": 0, "open": 0},
                           xvl_lane=xvl, opportunity={"signals": 25}, boot_id="b")
    assert out["integrity"]["accepted_without_order"] == 1
    assert out["integrity"]["pre_entry_evidence_unavailable_since_boot"] == 1
    assert out["since_boot"]["last_attempt_outcome"] == "PRE_ENTRY_EVIDENCE_UNAVAILABLE"
    assert out["integrity"]["identity"]["route_counts_closed_matches_policy_closes"] is True


def test_params_validate():
    assert mt.parse_lanes([f"{GS1},{HA}", GS1], [GS1, HA]) == [GS1, HA]
    with pytest.raises(mt.BadRequest):
        mt.parse_lanes(["NOPE"], [GS1])
    with pytest.raises(mt.BadRequest):
        mt.parse_be_band("2.5")
    with pytest.raises(mt.BadRequest):
        mt.resolve_epoch("ce-older", EPOCH)
    assert mt.resolve_epoch("current", EPOCH) == mt.resolve_epoch(EPOCH, EPOCH) == EPOCH
    assert mt.parse_limit("9999") == mt.TRADES_MAX_LIMIT
    assert mt.parse_ts_param("2026-10-04T08:37:00Z", "since") == pytest.approx(1791102220.0)


def test_tape_slice_columns_pointer_and_gaps():
    ring = mt.TapeSliceRing(max_seconds=600)
    base = 1_791_100_000
    for ts in range(base, base + 300):
        if ts in (base + 10, base + 11):
            continue
        ring.append({"bucket_ts": ts, "bid": 85000.0, "ask": 85001.0, "fresh": ts != base + 20, "valid_bbo": True})
    view = mt.tape_view(ring, from_ts=base, to_ts=base + 29, fields=["bid", "ask", "fresh"], now=base + 300)
    assert view["cols"] == ["bucket_ts", "bid", "ask", "fresh"]
    assert view["rows"][0] == [base, 85000.0, 85001.0, True]
    assert [r[0] for r in view["rows"]] == sorted(r[0] for r in view["rows"])
    assert view["continuity"]["gaps"] == [[base + 10, base + 11]] and view["continuity"]["missing_seconds"] == 2
    assert view["continuity"]["stale_rows"] == 1
    old = mt.tape_view(ring, from_ts=base - 3600, to_ts=base - 3000, fields=None, now=base + 300)
    assert old["rows"] == [] and old["pointer"]["reason"] == "RANGE_OLDER_THAN_RING"
    with pytest.raises(mt.BadRequest):
        mt.tape_view(ring, from_ts=base, to_ts=base + 901, fields=None, now=base)
    full = mt.tape_view(ring, from_ts=base, to_ts=base + 299, fields=None, now=base)
    assert len(json.dumps(full).encode()) <= mt.MAX_TAPE_BYTES


# ------------------------------------------------------------------ Fly routes

class _Raising:
    def __getattr__(self, name):
        raise AssertionError(f"forbidden call: {name}")


class _TrackedLock:
    def __init__(self, inner):
        self.inner, self.acquired = inner, 0

    def acquire(self, *a, **k):
        self.acquired += 1
        return self.inner.acquire(*a, **k)

    def release(self):
        return self.inner.release()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *exc):
        self.release()


@pytest.fixture
def bot(monkeypatch):
    import bot as bot_module

    monkeypatch.setattr(bot_module, "_BOT_ADMIN_TOKEN", ADMIN)
    monkeypatch.setattr(bot_module, "_MONITOR_READ_TOKEN", MONITOR)
    monkeypatch.setattr(bot_module, "_DASHBOARD_BOOTSTRAP_COMPLETE", True)
    monkeypatch.setattr(bot_module, "BOT_INSTANCE_ID", "tiles-test-boot")
    monkeypatch.setattr(bot_module, "_API_RATE_MAX_PER_IP", 10_000)
    monkeypatch.setattr(bot_module, "_current_epoch_boundary", lambda force=False: (CUTOFF, "data_epoch"))
    monkeypatch.setattr(bot_module, "_current_epoch_id_for_display", lambda: EPOCH)
    trades = [
        _closed("gs1-a", GS1, close_ts=CUTOFF + 100, bp=11.6, net=0.029),
        _closed("ha-old", HA, close_ts=CUTOFF - 3600, bp=5.0, net=0.0125),  # before the epoch cutoff
        _closed("ha-forced", HA, close_ts=CUTOFF + 200, bp=-20.0, net=-0.05, reason="ADMIN_MANUAL_CLOSE"),
        _closed("ha-b", HA, close_ts=CUTOFF + 300, bp=-3.0, net=-0.0075),
    ]
    monkeypatch.setattr(bot_module, "trades", trades)
    monkeypatch.setattr(bot_module, "open_positions", [
        {"trade_id": "gs1-open", "research_lane": GS1, "status": "OPEN", "dir": "LONG", "entry": 85000.0,
         "entry_ts": CUTOFF + 400, "qty": 0.0003, "leverage": 100, "margin_usdt": 0.25,
         "policy_state": {"be_armed": True, "peak_bp": 9.0}}])
    monkeypatch.setattr(bot_module, "pending_orders", [
        {"trade_id": "b2-pend", "research_lane": B2, "dir": "SHORT", "limit_price": 85100.0,
         "order_created_ts": CUTOFF + 500, "qty": 0.0003}])
    monkeypatch.setattr(bot_module, "expired_orders", [
        {"trade_id": "gs1-exp", "research_lane": GS1, "dir": "LONG", "limit_price": 84900.0,
         "created_ts": CUTOFF + 50, "expired_ts": CUTOFF + 60, "reason": "TTL_EXPIRED"}])
    monkeypatch.setattr(bot_module, "_api_state_cache", {"payload": {"tile_route_counts": {
        GS1: {"closed": 1, "open": 1, "expired": 1, "selected_calls": 3},
        HA: {"closed": 1, "open": 0, "expired": 0, "selected_calls": 2}}}, "built_at": time.time()})
    bot_module._tile_fill_index_record("gs1-a", {"fill_basis": "TRADE_THROUGH", "fill_ts": CUTOFF + 40})
    monkeypatch.setattr(bot_module, "_MONITOR_CACHE", {})
    return bot_module


def _get(bot, path, headers=None, **params):
    return bot.app.test_client().get(path, headers=headers or {}, query_string=params, environ_base=REMOTE)


def test_routes_require_admin_or_monitor_bearer_and_are_get_only(bot):
    client = bot.app.test_client()
    for path in ROUTES:
        anon = client.get(path, environ_base=REMOTE)
        assert anon.status_code == 401 and anon.get_json() == {"error": "unauthorized"}
        assert client.get(path, headers={"Authorization": f"Bearer {ADMIN}"}, environ_base=REMOTE).status_code == 401
        for headers in ({"X-Bot-Admin-Token": ADMIN}, {"Authorization": f"Bearer {MONITOR}"}):
            ok = client.get(path, headers=headers, environ_base=REMOTE)
            assert ok.status_code == 200, (path, ok.data[:300])
            assert ok.headers["Cache-Control"] == "no-store"
            body = ok.get_json()
            assert body["boot_id"] == "tiles-test-boot" and body["data_epoch_id"] == EPOCH
            assert body["read_only"] is True and "scope" in body
        assert client.post(path, headers={"X-Bot-Admin-Token": ADMIN}, environ_base=REMOTE).status_code == 405
        assert path not in bot._READ_ONLY_GET_PATHS
    monitor = {"Authorization": f"Bearer {MONITOR}"}
    assert client.get("/api/state", headers=monitor, environ_base=REMOTE).status_code in (401, 200)
    assert client.post("/api/pause", headers=monitor, json={}, environ_base=REMOTE).status_code == 401


def test_monitor_token_disabled_when_short_or_equal_to_admin():
    assert monitor_api.configured_monitor_token("short", ADMIN) == ""
    assert monitor_api.configured_monitor_token(ADMIN + "-but-long-enough-xx", ADMIN + "-but-long-enough-xx") == ""


def test_trades_route_epoch_scoped_forced_flagged_and_fill_basis(bot):
    body = _get(bot, "/api/monitor/tiles/trades", {"X-Bot-Admin-Token": ADMIN}).get_json()
    ids = [r["trade_id"] for r in body["rows"]]
    assert "ha-old" not in ids  # closed before the epoch cutoff
    assert set(ids) == {"gs1-exp", "gs1-a", "ha-forced", "ha-b", "gs1-open", "b2-pend"}
    by_id = {r["trade_id"]: r for r in body["rows"]}
    assert by_id["ha-forced"]["exit"]["forced_close"] is True
    assert by_id["gs1-a"]["fill"]["fill_basis"] == "TRADE_THROUGH"
    assert by_id["ha-b"]["fill"]["fill_basis"] == mt.FILL_BASIS_NOT_INDEXED
    assert by_id["gs1-open"]["protection"]["be_armed"] is True and by_id["gs1-a"]["tile_short"] == "GS-01"
    excl = _get(bot, "/api/monitor/tiles/trades", {"X-Bot-Admin-Token": ADMIN}, include_forced="0",
                status="closed").get_json()
    assert {r["trade_id"] for r in excl["rows"]} == {"gs1-a", "ha-b"}
    page = _get(bot, "/api/monitor/tiles/trades", {"X-Bot-Admin-Token": ADMIN}, limit="2").get_json()
    assert page["returned"] == 2 and page["next_cursor"]
    rest = _get(bot, "/api/monitor/tiles/trades", {"X-Bot-Admin-Token": ADMIN}, limit="500",
                cursor=page["next_cursor"]).get_json()
    assert [r["trade_id"] for r in page["rows"]] + [r["trade_id"] for r in rest["rows"]] == ids
    assert _get(bot, "/api/monitor/tiles/trades", {"X-Bot-Admin-Token": ADMIN}, lane="NOPE").status_code == 400
    assert _get(bot, "/api/monitor/tiles/trades", {"X-Bot-Admin-Token": ADMIN}, epoch="ce-older").status_code == 400


def test_totals_route_excludes_forced_and_matches_route_counts(bot):
    body = _get(bot, "/api/monitor/tiles/totals", {"X-Bot-Admin-Token": ADMIN}).get_json()
    assert body["scope"]["include_forced"] is False and body["scope"]["wl_basis"] == "PRICE_BP_NET_OF_FEES"
    assert body["lanes"][HA]["closes"] == 1 and body["lanes"][HA]["forced_closes"] == 1
    assert body["lanes"][HA]["losses"] == 1
    assert body["parity"][HA]["tile_route_counts_match"] is True
    assert body["parity"][GS1]["tile_route_counts_match"] is True
    assert len(json.dumps(body).encode()) <= mt.MAX_TILE_TOTALS_BYTES


def test_counters_route_and_specs_route(bot):
    counters = _get(bot, "/api/monitor/tiles/counters", {"X-Bot-Admin-Token": ADMIN}).get_json()
    gs1 = next(row for row in counters["lanes"] if row["lane"] == GS1)
    assert gs1["epoch"]["fills"] == 2 and gs1["epoch"]["expiries"] == 1 and gs1["epoch"]["open"] == 1
    assert len(json.dumps(counters).encode()) <= mt.MAX_TILE_COUNTERS_BYTES
    specs = _get(bot, "/api/monitor/tiles/specs", {"X-Bot-Admin-Token": ADMIN}).get_json()
    assert len(specs["tiles"]) == len(bot.ACTIVE_TILE_ORDER) == 5  # Tiles 6/7 removed 23 Oct 2026
    assert not specs.get("truncated")
    reg = bot.ACTIVE_TILE_REGISTRY
    for tile in specs["tiles"]:
        pre = reg[tile["lane"]].get("pre_registration") or {}
        if pre.get("kill") is not None:
            assert json.dumps(tile["pre_registration"]["kill"], sort_keys=True) == json.dumps(
                mt.json_safe(pre["kill"]), sort_keys=True)
        assert "signal_clocks" in tile and "registry" in tile["signal_clocks"]
    one = _get(bot, "/api/monitor/tiles/specs", {"X-Bot-Admin-Token": ADMIN}, lane=B2)
    assert one.status_code == 200 and len(one.data) <= mt.MAX_TILE_SPECS_LANE_BYTES


def test_tape_route_reads_the_ring(bot, monkeypatch):
    ring = mt.TapeSliceRing(max_seconds=120)
    now = int(time.time())
    for ts in range(now - 60, now):
        ring.append({"bucket_ts": ts, "bid": 1.0, "ask": 2.0, "fresh": True, "valid_bbo": True})
    monkeypatch.setattr(bot, "_TILE_TAPE_RING", ring)
    body = _get(bot, "/api/monitor/tape", {"X-Bot-Admin-Token": ADMIN}, from_ts=str(now - 30),
                to_ts=str(now - 1), fields="bid,ask").get_json()
    assert body["cols"] == ["bucket_ts", "bid", "ask"] and len(body["rows"]) == 30
    assert _get(bot, "/api/monitor/tape", {"X-Bot-Admin-Token": ADMIN}, from_ts=str(now - 1000),
                to_ts=str(now)).status_code == 400


def test_routes_never_take_trade_lock_or_touch_exchange_relay_or_ai(bot, monkeypatch):
    tracked = _TrackedLock(bot.trade_lock)
    monkeypatch.setattr(bot, "trade_lock", tracked)
    for name in ("bitfinex_client", "_relay_outbox", "deepseek_client", "exchange"):
        if hasattr(bot, name):
            monkeypatch.setattr(bot, name, _Raising())
    for path in ROUTES:
        assert _get(bot, path, {"X-Bot-Admin-Token": ADMIN}).status_code == 200, path
    assert tracked.acquired == 0


def test_handlers_have_no_writes_or_order_calls():
    src = (Path(__file__).resolve().parent / "bot.py").read_text(encoding="utf-8")
    block = src.split("# Read-only tile audit data API (/api/monitor/tiles/*", 1)[1].split(
        "\n@app.route('/api/research/shadow_exits')", 1)[0]
    # The archive-receipt append-only audit log is the one deliberate writer in this region (own auth + cap,
    # covered by test_archive_receipt_v2); everything else stays write-free.
    block = block.split("def _archive_receipt_log(", 1)[0] + block.split("def _int_field(", 1)[1]
    for forbidden in ("trade_lock", "open(", "requests.", "_place_", "cancel", "_safe_append_jsonl",
                      "json.dump(", "relay", "deepseek", "set_pause", "execution_paused\"] ="):
        assert forbidden not in block, forbidden
    loop = src.split("def microstructure_capture_loop():", 1)[1].split("\ndef ", 1)[0]
    assert "_TILE_TAPE_RING.append(row)" in loop


def test_ready_entry_policy_view_states_the_b2_signal_clock_without_touching_the_registry(bot):
    reg = bot.ACTIVE_TILE_REGISTRY
    b2 = bot._ready_entry_policy_view(reg["FAMILY_GSB2_REGIME_SWITCHER"]["entry_policy"])
    assert b2["signal_clock"] == "SHARED_AI_CALL + BAR_CLOSE_3M_CVD_EVALUATOR" and b2["signal_clock_derived"] is True
    assert "signal_clock" not in reg["FAMILY_GSB2_REGIME_SWITCHER"]["entry_policy"]
    gs1 = bot._ready_entry_policy_view(reg[GS1]["entry_policy"])
    assert gs1["signal_clock"] == "PER_SECOND_CROSS_VENUE_EVALUATOR" and "signal_clock_derived" not in gs1
    assert bot._ready_entry_policy_view(reg[HA]["entry_policy"])["signal_clock"] == "SHARED_AI_CALL"


def test_closed_row_exposes_recorded_stage_timestamps():
    close = CUTOFF + 900
    ev = {"schema": "trade_event_timestamps_v1", "signal_ts": close - 700.0, "order_sent_ts": close - 698.5,
          "fill_ts": close - 697.0, "fill_ts_basis": "RECORDED_AT_PAPER_FILL",
          "exit_trigger_ts": close - 0.8, "exit_trigger_source": "TICK", "exit_trigger_price": 85100.0,
          "exit_fill_ts": close - 0.2}
    row = _rows([_closed("t-ev", GS1, close_ts=close, event_timestamps=ev)])[0]
    assert row["order"]["submitted_ts"] == close - 698.5
    assert row["fill"]["fill_ts"] == close - 697.0 and row["fill"]["fill_ts_basis"] == "RECORDED_AT_PAPER_FILL"
    assert row["exit"]["exit_trigger_ts"] == close - 0.8 and row["exit"]["exit_fill_ts"] == close - 0.2
    t = row["timing"]
    assert t["basis"] == "RECORDED_EVENT_TIMESTAMPS"
    assert t["signal_to_sent_sec"] == 1.5 and t["sent_to_fill_sec"] == 1.5
    assert t["exit_trigger_to_fill_sec"] == pytest.approx(0.6)


def test_closed_row_without_event_timestamps_never_back_calculates_timing():
    row = _rows([_closed("t-old", GS1, close_ts=CUTOFF + 900)])[0]
    assert row["order"]["submitted_ts"] is None
    assert row["fill"]["fill_ts_basis"] == "CLOSE_TS_MINUS_DURATION"
    assert row["timing"]["basis"] == "NOT_RECORDED" and row["timing"]["fill_ts"] is None
    assert row["exit"]["exit_trigger_ts"] is None

def test_tile_trade_row_surfaces_regime_at_signal_from_adaptive_entry_decision():
    """GS-05/GS-06: Health Monitor reads regime gates off trades[].trigger."""
    raw = _closed(
        "gs5-1", GS5, close_ts=CUTOFF + 50, bp=4.0, net=0.01,
        adaptive_entry_decision={
            "regime_at_signal": "VIOLENT",
            "regime_cell": "VIOLENT",
            "exit_profile": "GS01_VIOLENT",
            "action": "TAKER",
            "reason": "REGIME_VIOLENT_TAKER",
            "trigger_kind": "PREMIUM",
        },
    )
    row = _rows([raw])[0]
    trigger = row["trigger"]
    assert trigger["regime_at_signal"] == "VIOLENT"
    assert trigger["regime_cell"] == "VIOLENT"
    assert trigger["exit_profile"] == "GS01_VIOLENT"
    assert trigger["action"] == "TAKER"
    # Fallback when only regime is present
    raw2 = _closed("gs6-1", GS6, close_ts=CUTOFF + 60, bp=1.0, net=0.002,
                   adaptive_entry_decision={"regime": "QUIET", "action": "TAKER"})
    assert _rows([raw2])[0]["trigger"]["regime_at_signal"] == "QUIET"


def test_counters_since_boot_exposes_stand_aside_shadow_counts():
    """GS-05 QUIET stand-aside / GS-06 shadow counts live under since_boot.adaptive_entry."""
    adaptive = {
        "decisions": 12, "stand_aside": 5, "shadow_stand_aside": 5, "submit": 7,
        "by_action": {"STAND_ASIDE": 5, "TAKER": 7},
        "by_regime": {"QUIET": 5, "TREND": 4, "VIOLENT": 3},
        "by_reason": {"REGIME_QUIET_STANDS_ASIDE": 5},
    }
    out = mt.counters_view(
        GS5, rows=[], route_counts={}, xvl_lane=None, opportunity=None,
        boot_id="boot-test", adaptive_lane=adaptive,
    )
    ae = out["since_boot"]["adaptive_entry"]
    assert ae["stand_aside"] == 5 and ae["shadow_stand_aside"] == 5 and ae["submit"] == 7
    assert ae["by_regime"]["QUIET"] == 5


# ---- Strategist read paths (17 Oct): reconcile route, public fill provenance, fresh_only on tile 6/7 ----------
def test_reconcile_route_is_monitor_read_only_and_fly_side_only(bot, monkeypatch):
    monkeypatch.setattr(bot, "_load_archive_receipt",
                        lambda: {"restore_test": {"checked": 23, "ok": 2, "bad": 21, "ts": "2026-10-17T03:32:09+1100"}})
    path = "/api/monitor/reconcile"
    client = bot.app.test_client()
    assert client.get(path, environ_base=REMOTE).status_code == 401
    assert client.get(path, headers={"Authorization": f"Bearer {ADMIN}"}, environ_base=REMOTE).status_code == 401
    assert client.post(path, headers={"X-Bot-Admin-Token": ADMIN}, environ_base=REMOTE).status_code == 405
    for headers in ({"X-Bot-Admin-Token": ADMIN}, {"Authorization": f"Bearer {MONITOR}"}):
        r = client.get(path, headers=headers, environ_base=REMOTE)
        assert r.status_code == 200 and r.headers["Cache-Control"] == "no-store"
        body = r.get_json()
        assert body["schema"] == "monitor_reconcile_v1" and body["read_only"] is True and body["epoch_id"] == EPOCH
        assert body["sources"] == {"fly": "OK", "mirror": "NOT_REPORTED_TO_FLY", "analyzer": "NOT_REPORTED_TO_FLY"}
        assert "archive_restore" not in body  # retired 18 Oct (no permanent archive)
        tiles = {t["lane"]: t for t in body["per_tile"]}
        assert tiles[GS1]["fly_n"] == 1 and tiles[HA]["fly_n"] >= 1
        assert all(t["mirror_n"] is None and t["analyzer_n"] is None and t["ok"] is None for t in tiles.values())
    assert path not in bot._READ_ONLY_GET_PATHS


def test_public_state_exposes_fill_provenance_but_still_strips_strategy_fields(bot):
    row = {"ts": "t", "trade_id": "gs1-x", "dir": "LONG", "entry": 1.0, "exit": 2.0, "net_pnl_usd": 0.1,
           "fill_model": "BOOK10L_WALK_AT_SIGNAL_PLUS_DELAY", "fill_model_version": 1, "delay_s": 1.5,
           "exit_reason": "GS_ATR_TAKE_PROFIT", "research_lane": GS1, "ai_band": "60-70"}
    out = bot._sanitize_public_state({"trades": [row]})["trades"][0]
    assert out["fill_model"] == "BOOK10L_WALK_AT_SIGNAL_PLUS_DELAY" and out["fill_model_version"] == 1
    assert out["delay_s"] == 1.5
    assert "exit_reason" not in out and "research_lane" not in out and "ai_band" not in out


DISLOC_BASE = "FAMILY_DISLOC_BFX_DISLOCATION_1M"


def test_disloc_monitor_route_and_row_source_removed(bot):
    """Tiles 6/7 removal (23 Oct 2026): /api/monitor/tiles/disloc is gone and the trades route has no
    DISLOC row source any more (historical ledgers stay on disk, read by nothing on this path)."""
    assert _get(bot, "/api/monitor/tiles/disloc", {"X-Bot-Admin-Token": ADMIN}).status_code == 404
    assert not hasattr(bot, "_disloc_tile_trade_rows") and not hasattr(bot, "_disloc_epoch_since")
    assert DISLOC_BASE not in bot.ACTIVE_TILE_ORDER


def test_switches_route_and_api_build_use_the_same_live_state(bot, monkeypatch):
    monkeypatch.setattr(bot, "_build_features_live_state", lambda: {"effective_live": False, "force_paper_mode": True})
    monkeypatch.setattr(bot, "monitor_tile_switch_rows", lambda: [
        {"lane": "G", "number": 3, "label": "GS-01", "retired": False, "paper_on": True, "live_orders_on": True}])
    sw = _get(bot, "/api/monitor/tiles/switches", {"X-Bot-Admin-Token": ADMIN}).get_json()
    assert "live copy DISARMED (force_paper)" in sw["build_features"]
    assert bot.current_build_features_text() == sw["build_features"]
    monkeypatch.setattr(bot, "_build_features_live_state", lambda: {"effective_live": False, "error": "X"})
    assert "live arming state unknown" in bot.current_build_features_text()


def test_state_trade_projections_carry_the_entry_pricing_receipt(bot):
    row = {"trade_id": "gs1-p", "research_lane": GS1, "ts": "2026-10-17T00:00:00+00:00", "entry": 1.0, "exit": 2.0,
           "fill_model": "BOOK10L_WALK_AT_SIGNAL_PLUS_DELAY", "fill_model_version": 1, "delay_s": 1.5,
           "delay_source": "default_until_n10", "net_pnl_usd": 0.1}
    recent, relay, _ = bot._snapshot_relay_trade_projections_locked(0.0, trades_src=[row])
    for projected in (recent[0], relay[0], bot._slim_trade_for_dashboard(row)):
        assert projected["fill_model"] == "BOOK10L_WALK_AT_SIGNAL_PLUS_DELAY"
        assert projected["fill_model_version"] == 1 and projected["delay_s"] == 1.5
        assert projected["delay_source"] == "default_until_n10"
    pub = bot._sanitize_public_state({"trades": [bot._slim_trade_for_dashboard(row)]})["trades"][0]
    assert pub["fill_model_version"] == 1 and pub["delay_s"] == 1.5 and "research_lane" not in pub


# ---- tile-integrity in the monitor allowlist; /api/monitor/fill-provenance --------------------------------------
def test_tile_integrity_is_readable_with_the_monitor_bearer(bot):
    client = bot.app.test_client()
    path = "/api/monitor/tile-integrity"
    assert client.get(path, environ_base=REMOTE).status_code == 401
    assert client.get(path, headers={"Authorization": f"Bearer {MONITOR}"}, environ_base=REMOTE).status_code == 200
    assert client.get(path, headers={"X-Bot-Admin-Token": ADMIN}, environ_base=REMOTE).status_code == 200
    assert client.post(path, headers={"X-Bot-Admin-Token": ADMIN}, environ_base=REMOTE).status_code == 405
    assert path in bot._MONITOR_TILE_PATHS and path not in bot._READ_ONLY_GET_PATHS


def test_fill_provenance_route_reports_pricing_shadow_forced_and_snapshot_coverage(bot, monkeypatch):
    book = {"s": 1, "a": [[100.0, 1.0]], "b": [[99.0, 1.0]]}
    v1 = {"fill_model": "BOOK10L_WALK_AT_SIGNAL_PLUS_DELAY", "fill_model_version": 1, "delay_s": 1.5,
          "delay_source": "default_until_n10", "exit_status": "PAPER_FILL", "fresh_eligible": True,
          "depth_snapshot_signal": book, "depth_snapshot_fill": book, "exit_depth_snapshot_fill": book}
    rows = [
        _closed("p-ok", GS1, close_ts=CUTOFF + 100, bp=1.0, net=0.01, **v1),
        _closed("p-nosnap", GS1, close_ts=CUTOFF + 110, bp=1.0, net=0.01, **{**v1, "exit_depth_snapshot_fill": None}),
        _closed("p-forced", GS1, close_ts=CUTOFF + 120, bp=-1.0, net=-0.01,
                **{**v1, "exit_status": "FORCED_NO_BOOK", "fresh_eligible": False, "fresh_data_excluded": True,
                   "exit_depth_snapshot_fill": None}),
        _closed("p-legacy", GS1, close_ts=CUTOFF + 130, bp=1.0, net=0.01, fill_model="AI_DIRECT"),
    ]
    monkeypatch.setattr(bot, "trades", rows)
    monkeypatch.setattr(bot, "_fresh_start_epoch", lambda: {"epoch_ts": CUTOFF})
    shadow = [{"event": "PAPER_SHADOW_NO_FILL", "research_lane": GS1, "status": "NO_FILL_IOC_CAP", "ts": CUTOFF + 5},
              {"event": "PAPER_SHADOW_NO_FILL", "research_lane": GS1, "status": "REJECT_NO_BOOK", "ts": CUTOFF + 6},
              {"event": "PAPER_SHADOW_NO_FILL", "research_lane": GS1, "status": "REJECT_NO_BOOK", "ts": CUTOFF - 99}]
    monkeypatch.setattr(bot, "_read_jsonl_tail", lambda path, n: list(shadow))
    path = "/api/monitor/fill-provenance"
    client = bot.app.test_client()
    assert client.get(path, environ_base=REMOTE).status_code == 401
    assert client.post(path, headers={"X-Bot-Admin-Token": ADMIN}, environ_base=REMOTE).status_code == 405
    r = client.get(path, headers={"Authorization": f"Bearer {MONITOR}"}, environ_base=REMOTE)
    assert r.status_code == 200 and r.headers["Cache-Control"] == "no-store"
    body = r.get_json()
    assert body["schema"] == "monitor_fill_provenance_v1" and body["read_only"] is True
    assert body["epoch"]["fresh_epoch_ts"] == CUTOFF and body["epoch"]["fresh_epoch_stamped"] is True
    t = {x["lane"]: x for x in body["per_tile"]}[GS1]
    assert t["closed"] == 4 and t["fresh_eligible"] == 2 and t["not_fresh"] == 2
    assert t["legacy_rows"] == 1 and t["v1_rows"] == 3
    assert t["fill_model_counts"]["BOOK10L_WALK_AT_SIGNAL_PLUS_DELAY"] == 3 and t["fill_model_counts"]["AI_DIRECT"] == 1
    assert t["fill_model_version_counts"] == {"1": 3, "none": 1} and t["delay_source_counts"]["default_until_n10"] == 3
    assert t["forced_counts"] == {"FORCED_NO_BOOK": 1}
    assert t["shadow"]["no_fill"] == 1 and t["shadow"]["reject"] == 1  # the pre-epoch reject is not counted
    assert t["snapshot_coverage"] == {"entry_signal": 3, "entry_fill": 3, "exit_fill": 1, "of_v1_rows": 3,
                                      "legs_without_snapshot": 2}
    assert {m["trade_id"] for m in t["rows_with_missing_snapshots"]} == {"p-nosnap", "p-forced"}
    assert "depth_snapshot_signal" not in json.dumps(body)  # no raw books on this route
    assert body["shadow_truncated"] is False  # the fake tail is tiny and the file is absent
    import os as _os
    monkeypatch.setattr(_os.path, "getsize", lambda p: 3 * 1024 * 1024 if str(p).endswith("paper_ioc_no_fill_shadow.jsonl") else 0)
    r2 = client.get(path, headers={"Authorization": f"Bearer {MONITOR}"}, environ_base=REMOTE)
    assert r2.get_json()["shadow_truncated"] is True


def test_closed_row_carries_trigger_vs_book_and_tape_age():
    # #807: the exit fired on a stale ticker bid 85000 while the 1 s book bid at the trigger second was 85085
    close = CUTOFF + 900
    ev = {"schema": "trade_event_timestamps_v1", "signal_ts": close - 700.0, "exit_trigger_ts": close - 2.0,
          "exit_trigger_source": "POSITION_MANAGER", "exit_trigger_price": 85000.0, "exit_trigger_bbo_ts": close - 3.8,
          "exit_fill_ts": close - 0.2}
    book = {"s": int(close - 2.0), "b": [[85085, 0.03], [85080, 0.1]], "a": [[85095, 0.05]]}
    row = _rows([_closed("t-trig", GS1, close_ts=close, event_timestamps=ev, exit_depth_snapshot_signal=book)])[0]
    assert row["exit"]["trigger_vs_book_bp"] == round((85085 / 85000 - 1) * 1e4, 3)
    assert row["exit"]["trigger_tape_age_s"] == 1.8
    bare = _rows([_closed("t-bare", GS1, close_ts=close)])[0]
    assert bare["exit"]["trigger_vs_book_bp"] is None and bare["exit"]["trigger_tape_age_s"] is None


def test_exit_lag_arm_gate_route(bot, monkeypatch):
    """RK ruling 25 Oct + option (a): scope from #868, admin / exchange-stop ids listed, UNPROVEN while n < 10, GS-01 not
    blocked, legacy 24 Oct view kept under legacy_v1."""
    import exit_lag_regression as elr
    t = elr.GATE_SCOPE_START_TS + 5000

    def ev(rx, dec):
        return {"exit_trigger_receive_ts": rx, "exit_trigger_receive_ts_source": "WS_FRAME" if rx else None,
                "exit_decision_ts": dec, "exit_fill_ts": dec + 0.5}
    rows = [{"trade_id": "a", "event_timestamps": ev(None, t)},
            {"trade_id": "adm", "exit_reason": "ADMIN_FORCE_FLAT", "event_timestamps": ev(None, t + 5)},
            {"trade_id": "xs", "exit_reason": "EXCHANGE_STOP_30BP", "event_timestamps": ev(None, t + 6)},
            {"trade_id": "b", "event_timestamps": ev(t + 9.99, t + 10)},
            {"trade_id": "c", "event_timestamps": ev(t + 19.98, t + 20)}]
    chains = [{"correlation_id": "gs1-live", "closed": True, "exit_order_class": "BOT_SENT", "exit_reason": "TP",
               "exit_fill_at_ts": t + 30, "exit_ack_at_ts": t + 29}]
    monkeypatch.setattr(bot, "_exit_latency_closed_trade_rows", lambda since: rows)
    monkeypatch.setitem(bot._LIVE_GUARDIAN_STATE, "exit_lag_chains", (t + 40, chains))
    r = _get(bot, "/api/monitor/exit-lag", {"Authorization": f"Bearer {MONITOR}"}, required=3)
    assert r.status_code == 200
    body = r.get_json()
    assert body["schema"] == "exit_lag_arm_gate_v2" and body["gate"] == "UNPROVEN" and body["arm_eligible"] is False
    assert body["gs01_arm_blocked_by_exit_lag"] is False and body["gs01_unproven_policy"]["max_notional_usd"] == 0.25
    assert body["threshold_s"] is None and body["n_exchange_stops"] == 0 and body["real_exchange_figure"]["source_ids"] == []
    assert body["admin_excluded_ids"] == ["adm"] and body["exchange_filled_ids"] == ["xs"]
    assert body["consecutive_measured_unjudged"] == 2 and body["run_broken_by"]["trade_id"] == "a"
    assert body["scope"]["since_ts"] == elr.GATE_SCOPE_START_TS and body["scope"]["reason"]
    assert body["live_exit_stamps_missing"] == ["gs1-live"]   # no paper twin => receive/decision/send missing
    assert body["legacy_v1"]["schema"] == "exit_lag_arm_gate_v1" and body["legacy_v1"]["threshold_s"] == 0.062
    assert MONITOR not in r.get_data(as_text=True) and ADMIN not in r.get_data(as_text=True)
    assert _get(bot, "/api/monitor/exit-lag", {"Authorization": f"Bearer {MONITOR}"}, required="x").status_code == 400


def test_exit_lag_gate_enforced_on_arm_and_live_entry(bot, monkeypatch):
    """HM R2 (a): the arm (tile ON) and live-entry / live-order paths read the gate flags, fail closed when the gate is
    unreadable, and refuse a GS-01 order above $0.25 margin with an [ALERT]."""
    import inspect
    import exit_lag_regression as elr
    unproven = elr.rk_gate([])
    monkeypatch.setattr(bot, "_exit_lag_gate_now", lambda: dict(unproven))
    bot._EXIT_LAG_ALERTS.clear()
    assert bot._exit_lag_live_block("FAMILY_GS01_XV_PREMIUM_ATR_TP", 0.25, where="TILE_ARM") is None
    assert bot._exit_lag_live_block("FAMILY_GS01_XV_PREMIUM_ATR_TP", 0.5, where="LIVE_ORDER") == elr.GS01_SIZE_BLOCK
    assert bot._exit_lag_live_block("FAMILY_GSB1_CVD_DIV_REGIME", 0.25, where="TILE_ARM") == elr.ARM_BLOCK_NOT_ELIGIBLE
    monkeypatch.setattr(bot, "_exit_lag_gate_now", lambda: {**unproven, "gs01_arm_blocked_by_exit_lag": True})
    assert bot._exit_lag_live_block("FAMILY_GS01_XV_PREMIUM_ATR_TP", 0.25, where="LIVE_ENTRY") == elr.ARM_BLOCK_GS01
    monkeypatch.setattr(bot, "_exit_lag_gate_now", lambda: None)
    assert bot._exit_lag_live_block("FAMILY_GS01_XV_PREMIUM_ATR_TP", 0.25, where="LIVE_ENTRY") == elr.ARM_BLOCK_UNREADABLE
    assert [a["reason"] for a in bot._EXIT_LAG_ALERTS] == [elr.GS01_SIZE_BLOCK, elr.ARM_BLOCK_NOT_ELIGIBLE,
                                                           elr.ARM_BLOCK_GS01, elr.ARM_BLOCK_UNREADABLE]
    # live entry: refused before any provider / chain work
    monkeypatch.setattr(bot, "_exit_lag_live_block", lambda *a, **k: elr.ARM_BLOCK_GS01)
    ok, reasons, info = bot._live_tile_admission("FAMILY_GS01_XV_PREMIUM_ATR_TP", "gs1-abc", 1.0)
    assert ok is False and reasons == [elr.ARM_BLOCK_GS01] and info["exit_lag_block"] == elr.ARM_BLOCK_GS01
    # tile-ON route checks before request_on; the signed order is checked before it is attached
    route = inspect.getsource(bot.api_bitfinex_tile_live_orders)
    assert route.index('_exit_lag_live_block(lane, _live_copy.MAX_MARGIN_USD, where="TILE_ARM")') < route.index("sw.request_on(")
    stamp = inspect.getsource(bot._live_copy_stamp_payload)
    assert stamp.index('approval.get("max_margin_usd"), where="LIVE_ORDER"') < stamp.index('payload["live_copy_approval"] = approval')


def test_exit_lag_gate_now_fails_closed(bot, monkeypatch):
    import exit_lag_regression as elr
    monkeypatch.setattr(bot, "_exit_latency_closed_trade_rows", lambda since: (_ for _ in ()).throw(RuntimeError("x")))
    assert bot._exit_lag_gate_now() is None
    assert elr.arm_block_reason(bot._exit_lag_gate_now(), "gs1") == elr.ARM_BLOCK_UNREADABLE
    monkeypatch.setattr(bot, "_exit_latency_closed_trade_rows", lambda since: [])
    assert bot._exit_lag_gate_now()["schema"] == "exit_lag_arm_gate_v2"


# ---- HM R3 B2 (#876): Flask-client behavioural tests; each fails if its barrier is changed to `if False:` ----
import types as _types

_GS01_LANE = "FAMILY_GS01_XV_PREMIUM_ATR_TP"


class _Sw:
    def __init__(self):
        self.on = []

    def request_on(self, lane, **kw):
        self.on.append(lane)

    def request_off(self, lane, **kw):
        pass

    def why_not_armed(self, lane, **kw):
        return {"eligible": True, "denials": []}

    def snapshot(self, lane):
        return {"bitfinex_live_orders": lane in self.on}


def _open_gates(bot, monkeypatch):
    """Every gate on the arm routes passes, so only the barrier under test can refuse."""
    sw = _Sw()
    enabled = []
    boot = _types.SimpleNamespace(snapshot=lambda: {"lanes": {}}, disable=lambda **k: {},
                                  enable=lambda lane, **k: enabled.append(lane) or (True, {}))
    elig = _types.SimpleNamespace(is_eligible=lambda lane: True, set=lambda *a, **k: (True, {}))
    for name, val in {"_get_bfx_live_switch": lambda: sw,
                      "_refresh_bitfinex_exposure_audit": lambda: {}, "_bitfinex_readiness_context": lambda: {},
                      "_bitfinex_size_checks_for_lane": lambda lane, ctx: {}, "_bitfinex_master_state": lambda: {},
                      "_get_live_copy_eligibility": lambda: elig, "_get_live_copy_bootstrap": lambda: boot,
                      "_live_guardian_heartbeat_stale": lambda: False, "_live_guardian_arm_block": lambda: None,
                      "_live_guardian_tile_arm_block": lambda lane: None,
                      "_exit_lag_live_block": lambda *a, **k: None}.items():
        monkeypatch.setattr(bot, name, val)
    return sw, enabled


def _non_allowlisted_active_lane():
    from combo_pathway_config import ACTIVE_TILE_ORDER
    import live_tile_gate as ltg_
    return next(l for l in ACTIVE_TILE_ORDER if ltg_.operator_eligibility_refusal(l) is not None)


def test_b2_tile_live_orders_route_allowlist_refuses(bot, monkeypatch):
    sw, _ = _open_gates(bot, monkeypatch)
    c = bot.app.test_client()
    for lane in (_non_allowlisted_active_lane(), "DISLOC", "ROUTER", "H-A", "zz-anything"):
        r = c.post(f"/api/bitfinex/tiles/{lane}/live-orders", json={"enabled": True}, environ_base=REMOTE, headers={"X-Bot-Admin-Token": ADMIN})
        assert r.status_code == 409 and r.get_json()["error"] == "LANE_NOT_ARM_ALLOWLISTED", (lane, r.get_json())
    assert sw.on == []
    r = c.post(f"/api/bitfinex/tiles/{_GS01_LANE}/live-orders", json={"enabled": True}, environ_base=REMOTE, headers={"X-Bot-Admin-Token": ADMIN})
    assert r.status_code == 200 and sw.on == [_GS01_LANE]   # control: the open path does reach request_on


def test_b2_protection_bootstrap_allowlist_refuses(bot, monkeypatch):
    _, enabled = _open_gates(bot, monkeypatch)
    c = bot.app.test_client()
    lane = _non_allowlisted_active_lane()
    r = c.post("/api/live-copy/protection-bootstrap", json={"enabled": True, "lane": lane}, environ_base=REMOTE, headers={"X-Bot-Admin-Token": ADMIN})
    assert r.status_code == 409 and r.get_json()["results"][lane]["error"] == "LANE_NOT_ARM_ALLOWLISTED"
    assert enabled == []
    r = c.post("/api/live-copy/protection-bootstrap", json={"enabled": True, "lane": _GS01_LANE}, environ_base=REMOTE, headers={"X-Bot-Admin-Token": ADMIN})
    assert r.status_code == 200 and enabled == [_GS01_LANE]


def test_b2_tile_arm_exit_lag_block_refuses_before_request_on(bot, monkeypatch):
    import exit_lag_regression as elr
    sw, _ = _open_gates(bot, monkeypatch)
    seen = []
    monkeypatch.setattr(bot, "_exit_lag_live_block",
                        lambda lane, margin, where, **k: seen.append(where) or elr.ARM_BLOCK_GS01)
    r = bot.app.test_client().post(f"/api/bitfinex/tiles/{_GS01_LANE}/live-orders", json={"enabled": True},
                                   environ_base=REMOTE, headers={"X-Bot-Admin-Token": ADMIN})
    assert r.status_code == 409 and r.get_json()["error"] == elr.ARM_BLOCK_GS01 and seen == ["TILE_ARM"]
    assert sw.on == []


# ---- HM R4 B1 (#876): the REAL _live_total_loss_chains_strict fails closed (no stub of the chain source) ----
def test_r4_real_total_loss_chain_source_fails_closed(bot, monkeypatch):
    import live_copy_monitor as lcm
    import live_copy_monitor_api as lcma
    monkeypatch.setitem(bot._LIVE_GUARDIAN_STATE, "red_alert", None)

    def boom():
        raise RuntimeError("provider down")
    monkeypatch.setitem(lcma._CTX, "provider", boom)
    assert bot._live_guardian_arm_block() == "LIVE_TOTAL_LOSS_UNREADABLE"
    monkeypatch.setitem(lcma._CTX, "provider", lambda: ["not", "a", "dict"])
    assert bot._live_guardian_arm_block() == "LIVE_TOTAL_LOSS_UNREADABLE"
    monkeypatch.setitem(lcma._CTX, "provider", lambda: {"approvals": [], "reports": []})

    def bad_chains(*a, **k):
        raise ValueError("corrupt journal")
    monkeypatch.setattr(lcm, "build_chains", bad_chains)
    assert bot._live_guardian_arm_block() == "LIVE_TOTAL_LOSS_UNREADABLE"


# ---- HM R4 B2 (#876): LIVE_ORDER exit-lag / GS-01 $0.25 barrier, behavioural ----
def _stamp_env(bot, monkeypatch, margin):
    sw = _Sw()
    appended = []
    ns = _types.SimpleNamespace
    for name, val in {
        "_platform_relay_lane_for_event": lambda tid, rl=None: _GS01_LANE,
        "_get_live_copy_eligibility": lambda: ns(is_eligible=lambda lane: True),
        "_get_bfx_live_switch": lambda: _types.SimpleNamespace(snapshot=lambda lane: {}, evaluate=lambda *a, **k: {}),
        "_get_live_copy_output": lambda: ns(snapshot=lambda: {}),
        "_bitfinex_readiness_context": lambda: {"reduce_only_supported": True},
        "_get_live_copy_protection": lambda: ns(flags=lambda lane: {"stop_coverage_verified": True}),
        "_bitfinex_size_checks_for_lane": lambda *a, **k: {},
        "_live_copy_paper_lock_active": lambda: False,
        "_get_live_copy_decisions": lambda: None,
        "_get_live_copy_approvals": lambda: ns(append=appended.append),
    }.items():
        monkeypatch.setattr(bot, name, val)
    monkeypatch.setattr(bot._live_copy, "stamp_or_block", lambda **k: (
        {"entry_allowed": True, "max_margin_usd": margin, "trade_id": k["trade_id"], "research_lane": k["lane"]}, []))
    return appended


def test_r4_live_order_site_withholds_gs01_over_cap(bot, monkeypatch):
    import exit_lag_regression as elr
    seen = []
    real = bot._exit_lag_live_block
    appended = _stamp_env(bot, monkeypatch, 0.5)
    monkeypatch.setattr(bot, "_exit_lag_live_block",
                        lambda lane, margin, where, **k: seen.append((where, margin)) or real(lane, margin, where=where, **k))
    out = bot._live_copy_stamp_payload({"event": "ORDER_PLACED", "trade_id": "gs1-r4", "research_lane": _GS01_LANE})
    assert "live_copy_approval" not in out and appended == []
    assert seen == [("LIVE_ORDER", 0.5)]
    xl = real(_GS01_LANE, 0.5, where="LIVE_ORDER", trade_id="gs1-r4")
    assert xl and out["live_copy_denied"] == [xl]
    # forced-blocked gate at a compliant margin
    monkeypatch.setattr(bot, "_exit_lag_live_block", lambda *a, **k: elr.ARM_BLOCK_UNREADABLE)
    _stamp_env(bot, monkeypatch, 0.25)
    out = bot._live_copy_stamp_payload({"event": "ORDER_PLACED", "trade_id": "gs1-r4b", "research_lane": _GS01_LANE})
    assert "live_copy_approval" not in out and out["live_copy_denied"] == [elr.ARM_BLOCK_UNREADABLE]
    # control: gate open -> the approval is attached (so the barrier, not the stubs, withholds above)
    monkeypatch.setattr(bot, "_exit_lag_live_block", lambda *a, **k: None)
    appended = _stamp_env(bot, monkeypatch, 0.25)
    out = bot._live_copy_stamp_payload({"event": "ORDER_PLACED", "trade_id": "gs1-r4c", "research_lane": _GS01_LANE})
    assert out.get("live_copy_approval", {}).get("entry_allowed") is True and len(appended) == 1
