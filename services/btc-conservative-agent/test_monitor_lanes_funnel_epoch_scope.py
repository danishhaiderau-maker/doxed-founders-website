"""/api/monitor/lanes and the pipeline funnel are epoch-scoped and exclude forced closes.

Freeze21b audit X4/X5: /api/monitor/lanes counted the 20:55 ADMIN_MANUAL_CLOSE
flatten (H-A 3 closes / 3 L / -$0.16) while tile_route_counts excluded it, and
pipeline_funnel_counters were since-boot counters fed by one submit path only
(FILLED 1 against 28 epoch fills).
"""
import bot
import monitor_api

HA = "FAMILY_COMMITTED_FADE_TAKER_90"
CTRL = "FAMILY_RANDOM_CONTROL_TAKER_90"
EPOCH_START = 1_791_100_000.0


def _row(trade_id, lane, net, reason, ts):
    return {"trade_id": trade_id, "research_lane": lane, "net_pnl_usd": net, "exit_reason": reason,
            "ts": bot.datetime.fromtimestamp(ts, bot.timezone.utc).isoformat(), "dir": "SHORT",
            "entry": 85125.0, "exit": 85125.0 - net / (25.0 / 85125.0), "execution_qty": 25.0 / 85125.0,
            "margin_usdt": 0.25, "leverage": 100, "pnl": round(net / 0.25 * 100, 2)}


def test_monitor_lanes_exclude_admin_closes_and_pre_epoch_rows(monkeypatch):
    rows = [
        _row("cft-old", HA, 0.05, "PATH_END_90M", EPOCH_START - 3600),          # previous epoch
        _row("cft-a", HA, -0.06, "ADMIN_MANUAL_CLOSE", EPOCH_START + 4000),     # 20:55 flatten
        _row("cft-b", HA, -0.06, "ADMIN_MANUAL_CLOSE", EPOCH_START + 4001),
        _row("cft-c", HA, -0.04, "ADMIN_MANUAL_CLOSE", EPOCH_START + 4002),
        _row("rnd-a", CTRL, 0.04, "PROFIT_PROTECTION_STOP", EPOCH_START + 3000),
        _row("rnd-b", CTRL, -0.06, "ADMIN_MANUAL_CLOSE", EPOCH_START + 4003),
    ]
    monkeypatch.setattr(bot, "trades", rows)
    monkeypatch.setattr(bot, "_showcase_trade_session_start", lambda: EPOCH_START)
    shaped, start = bot._monitor_lane_rows([HA, CTRL])
    assert start == EPOCH_START
    assert shaped[HA] == [] and len(shaped[CTRL]) == 1
    assert bot._MONITOR_LANE_FORCED_EXCLUDED == {HA: 3, CTRL: 1}
    stats = monitor_api.lane_stats(shaped[HA])
    assert (stats["closes"], stats["losses"], stats["net_usd"]) == (0, 0, 0.0)


def test_monitor_lanes_payload_declares_scope(monkeypatch):
    monkeypatch.setattr(bot, "trades", [_row("rnd-a", CTRL, 0.04, "PROFIT_PROTECTION_STOP", EPOCH_START + 1),
                                        _row("rnd-b", CTRL, -0.06, "ADMIN_MANUAL_CLOSE", EPOCH_START + 2)])
    monkeypatch.setattr(bot, "_showcase_trade_session_start", lambda: EPOCH_START)
    monkeypatch.setattr(bot, "_current_epoch_boundary", lambda force=False: (EPOCH_START, "data_epoch"))
    payload = bot._monitor_lanes_payload(EPOCH_START + 10)
    assert payload["scope"]["include_forced"] is False
    assert payload["scope"]["trade_scope"] == "SIGNED_FRESH_EPOCH"
    assert "ADMIN_MANUAL_CLOSE" in payload["scope"]["stats_excluded_exit_reasons"]
    ctrl = next(row for row in payload["lanes"] if row["lane"] == CTRL)
    assert ctrl["closes"] == 1 and ctrl["forced_closes_excluded"] == 1


def test_epoch_funnel_sums_tile_route_counts(monkeypatch):
    monkeypatch.setattr(bot, "_current_epoch_boundary", lambda force=False: (EPOCH_START, "data_epoch"))
    counts = {
        HA: {"selected_calls": 6, "pending": 0, "open": 0, "closed": 0, "expired": 0},
        CTRL: {"selected_calls": 7, "pending": 0, "open": 1, "closed": 2, "expired": 0},
        "FAMILY_GS01_XV_PREMIUM_ATR_TP": {"selected_calls": 7, "pending": 1, "open": 0, "closed": 4, "expired": 1},
    }
    funnel = bot._epoch_pipeline_funnel(counts, {"AI_CALLED": 40, "FILLED": 1})
    assert funnel["FILLED"] == 7 and funnel["CLOSED"] == 6 and funnel["OPEN"] == 1
    assert funnel["ORDER_SUBMITTED"] == 9 and funnel["EXPIRED"] == 1 and funnel["SELECTED_CALLS"] == 20
    assert funnel["scope"]["epoch_cutoff_source"] == "data_epoch"
    assert funnel["since_boot_ai"] == {"AI_CALLED": 40}


def test_publish_keeps_since_boot_counters_under_their_own_key(monkeypatch):
    monkeypatch.setattr(bot, "_current_epoch_boundary", lambda force=False: (EPOCH_START, "data_epoch"))
    snap = {"pipeline_funnel_counters": {"AI_CALLED": 3, "FILLED": 1}}
    counts = {CTRL: {"selected_calls": 2, "pending": 0, "open": 0, "closed": 2, "expired": 0}}
    bot._publish_epoch_pipeline_funnel(snap, counts)
    assert snap["pipeline_funnel_counters"]["FILLED"] == 2
    assert snap["pipeline_funnel_counters_since_boot"] == {"AI_CALLED": 3, "FILLED": 1}
    bot._publish_epoch_pipeline_funnel(snap, counts)  # idempotent on an already-published snapshot
    assert snap["pipeline_funnel_counters_since_boot"] == {"AI_CALLED": 3, "FILLED": 1}
    display = bot.build_dashboard_display(snap)["display_pipeline"]["funnel"]
    assert display["FILLED"] == 2 and display["AI_CALLED"] == 3


def test_status_reads_the_epoch_funnel_from_the_state_snapshot(monkeypatch):
    monkeypatch.setitem(bot._api_state_cache, "payload", {
        "pipeline_funnel_counters": {"schema": bot.EPOCH_PIPELINE_FUNNEL_SCHEMA, "FILLED": 28}})
    assert bot._status_epoch_pipeline_funnel()["FILLED"] == 28
    monkeypatch.setitem(bot._api_state_cache, "payload", None)
    assert bot._status_epoch_pipeline_funnel()["status"] == "STATE_SNAPSHOT_WARMING"
