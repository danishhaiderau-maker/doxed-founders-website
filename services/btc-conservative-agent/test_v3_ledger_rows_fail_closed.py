"""Queue item 3 (HM 19 Oct): V3 ledger readers hard-reject pre-epoch / straddle / unstamped rows under a declared epoch."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "research"))

import data_epoch as de
from analyzer_epoch_guard import EpochGuard, set_process_guard

FLOOR = 1_790_000_000.0
PRE = {"segment_ref": {"start_ts": FLOOR - 500, "end_ts": FLOOR - 100}}
STRADDLE = {"segment_ref": {"start_ts": FLOOR - 50, "end_ts": FLOOR + 50}}
OK = {"segment_ref": {"start_ts": FLOOR + 10, "end_ts": FLOOR + 60}}
UNSTAMPED = {"id": "x"}


def _guard(floor=FLOOR, require=True):
    return EpochGuard(de.new_manifest("ce-20261018-fresh", started_at_ts=FLOOR), floor_ts=floor, require_floor=require)


@pytest.fixture(autouse=True)
def _reset():
    yield
    set_process_guard(None)


def test_only_rows_wholly_after_the_floor_are_admitted_and_each_rejection_is_counted():
    g = _guard()
    got = [g.admit_v3_ledger_row("v3/ledgers/decision.jsonl", r) for r in (PRE, STRADDLE, OK, UNSTAMPED)]
    assert got == [False, False, True, False]
    assert g.rejected["v3/ledgers/decision.jsonl"] == {"PRE_EPOCH": 1, "STRADDLE": 1, "UNSTAMPED": 1}


def test_declared_epoch_without_a_floor_admits_nothing_and_no_manifest_admits_all():
    assert _guard(floor=None).admit_v3_ledger_row("v3/ledgers/decision.jsonl", OK) is False
    assert EpochGuard(None).admit_v3_ledger_row("v3/ledgers/decision.jsonl", UNSTAMPED) is True


def test_report_and_adapter_readers_drop_non_fresh_rows(tmp_path):
    led = tmp_path / "v3" / "ledgers"
    led.mkdir(parents=True)
    p = led / "decision.jsonl"
    p.write_text("".join(json.dumps(r) + "\n" for r in (PRE, STRADDLE, OK, UNSTAMPED)))
    import research_v3_report as rep
    import v3_policy_report_adapter as ad
    assert len(rep._read_ledger(p)) == 4 and len(list(ad._iter_jsonl(p))) == 4   # no guard installed: unchanged
    set_process_guard(_guard())
    assert rep._read_ledger(p) == [OK]
    assert list(ad._iter_jsonl(p)) == [OK]
    set_process_guard(_guard(floor=None))
    assert rep._read_ledger(p) == [] and list(ad._iter_jsonl(p)) == []


# ---- real floor and real saved ledger rows (Fly runtime/v3/ledgers, captured 19 Oct) --------------------------------
REAL_FLOOR = 1792263671.26
_FIX = json.loads((Path(__file__).resolve().parent / "fixtures" / "v3_ledger_rows_fresh_20261019.json").read_text())["rows"]


def _real_guard(floor=REAL_FLOOR):
    return EpochGuard(de.new_manifest("ce-20261018-fresh", started_at_ts=REAL_FLOOR), floor_ts=floor, require_floor=True)


def _bump_first_ts(row, to):
    import copy
    row = copy.deepcopy(row)

    def walk(v):
        if isinstance(v, dict):
            for k, x in v.items():
                if k.endswith("_ts") and isinstance(x, (int, float)) and not isinstance(x, bool):
                    v[k] = to
                    return True
                if walk(x):
                    return True
        elif isinstance(v, list):
            return any(walk(x) for x in v)
        return False
    assert walk(row)
    return row


def test_real_fresh_rows_are_admitted_and_real_straddle_row_is_rejected():
    g = _real_guard()
    for name in ("decision", "lifecycle"):
        for r in _FIX[name]:
            assert g.admit_v3_ledger_row(f"v3/ledgers/{name}.jsonl", r) is True, name
    seg = [g.admit_v3_ledger_row("v3/ledgers/market_segment.jsonl", r) for r in _FIX["market_segment"]]
    assert seg == [True, False, True]                      # the middle real row's window crosses the floor (-165 s)
    blk = g.receipt_block()
    assert blk["rows_admitted"] == 8 > 0
    assert blk["rejected_by_stream"]["v3/ledgers/market_segment.jsonl"] == {"STRADDLE": 1}
    fr = blk["fresh_rule"]
    assert fr["status"] == "GREEN" and fr["v3_readers"]["v3/ledgers/decision.jsonl"]["admitted"] == 3


def test_any_nested_ts_before_the_real_floor_rejects_a_real_row():
    g = _real_guard()
    for name in ("decision", "lifecycle", "market_segment"):
        good = _FIX[name][0]
        assert g.admit_v3_ledger_row(f"v3/ledgers/{name}.jsonl", good) is True
        assert g.admit_v3_ledger_row(f"v3/ledgers/{name}.jsonl", _bump_first_ts(good, REAL_FLOOR - 1)) is False


def test_declared_epoch_with_no_floor_admits_nothing_and_is_red():
    g = _real_guard(floor=None)
    assert all(g.admit_v3_ledger_row("v3/ledgers/decision.jsonl", r) is False for r in _FIX["decision"])
    fr = g.receipt_block()["fresh_rule"]
    assert fr["status"] == "RED" and fr["reason"] == "FRESH_FLOOR_MISSING"
    assert EpochGuard(de.new_manifest("ce-20261018-fresh", started_at_ts=REAL_FLOOR), floor_ts=None).fresh_rule_status()["status"] == "RED"


def test_load_ids_stays_unfiltered():
    src = (Path(__file__).resolve().parent / "research_v3_store.py").read_text()
    assert "admit_v3_ledger_row" not in src and "process_guard" not in src
