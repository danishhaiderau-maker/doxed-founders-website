"""Items 2/3/6 + Strategist goal (a)-(e): realism-stamped per-tile table on countable rows only, strict epoch
hard-reject, source_revision, and a generation that is VALID with exit-ladder sim and cross-world absent."""
from research.generation_receipt import EXPECTED_DATA_EPOCH_ID, build_generation_receipt
from research.tile_realism_table import build_tile_table, cluster_ci

EPOCH = EXPECTED_DATA_EPOCH_ID
PROOF = {"trigger_price_source": "book", "delay_source": "POOLED_LIVE_ACK_CROSS_LANE:ENTRY_MKT_GTC_q90_n24",
         "exit_priced_delay_source": "POOLED_LIVE_ACK_CROSS_LANE:EXIT_RO_MKT_max_n12",
         "entry_walk_bp": 0.4, "exit_walk_bp": 0.6, "exit_legs_unmeasured": 0, "evidence_status": "COMPLETE",
         "entry_book_age_ms": 210, "exit_price_age_s": 0.33, "exit_evidence_status": "COMPLETE",
         "exit_unverified_reason": None, "levels_crossed_unfired": [], "stop_coverage": "STOP_COVERAGE_OK",
         "liq_unverified": False, "liq_mark_rows_n": 30, "liq_trigger_ts": None,
         "entry_book_receive_ts": 1000.2, "entry_book_receive_mono": 50.2, "entry_book_receive_ts_source": "WS_FRAME",
         "entry_book_receive_age_ms": 120.0, "exit_book_receive_ts": 1100.4, "exit_book_receive_mono": 150.4,
         "exit_book_receive_ts_source": "WS_FRAME", "exit_book_receive_age_ms": 80.0}
T0 = 1_792_270_800.0  # hour-aligned: 2 trades per 1 h cluster


def row(i, bp, tile="GS01", **extra):
    r = {"trade_id": f"t-{i:04d}", "tile_short": tile, "data_epoch_id": EPOCH,
         "collector_epoch_id": "epoch-e13a634e93d8d9a87305476c", "fill_model": "BOOK10L_WALK_V2",
         "entry_ts": T0 + i * 1800, "net_pnl_bp": bp, **PROOF}
    r.update(extra)
    return r


def rows():
    out = [row(i, bp) for i, bp in enumerate([4.0, -2.0, 6.0, 1.0, 3.0, -1.0])]
    out.append(row(90, 50.0, trigger_price_source="ticker"))          # R17_UNPROVEN
    out.append(row(91, 50.0, delay_source="default_until_n10"))       # R17_UNPROVEN
    out.append(row(92, 50.0, data_epoch_id="ce-20260901-old"))        # other epoch
    return out


def table():
    return build_tile_table(rows(), ids=set(), load_errors=[])


def test_tile_table_stamps_and_counts_only_countable_rows():
    t = table()
    tile = t["tiles"]["GS01"]
    assert (tile["rows"], tile["countable"], tile["n"]) == (9, 6, 6)
    assert tile["mean_bp"] == round(11.0 / 6, 4)  # the 50 bp non-countable rows never enter the mean
    assert tile["ci95_low"] < tile["mean_bp"] < tile["ci95_high"] and tile["clusters"] == 3
    assert t["non_countable_by_reason"] == {"NOT_CURRENT_DATA_EPOCH": 1, "R17_UNPROVEN": 2}
    assert t["r17_excluded_rows"] == 2
    stamp = t["trades"][0]
    assert stamp["fill_model"] == "BOOK10L_WALK_V2" and stamp["trigger_price_source"] == "book"
    assert stamp["delay_n"] == 24 and stamp["entry_book_receive_ts_present"] and stamp["exit_book_receive_ts_present"]


def test_cluster_ci_needs_two_clusters():
    assert cluster_ci([1.0, 2.0], [1, 1])["ci95_low"] is None
    assert cluster_ci([], [])["n"] == 0


def _blockers():
    return {"level": "RED", "items": [
        {"input": "protection_replay_window", "status": "OK", "evidence": {"alert_level": "GREEN"}},
        {"input": "exit_ladder_simulator", "status": "BLOCKED_QUARANTINED", "reason_code": "R3_QUARANTINE"},
        {"input": "cross_world_evidence", "status": "OPTIONAL_MISSING", "reason_code": "REPORT_MISSING"},
        {"input": "signal_replay", "status": "RETIRED"},
    ]}


def _valid_generation(**over):
    t = table()
    kwargs = dict(
        integrity={"report_status": "VALID", "checks": [{"check": "fixture", "passed": True}]},                                                   # (a)
        data_epoch={"declared": True, "epoch_id": EPOCH, "pre_epoch_rows_admitted": 0,          # (a) + (b)
                    "straddle_rows_unguarded": 0, "unstamped_rows_unguarded": 0},
        input_blockers=_blockers(),
        protection_replay_window={"truncated": False, "events_eligible": 10, "events_selected": 10,  # (e)
                                  "alert_level": "AMBER", "reason": "R17_UNPROVEN_EVENTS_EXCLUDED",
                                  "mark_events_by_basis": {"BOOK10L_WALK_TRADE_STOP_V1": 4, "R17_UNPROVEN": 6},
                                  "mark_excluded_events_by_reason": {"R17_UNPROVEN:BOOK_ROWS_DO_NOT_COVER_WINDOW": 6}},
        source_revision="346ef2bf",
    )
    kwargs.update(over)
    receipt = build_generation_receipt({"generation_id": "g", "required_report_status": {
        "a.json": {"available_in_generation": True}}}, **kwargs)
    return receipt, t


def test_generation_with_a_to_e_valid_and_sim_crossworld_absent_is_valid():
    receipt, t = _valid_generation()
    assert t["tiles"]["GS01"]["n"] == 6 and t["r17_excluded_rows"] == 2                         # (c) + (d)
    assert receipt["complete"] is True and receipt["integrity_status"] == "VALID"
    assert receipt["level"] != "RED"
    assert receipt["quarantined_inputs"] == [{"input": "exit_ladder_simulator", "status": "BLOCKED_QUARANTINED",
                                              "reason_code": "R3_QUARANTINE"}]
    assert receipt["optional_inputs"][0]["status"] == "OPTIONAL_MISSING"
    assert not any("exit_ladder" in r or "cross_world" in r for r in receipt["reasons"])
    assert receipt["protection_replay_excluded_by_reason"] == {"R17_UNPROVEN:BOOK_ROWS_DO_NOT_COVER_WINDOW": 6}
    assert receipt["source_revision"] == "346ef2bf"


def test_receipt_hard_rejects_any_other_epoch():
    for bad in ("epoch-e13a634e93d8d9a87305476c", "ce-20260901-old"):
        receipt, _ = _valid_generation(data_epoch={"declared": True, "epoch_id": bad, "pre_epoch_rows_admitted": 0})
        assert receipt["level"] == "RED" and receipt["complete"] is False and receipt["epoch_rejected"] is True
    receipt, _ = _valid_generation(data_epoch=None)
    assert receipt["level"] == "RED" and receipt["complete"] is False


def test_unguarded_unplaced_rows_are_red_and_incomplete():
    receipt, _ = _valid_generation(data_epoch={"declared": True, "epoch_id": EPOCH, "pre_epoch_rows_admitted": 0,
                                               "straddle_rows_unguarded": 1, "unstamped_rows_unguarded": 0})
    assert receipt["level"] == "RED" and receipt["complete"] is False


def test_off_book_replay_basis_is_red():
    receipt, _ = _valid_generation(protection_replay_window={
        "truncated": False, "events_eligible": 3, "events_selected": 3, "alert_level": "GREEN",
        "mark_events_by_basis": {"BOOK10L_WALK_TRADE_STOP_V1": 2, "CANDLE_1M": 1}})
    assert receipt["level"] == "RED" and receipt["complete"] is False


def test_missing_source_revision_is_flagged():
    receipt, _ = _valid_generation(source_revision=None)
    assert receipt["source_revision"] is None and "source_revision missing" in receipt["reasons"]


def test_integrity_invalid_still_red():
    receipt, _ = _valid_generation(integrity={"report_status": "INVALID", "failed_checks": [{"name": "trades_wins_losses"}]})
    assert receipt["level"] == "RED" and receipt["complete"] is False
    assert any("trades_wins_losses" in r for r in receipt["reasons"])
