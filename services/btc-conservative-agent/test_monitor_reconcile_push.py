"""Per-lane laptop reconciliation block pushed with the system-health report, used by /api/monitor/reconcile only
for the same epoch and while fresh; otherwise the sources stay NOT_REPORTED_TO_FLY with a reason."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

import ledger_reconciliation as lr
import monitor_reconcile as mr

EPOCH = "ce-20261018-fresh"
NOW = 1_792_300_000.0
GS1, HA = "FAMILY_GS01_XV_PREMIUM_ATR_TP", "FAMILY_RANDOM_CONTROL_TAKER_90"


def _iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()


def _closed(tid, lane, net):
    return {"trade_id": tid, "research_lane": lane, "status": "CLOSED", "pnl": {"net_usd": net}, "pricing": {}}


def _block(**over):
    block = {"schema": mr.PUSH_SCHEMA, "epoch_id": EPOCH, "generated_at": _iso(NOW - 600), "compared": True,
             "bounded": True,
             "per_lane": {GS1: {"mirror_n": 2, "analyzer_n": 2, "missing_ids_n": 0, "pnl_diff_usd": 0.004},
                          HA: {"mirror_n": 3, "analyzer_n": 2, "missing_ids_n": 1, "pnl_diff_usd": -0.5}}}
    block.update(over)
    return block


def _build(block, received_age=60.0, epoch=EPOCH):
    pushed = {"block": mr.sanitize_push(block), "received_ts": NOW - received_age}
    rows = {GS1: [_closed("a", GS1, 0.01), _closed("b", GS1, 0.02)], HA: [_closed("c", HA, 0.1)]}
    return mr.build(rows, {GS1: "GS1", HA: "HA"}, None, {"data_epoch_id": epoch}, pushed=pushed, now=NOW)


def test_same_epoch_fresh_push_fills_per_lane_fields_and_ok():
    out = _build(_block())
    assert out["sources"] == {"fly": "OK", "mirror": "LAPTOP_PUSH", "analyzer": "LAPTOP_PUSH"}
    assert out["sources_reason"] is None
    tiles = {t["lane"]: t for t in out["per_tile"]}
    assert tiles[GS1]["mirror_n"] == 2 and tiles[GS1]["analyzer_n"] == 2
    assert tiles[GS1]["missing_ids_n"] == 0 and tiles[GS1]["pnl_diff_usd"] == 0.004 and tiles[GS1]["ok"] is True
    assert tiles[HA]["ok"] is False and tiles[HA]["missing_ids_n"] == 1 and tiles[HA]["pnl_diff_usd"] == -0.5
    assert out["laptop_push"]["epoch_id"] == EPOCH and out["laptop_push"]["generated_at"] == _iso(NOW - 600)


@pytest.mark.parametrize("block,received_age,epoch,reason", [
    (_block(epoch_id="ce-20261004-old"), 60, EPOCH, "EPOCH_MISMATCH"),
    (_block(), 60, None, "EPOCH_MISMATCH"),
    (_block(generated_at=_iso(NOW - mr.PUSH_MAX_AGE_SEC - 1)), 60, EPOCH, "STALE (generated"),
    (_block(), mr.PUSH_MAX_RECEIVE_AGE_SEC + 1, EPOCH, "STALE (last push"),
    (_block(generated_at=None), 60, EPOCH, "GENERATED_AT_MISSING"),
    (_block(compared=False), 60, EPOCH, "LAPTOP_COULD_NOT_COMPARE"),
    ({"schema": "something_else"}, 60, EPOCH, "NO_LAPTOP_PUSH"),
])
def test_wrong_epoch_or_stale_push_keeps_not_reported_with_a_reason(block, received_age, epoch, reason):
    out = _build(block, received_age, epoch)
    assert out["sources"] == {"fly": "OK", "mirror": "NOT_REPORTED_TO_FLY", "analyzer": "NOT_REPORTED_TO_FLY"}
    assert out["sources_reason"].startswith(reason)
    assert all(t["mirror_n"] is None and t["analyzer_n"] is None and t["missing_ids_n"] is None
               and t["pnl_diff_usd"] is None and t["ok"] is None for t in out["per_tile"])


def test_no_push_at_all_is_unchanged_fly_side_only():
    out = mr.build({GS1: [_closed("a", GS1, 0.01)]}, {}, None, {"data_epoch_id": EPOCH})
    assert out["sources"]["mirror"] == "NOT_REPORTED_TO_FLY" and out["sources_reason"] == "NO_LAPTOP_PUSH"
    assert out["laptop_push"] is None and out["per_tile"][0]["fly_n"] == 1


def test_sanitize_push_bounds_and_types_untrusted_values():
    raw = _block(per_lane={GS1: {"mirror_n": "2", "analyzer_n": -1, "missing_ids_n": 1.5, "pnl_diff_usd": "nan"},
                           **{f"L{i}": {} for i in range(100)}, 7: {"mirror_n": 1}}, epoch_id="x" * 500)
    clean = mr.sanitize_push(raw)
    assert clean["per_lane"][GS1] == {"mirror_n": None, "analyzer_n": None, "missing_ids_n": None, "pnl_diff_usd": None,
                                      "bounded": None}
    assert len(clean["per_lane"]) <= mr.MAX_PUSH_LANES and len(clean["epoch_id"]) == 64
    assert mr.sanitize_push(None) is None and mr.sanitize_push({"per_lane": {}}) is None


def test_lane_partial_values_fill_but_leave_ok_unknown():
    out = _build(_block(per_lane={GS1: {"mirror_n": 2, "analyzer_n": None, "missing_ids_n": 0, "pnl_diff_usd": 0}}))
    tile = next(t for t in out["per_tile"] if t["lane"] == GS1)
    assert tile["mirror_n"] == 2 and tile["analyzer_n"] is None and tile["ok"] is None


# ---- laptop side --------------------------------------------------------------------------------------------------
def _fly_state(lane_net):
    return {"lane_pnl_ledger": {lane: {"closes": n, "net_pnl_usd": pnl, "wins": n} for lane, (n, pnl) in lane_net.items()},
            "trades": [{"trade_id": "t-new", "research_lane": GS1, "close_ts": _iso(NOW - 10000), "net_pnl_usd": 0.5}]}


def test_compare_with_fly_reports_missing_ids_and_pnl_diff_per_lane_and_the_push_block():
    report = {"lanes": [GS1, HA], "level": "GREEN", "epoch_id": "collector-x", "generated_at": _iso(NOW - 300),
              "source_data_through": _iso(NOW - 400),
              "analyzer_cohort": {GS1: {"n": 1, "net_pnl_usd": 0.25}, HA: {"n": 0, "net_pnl_usd": 0.0}}}
    mirror = [{"trade_id": "t-1", "research_lane": GS1, "status": "CLOSED", "close_ts": _iso(NOW - 500),
               "net_pnl_usd": 0.25}]
    out = lr.compare_with_fly(report, _fly_state({GS1: (2, 0.80), HA: (0, 0.0)}), mirror)
    per = out["breakdown"]["per_lane"]
    assert per[GS1]["missing_ids_n"] == 1 and out["breakdown"]["missing_in_mirror"] == ["t-new"]
    assert per[GS1]["pnl_diff_usd"] == pytest.approx(0.80 - 0.75) and per[HA]["missing_ids_n"] == 0
    block = lr.reconcile_push_block(report, out, EPOCH)
    assert block["schema"] == mr.PUSH_SCHEMA and block["epoch_id"] == EPOCH and block["compared"] is True
    assert block["generated_at"] == report["generated_at"]
    assert block["per_lane"][GS1] == {"mirror_n": 1, "analyzer_n": 1, "missing_ids_n": 1,
                                      "pnl_diff_usd": per[GS1]["pnl_diff_usd"], "bounded": per[GS1]["bounded"]}
    assert block["bounded"] is out["breakdown"]["bounded"]
    assert mr.sanitize_push(block)["per_lane"][GS1]["missing_ids_n"] == 1  # round-trips through the Fly sanitizer
    no_fly = lr.reconcile_push_block(report, lr.compare_with_fly(report, None, mirror), EPOCH)
    assert no_fly["compared"] is False and no_fly["per_lane"] == {} and no_fly["bounded"] is None


@pytest.mark.parametrize("bounded", [False, None, "yes"])
def test_unbounded_or_unknown_bounded_fills_numbers_but_leaves_ok_null(bounded):
    """HM nit: while the mirror lags (bounded false) the laptop counts are a lower bound; ok must stay null, never
    read false. A missing or non-bool flag is treated the same."""
    lanes = {GS1: {"mirror_n": 3, "analyzer_n": 2, "missing_ids_n": 1, "pnl_diff_usd": -0.5}}
    out = _build(_block(bounded=bounded, per_lane=lanes))
    tile = next(t for t in out["per_tile"] if t["lane"] == GS1)
    assert out["sources"]["mirror"] == "LAPTOP_PUSH"
    assert tile["mirror_n"] == 3 and tile["missing_ids_n"] == 1 and tile["pnl_diff_usd"] == -0.5
    assert tile["bounded"] is (False if bounded is False else None) and tile["ok"] is None
    assert out["laptop_push"]["bounded"] is (False if bounded is False else None)


def test_per_lane_bounded_overrides_the_block_flag():
    lanes = {GS1: {"mirror_n": 2, "analyzer_n": 2, "missing_ids_n": 0, "pnl_diff_usd": 0.0, "bounded": False},
             HA: {"mirror_n": 1, "analyzer_n": 1, "missing_ids_n": 0, "pnl_diff_usd": 0.0}}
    tiles = {t["lane"]: t for t in _build(_block(bounded=True, per_lane=lanes))["per_tile"]}
    assert tiles[GS1]["ok"] is None and tiles[HA]["ok"] is True
