import ast
import json
import threading
from collections import deque
from pathlib import Path

import shadow_chase_buckets as scb

ROOT = Path(__file__).resolve().parent
BOT_SOURCE = (ROOT / "bot.py").read_text(encoding="utf-8")
MODULE_SOURCE = (ROOT / "shadow_chase_buckets.py").read_text(encoding="utf-8")


def _shadow(**overrides):
    base = {
        "trade_id": "compressed-shadow-1", "shared_ai_call_id": "call-1", "epoch_id": "epoch-a",
        "policy_signature": "policy-sha256-x", "direction": "LONG", "signal_price": 100_000.0,
        "signal_ts": 1_000.0, "expires_ts": 1_780.0, "virtual_limit_price": 99_900.0,
        "entry_fee_rate": 0.0001, "exit_fee_rate": 0.0002, "leverage": 100, "requested_margin_usd": 0.25,
    }
    base.update(overrides)
    return base


def test_fill_after_two_chases_marks_outcome_at_horizon_with_fees():
    tr = scb.new_tracker(_shadow())
    assert scb.observe(tr, now_ts=1_010, bid=99_990, ask=100_000, last=99_995, stage_index=0) == []
    assert scb.observe(tr, now_ts=1_130, bid=99_960, ask=99_970, last=99_965, stage_index=2,
                       virtual_limit_price=99_950.0) == []
    assert scb.observe(tr, now_ts=1_140, bid=99_940, ask=99_950, last=99_945, stage_index=2) == []
    assert tr["filled"] and tr["chase_count_at_fill"] == 2 and tr["fill_price"] == 99_950.0
    scb.observe(tr, now_ts=1_500, bid=100_040, ask=100_060, last=100_050)
    scb.observe(tr, now_ts=2_000, bid=99_900, ask=99_910, last=99_905)
    [row] = scb.observe(tr, now_ts=1_140 + scb.OUTCOME_HORIZON_SEC, bid=99_990, ask=100_010, last=100_000)
    assert row["schema"] == scb.SHADOW_CHASE_BUCKET_SCHEMA and row["outcome"] == "FILLED"
    assert row["bucket"] == "2_chases" and row["chase_count"] == 2
    assert row["time_to_fill_sec"] == 140.0
    assert row["fill_vs_signal_bp"] == -5.0
    assert row["mfe_bp"] > 0 > row["mae_bp"]
    assert row["fee_bp"] == 3.0 and row["net_bp"] == round(row["gross_bp"] - 3.0, 4)
    assert row["places_order"] is False and row["relay_eligible"] is False
    assert row["execution_class"] == "SHADOW_ONLY"
    assert tr["closed"] and scb.observe(tr, now_ts=10_000, bid=1, ask=2, last=1) == []


def test_unfilled_schedule_emits_one_no_fill_in_its_last_reached_bucket():
    tr = scb.new_tracker(_shadow(direction="SHORT", virtual_limit_price=100_100.0))
    for stage, ts in ((1, 1_060), (3, 1_240), (5, 1_600)):
        assert scb.observe(tr, now_ts=ts, bid=99_990, ask=100_000, last=99_995, stage_index=stage) == []
    [row] = scb.observe(tr, now_ts=1_780, bid=99_990, ask=100_000, last=99_995)
    assert row["outcome"] == "NO_FILL" and row["bucket"] == "5+_chases" and row["filled"] is False
    assert row["net_bp"] is None and row["win"] is None


def test_aggregate_fill_rate_wr_ev_and_rejects_non_shadow_rows():
    rows = [
        {"schema": scb.SHADOW_CHASE_BUCKET_SCHEMA, "execution_class": "SHADOW_ONLY", "places_order": False,
         "epoch_id": "epoch-a", "chase_count": 0, "filled": True, "net_bp": 10.0, "time_to_fill_sec": 5},
        {"schema": scb.SHADOW_CHASE_BUCKET_SCHEMA, "execution_class": "SHADOW_ONLY", "places_order": False,
         "epoch_id": "epoch-a", "chase_count": 1, "filled": True, "net_bp": -4.0, "time_to_fill_sec": 70},
        {"schema": scb.SHADOW_CHASE_BUCKET_SCHEMA, "execution_class": "SHADOW_ONLY", "places_order": False,
         "epoch_id": "epoch-a", "chase_count": 5, "filled": False, "net_bp": None},
        {"schema": scb.SHADOW_CHASE_BUCKET_SCHEMA, "execution_class": "SHADOW_ONLY", "places_order": True,
         "epoch_id": "epoch-a", "chase_count": 0, "filled": True, "net_bp": 99.0},
        {"schema": scb.SHADOW_CHASE_BUCKET_SCHEMA, "execution_class": "SHADOW_ONLY", "places_order": False,
         "epoch_id": "epoch-old", "chase_count": 0, "filled": True, "net_bp": 99.0},
    ]
    out = scb.aggregate(rows, epoch_id="epoch-a", margin_usd=0.25, leverage=100)
    assert out["records"] == 3
    b0, b1, b2, _, _, b5 = out["buckets"]
    assert (b0["reached"], b0["trades"], b0["fill_rate_pct"], b0["win_rate_pct"], b0["ev_bp"]) == (3, 1, 33.33, 100.0, 10.0)
    assert b0["ev_usd"] == 0.025
    assert (b1["reached"], b1["trades"], b1["fill_rate_pct"], b1["win_rate_pct"]) == (2, 1, 50.0, 0.0)
    assert (b2["reached"], b2["trades"], b2["fill_rate_pct"], b2["ev_bp"]) == (1, 0, 0.0, None)
    assert (b5["reached"], b5["trades"], b5["fill_rate_pct"]) == (1, 0, 0.0)


def test_shadow_module_never_orders_or_relays():
    tree = ast.parse(MODULE_SOURCE)
    imports = {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
    imports |= {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    assert imports <= {"__future__", "typing"}
    calls = {node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", "")
             for node in ast.walk(tree) if isinstance(node, ast.Call)}
    for forbidden in ("submit", "place", "relay", "create_order", "execute", "promote"):
        assert not any(forbidden in name.lower() for name in calls), forbidden


def _function_source(name):
    tree = ast.parse(BOT_SOURCE)
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
    return ast.get_source_segment(BOT_SOURCE, node)


def test_bot_feeds_shadow_tracker_only_from_the_shadow_poll_and_appends_evidence():
    poll = _function_source("_poll_chase_offset_touch_grid")
    assert "shadow_chase_buckets.observe(" in poll
    assert "_note_shadow_chase_bucket_records(shadow_rows)" in poll
    segment = poll.split("for tid, tracker in list(_shadow_chase_bucket_book.items()):", 1)[1].split("# Disk validation", 1)[0]
    for forbidden in ("execute_simulated_order", "_place_simulated_limit_order", "promote_signal",
                      "submit_limit_entry", "relay", "pending_orders.append"):
        assert forbidden not in segment
    arm = _function_source("_arm_shared_compressed_shadow_chase")
    assert "_shadow_chase_bucket_book[trade_id] = shadow_chase_buckets.new_tracker(shadow_state)" in arm
    assert BOT_SOURCE.count("shadow_chase_buckets.observe(") == 1


def _snapshot_namespace(tmp_path, chasing=(), rows=()):
    grid = tmp_path / "chase_offset_touch_grid.jsonl"
    grid.write_text("".join(json.dumps(r) + "\n" for r in rows) + '{"schema": "chase_offset_touch_grid_v1"}\n',
                    encoding="utf-8")
    namespace = {
        "json": json, "threading": threading, "deque": deque, "shadow_chase_buckets": scb,
        "CHASE_OFFSET_TOUCH_GRID_FILE": str(grid), "FIXED_MARGIN_USDT": 0.25,
        "_state_leverage": lambda: 100, "_collector_v22_epoch_id": lambda: "epoch-a",
        "chasing_tile_lanes": lambda: tuple(chasing),
    }
    tree = ast.parse(BOT_SOURCE)
    wanted = {"_note_shadow_chase_bucket_records", "_load_shadow_chase_bucket_records_once",
              "_load_chase_analytics_snapshot"}
    body = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in wanted:
            body.append(node)
        elif isinstance(node, ast.Assign) and any(
                getattr(t, "id", "").startswith("_shadow_chase_bucket_") for t in node.targets):
            body.append(node)
    exec(compile(ast.Module(body=body, type_ignores=[]), "<shadow-snapshot>", "exec"), namespace)
    return namespace


def test_snapshot_populates_simulated_buckets_and_labels_no_live_chasing_tile(tmp_path):
    row = {"schema": scb.SHADOW_CHASE_BUCKET_SCHEMA, "execution_class": "SHADOW_ONLY", "places_order": False,
           "trade_id": "t1", "epoch_id": "epoch-a", "chase_count": 3, "filled": True, "net_bp": 6.0,
           "time_to_fill_sec": 300}
    ns = _snapshot_namespace(tmp_path, rows=[row])
    snap = ns["_load_chase_analytics_snapshot"]()
    assert snap["status"] == "SIMULATED_SHADOW" and snap["records"] == 1
    assert snap["label"] == scb.SIMULATED_LABEL
    assert snap["live"] == {"status": "NOT_APPLICABLE", "reason": "NO_CHASING_TILE"}
    assert [b["bucket"] for b in snap["buckets"]] == list(scb.BUCKET_KEYS)
    assert snap["buckets"][3]["trades"] == 1 and snap["buckets"][3]["ev_bp"] == 6.0
    ns["_note_shadow_chase_bucket_records"]([{**row, "trade_id": "t2", "chase_count": 0}])
    again = ns["_load_chase_analytics_snapshot"]()
    assert again["records"] == 2 and again["buckets"][0]["trades"] == 1


def test_snapshot_keeps_live_tile_section_separate_when_a_tile_chases(tmp_path):
    ns = _snapshot_namespace(tmp_path, chasing=("FAMILY_CHASER",))
    snap = ns["_load_chase_analytics_snapshot"]()
    assert snap["status"] == "COLLECTING" and snap["chasing_lanes"] == ["FAMILY_CHASER"]
    assert snap["label"] == "SIMULATED (shadow)"
    assert snap["live"]["status"] == "UNAVAILABLE"
