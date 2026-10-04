"""Unfilled tile orders reach the tile's v3 lifecycle cohort as NO_FILL terminals."""
import copy
import json
from pathlib import Path

import research_v3_store
from research_v3_bridge import dual_write_terminal_paper_schedule

PROVENANCE = {
    "source_revision": "a" * 40,
    "deployed_revision": "b" * 40,
    "tile_config_signature": "c" * 64,
    "config_signature": "d" * 64,
}
EPOCH = "epoch-tile-no-fill"


def _order(lane="FAMILY_NOTRADE_FOLLOW_TAKER_60", reason="SIGNAL_TTL_EXPIRED", trade_id="ntt-nofill-1"):
    schedule = {
        "schema": "research_chase_schedule_v1", "authoritative": True,
        "intervals": [{"bucket_id": "b0", "start_ts": 100.0, "end_ts": 1900.0, "limit_price": 100.0}],
        "terminal_ts": 1900.0, "terminal_ts_exact": 1900.0, "terminal_reason": reason,
    }
    signal = {"trade_id": trade_id, "created_ts": 100.0, "raw_direction": "LONG", "final_direction": "LONG",
              "shared_ai_call_id": f"scan-{trade_id}", "symbol": "tBTCF0:USTF0", "research_lane": lane,
              "policy_id": lane, **PROVENANCE}
    order = {**signal, "status": "CANCELLED", "qty": 0.0003, "requested_qty": 0.0003, "limit_price": 100.0,
             "touched_limit": False, "research_chase_schedule": schedule, "chase_schedule_authoritative": True}
    return order, copy.deepcopy(signal)


def _lifecycle_rows(root: Path):
    rows = []
    for path in root.rglob("lifecycle.jsonl"):
        rows += [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return rows


def test_unfilled_tile_order_writes_one_tile_no_fill_terminal(tmp_path, monkeypatch):
    monkeypatch.setattr(research_v3_store, "_collection_provenance", lambda: dict(PROVENANCE))
    order, signal = _order()
    receipt = dual_write_terminal_paper_schedule(order, signal, epoch_id=EPOCH, data_dir=tmp_path, lifecycle_final=True)
    dual_write_terminal_paper_schedule(order, signal, epoch_id=EPOCH, data_dir=tmp_path, lifecycle_final=True)
    assert receipt["no_fill_lifecycle_write"] is not None
    rows = [r for r in _lifecycle_rows(tmp_path) if r.get("terminal_no_fill") is True]
    assert len(rows) == 1
    row = rows[0]
    assert row["research_lane"] == "FAMILY_NOTRADE_FOLLOW_TAKER_60" and row["outcome_state"] == "NO_FILL"
    assert row["policy_signature"] == receipt["policy_signature"] and row["policy_signature"].startswith("paper-policy-")
    assert row["event_id"] == "ntt-nofill-1" and row["episode_id"] == receipt["episode_id"]
    assert row["terminal_ttl_expired"] is True and row["forced_terminal"] is False
    assert row["ranking_eligible"] is False


def test_deploy_boundary_cancel_is_a_forced_no_fill_terminal(tmp_path, monkeypatch):
    monkeypatch.setattr(research_v3_store, "_collection_provenance", lambda: dict(PROVENANCE))
    order, signal = _order(reason="CIRCUIT_BREAKER_ADMIN_MANUAL", trade_id="ntt-admin-1")
    dual_write_terminal_paper_schedule(order, signal, epoch_id=EPOCH, data_dir=tmp_path, lifecycle_final=True)
    (row,) = [r for r in _lifecycle_rows(tmp_path) if r.get("terminal_no_fill") is True]
    assert row["forced_terminal"] is True and row["terminal_reason"] == "CIRCUIT_BREAKER_ADMIN_MANUAL"


def test_filled_or_non_tile_orders_write_no_no_fill_terminal(tmp_path, monkeypatch):
    monkeypatch.setattr(research_v3_store, "_collection_provenance", lambda: dict(PROVENANCE))
    filled, signal = _order(reason="FILLED", trade_id="ntt-filled-1")
    assert dual_write_terminal_paper_schedule(
        filled, signal, epoch_id=EPOCH, data_dir=tmp_path, lifecycle_final=True)["no_fill_lifecycle_write"] is None
    for retired_lane, trade_id in (("CONTINUOUS", "cont-1"), ("FAMILY_TREND_FADE_60", "ftf-1")):
        legacy, signal = _order(lane=retired_lane, trade_id=trade_id)
        assert dual_write_terminal_paper_schedule(
            legacy, signal, epoch_id=EPOCH, data_dir=tmp_path, lifecycle_final=True)["no_fill_lifecycle_write"] is None
    assert not [r for r in _lifecycle_rows(tmp_path) if r.get("terminal_no_fill") is True]

def test_completion_reconciler_proves_the_tile_no_fill_entry_outcome(tmp_path, monkeypatch):
    from lifecycle_bundles import LifecycleKey
    from lifecycle_completion_reconciler import evaluate_lifecycle_completion
    monkeypatch.setattr(research_v3_store, "_collection_provenance", lambda: dict(PROVENANCE))
    order, signal = _order()
    receipt = dual_write_terminal_paper_schedule(order, signal, epoch_id=EPOCH, data_dir=tmp_path, lifecycle_final=True)
    rows = []
    for path in tmp_path.rglob("*.jsonl"):
        rows += [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    key = LifecycleKey(EPOCH, receipt["episode_id"], receipt["policy_signature"], "FAMILY_NOTRADE_FOLLOW_TAKER_60")
    result = evaluate_lifecycle_completion(
        key, [r for r in rows if r.get("research_lane") == "FAMILY_NOTRADE_FOLLOW_TAKER_60"], now=10_000.0)
    blockers = set(result["blockers"])
    assert not blockers & {"UNIQUE_ENTRY_OUTCOME_NOT_PROVEN", "POSITION_NOT_PROVEN_CLOSED",
                           "OPEN_QUANTITY_MISSING", "EVENT_ID_MISSING_OR_AMBIGUOUS",
                           "UNIQUE_TERMINAL_SCHEDULE_NOT_PROVEN"}
