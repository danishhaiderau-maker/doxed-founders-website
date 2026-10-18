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
