"""Every analyzer V3-ledger reader routes through the hard fresh-epoch guard (Strategist 19 Oct 18:07 audit).

#754 made research_v3_report._iter_ledger and v3_policy_report_adapter._iter_jsonl drop non-fresh rows, but they still
opened the ledger outside ``guarded_read`` and ten other readers neither filtered nor declared the open, so the read
monitor kept counting every straddle/unstamped row of those files as "read unguarded" (2910 -> 3910 as the ledgers
grew). Each reader below must (1) drop PRE_EPOCH, STRADDLE and UNSTAMPED rows under a declared epoch and (2) leave the
read monitor with no unguarded V3-ledger open (straddle_rows_unguarded == unstamped_rows_unguarded == 0).
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
for extra in (HERE, HERE / "research"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

import analyzer_epoch_guard as eg  # noqa: E402
import data_epoch as de  # noqa: E402

FLOOR = 1792263671.2608788  # fresh epoch 18 Oct 06:01:11 AEDT
EPOCH = "epoch-test"


def _row(rid, lo, hi, **extra):
    row = {"record_id": rid, "event_id": f"ev-{rid}", "episode_id": f"e-{rid}", "opportunity_id": f"o-{rid}",
           "epoch_id": EPOCH, "ledger": "x"}
    if lo is not None:
        row["segment_ref"] = {"start_ts": lo, "end_ts": hi}
    row.update(extra)
    return row


OK = _row("ok", FLOOR + 10, FLOOR + 60)
PRE = _row("pre", FLOOR - 500, FLOOR - 100)
STRADDLE = _row("str", FLOOR - 50, FLOOR + 50)
UNSTAMPED = _row("uns", None, None)
ROWS = (PRE, STRADDLE, OK, UNSTAMPED)


@pytest.fixture
def env(tmp_path):
    guard = eg.EpochGuard(de.new_manifest("ce-20261018-fresh", started_at_ts=FLOOR), floor_ts=FLOOR, require_floor=True)
    eg.set_process_guard(guard)
    monitor = eg.StreamReadMonitor([tmp_path]).install()
    yield tmp_path, guard, monitor
    eg.set_process_guard(None)
    eg._ACTIVE_MONITOR = None


def _ledger(root: Path, name: str, rows=ROWS) -> Path:
    path = root / "v3" / "ledgers" / f"{name}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, sort_keys=True, separators=(",", ":")) + "\n" for r in rows),
                    encoding="utf-8")
    return path


def _ids(rows):
    return sorted(str(r.get("record_id")) for r in rows)


def _assert_no_unguarded_v3(monitor, guard):
    report = monitor.report(guard.manifest, FLOOR)
    v3 = {k: v for k, v in report["unguarded_stream_reads"].items() if k.startswith("v3/")}
    assert v3 == {}, v3
    assert report["straddle_rows_unguarded"] == 0 and report["unstamped_rows_unguarded"] == 0
    assert report["pre_epoch_rows_admitted"] == 0
    return report


def test_helpers_classify_paths_and_rows():
    assert eg.v3_ledger_relpath("/m/tree/v3/ledgers/decision.jsonl.2") == "v3/ledgers/decision.jsonl"
    assert eg.v3_ledger_relpath("/m/v3/recovery_ledgers/market_segment.jsonl") == "v3/recovery_ledgers/market_segment.jsonl"
    assert eg.v3_ledger_relpath("/m/trades_3factor.csv") is None
    eg.set_process_guard(eg.EpochGuard(de.new_manifest("ce-20261018-fresh", started_at_ts=FLOOR), floor_ts=FLOOR,
                                       require_floor=True))
    try:
        assert [eg.admit_v3_row("v3/ledgers/decision.jsonl", r) for r in ROWS] == [False, False, True, False]
        assert eg.admit_v3_line("v3/ledgers/decision.jsonl", json.dumps(OK)) is True
        assert eg.admit_v3_line("v3/ledgers/decision.jsonl", b"not json") is False
    finally:
        eg.set_process_guard(None)
    assert eg.admit_v3_row("v3/ledgers/decision.jsonl", UNSTAMPED) is True  # no declared epoch: unchanged


def test_raw_open_is_still_counted_unguarded(env):
    """Control: the monitor really flags a raw open of a ledger with straddle/unstamped rows."""
    root, guard, monitor = env
    path = _ledger(root, "decision")
    with open(path, "rb") as handle:
        handle.read()
    report = monitor.report(guard.manifest, FLOOR)
    assert report["straddle_rows_unguarded"] == 1 and report["unstamped_rows_unguarded"] == 1
    assert "v3/ledgers/decision.jsonl" in report["unguarded_stream_reads"]


def test_research_v3_report_iter_ledger(env):
    root, guard, monitor = env
    import research_v3_report as rep
    assert _ids(rep._read_ledger(_ledger(root, "decision"))) == ["ok"]
    _assert_no_unguarded_v3(monitor, guard)
    assert guard.rejected["v3/ledgers/decision.jsonl"] == {"PRE_EPOCH": 1, "STRADDLE": 1, "UNSTAMPED": 1}


def test_v3_policy_report_adapter_iter_jsonl_and_cycle_snapshot(env):
    root, guard, monitor = env
    import v3_policy_report_adapter as ad
    for name in ("opportunity", "decision", "order_intent", "execution", "lifecycle", "market_segment"):
        _ledger(root, name)
    assert _ids(ad._iter_jsonl(root / "v3" / "ledgers" / "decision.jsonl")) == ["ok"]
    snap = ad.load_v3_cycle_snapshot(root)
    assert set(snap["ledger_counts"].values()) == {1}, snap["ledger_counts"]  # canonical-index ledgers gated too
    _assert_no_unguarded_v3(monitor, guard)


def test_v3_ledger_index_iter_canonical_rows(env, tmp_path_factory):
    root, guard, monitor = env
    from research.v3_ledger_index import iter_canonical_rows
    path = _ledger(root, "order_intent")
    cache = tmp_path_factory.mktemp("idx")
    for _ in range(2):  # cold parse, then warm cached index: both gated
        assert [json.loads(b)["record_id"] for b in iter_canonical_rows(path, cache_dir=cache)] == ["ok"]
    _assert_no_unguarded_v3(monitor, guard)


def test_research_v3_candidates_readers(env):
    root, guard, monitor = env
    import research_v3_candidates as cand
    path = _ledger(root, "opportunity")
    assert _ids(cand._read_jsonl(path)) == ["ok"]
    assert [r["record_id"] for _off, r in cand._read_jsonl_offsets(_ledger(root, "order_intent"))] == ["ok"]
    _assert_no_unguarded_v3(monitor, guard)


def test_policy_evidence_evaluator_read_jsonl(env):
    root, guard, monitor = env
    from research import policy_evidence_evaluator as pee
    seen = []
    assert _ids(pee._read_jsonl(_ledger(root, "order_intent"), observe=seen.append)) == ["ok"]
    assert _ids(seen) == ["ok"]  # the incident observer never sees a non-fresh row
    _assert_no_unguarded_v3(monitor, guard)


def test_policy_evidence_bindings_read_jsonl_and_digest(env):
    root, guard, monitor = env
    from research import policy_evidence_bindings as peb
    assert _ids(peb._read_jsonl(_ledger(root, "lifecycle"))) == ["ok"]
    for name in peb.LEDGERS:
        _ledger(root, name)
    assert len(peb._binding_inputs_digest(root / "v3")) == 64
    report = _assert_no_unguarded_v3(monitor, guard)
    assert report["inventory_stream_opens"]  # the raw-byte cache key is declared inventory, not evidence


def test_evidence_coverage_triage_counts_fresh_rows_only(env):
    root, guard, monitor = env
    from research.evidence_coverage_triage import ledger_source_counts
    for name in ("opportunity", "decision", "order_intent", "execution", "lifecycle", "market_segment"):
        _ledger(root, name)
    assert set(ledger_source_counts(root / "v3").values()) == {1}
    _assert_no_unguarded_v3(monitor, guard)


def test_fill_model_measure_decision_latency(env):
    root, guard, monitor = env
    from research import fill_model as fm

    def opp(rid, sig):
        return {"record_id": rid, "episode_id": f"e-{rid}", "signal_ts": sig}

    def intent(rid, elig, **extra):
        return {"record_id": rid, "episode_id": f"e-{rid}", **extra, "chase_schedule": {"action_timing_receipts": [
            {"action_type": "INITIAL_SUBMIT", "eligibility_ts": elig}]}}

    _ledger(root, "opportunity", [opp("ok", FLOOR + 10), opp("pre", FLOOR - 500), opp("str", FLOOR + 5)])
    _ledger(root, "order_intent", [intent("ok", FLOOR + 11), intent("pre", FLOOR - 499),
                                   intent("str", FLOOR + 6, requested_start_ts=FLOOR - 50)])
    assert fm.measure_decision_latency(root / "v3" / "ledgers")["n"] == 1
    _assert_no_unguarded_v3(monitor, guard)


def test_strategy_lab_recorded_fill_atr(env):
    root, guard, monitor = env
    from strategy_lab import simulator
    _ledger(root, "execution", [dict(r, atr14_pct_at_fill=0.05) for r in ROWS])
    assert sorted(simulator.recorded_fill_atr(str(root), extra_dirs=())) == ["ev-ok"]
    _assert_no_unguarded_v3(monitor, guard)


def test_shared_context_coverage_index(env):
    root, guard, monitor = env
    from research import shared_context_coverage as scc
    _ledger(root, "opportunity")
    _ledger(root, "market_segment")
    db, scanned, complete = scc._advance_context_index(root, 2 * 1024 * 1024, 2000)
    try:
        episodes = sorted(r[0] for r in db.execute("SELECT episode FROM refs"))
    finally:
        db.close()
    assert complete and episodes == ["e-ok", "e-ok"]
    _assert_no_unguarded_v3(monitor, guard)


def test_baseline_execution_context_row_index(env):
    root, guard, monitor = env
    from research.baseline_execution_context import VerifiedLedgerRowIndex
    path = _ledger(root, "pre_entry_features")
    with eg.guarded_read("v3/ledgers/pre_entry_features.jsonl", "inventory"):  # the caller's pin, not evidence
        raw = path.read_bytes()
    with VerifiedLedgerRowIndex() as index:
        count = index.add_source(root, "v3/ledgers/pre_entry_features.jsonl",
                                 expected_sha=hashlib.sha256(raw).hexdigest(), expected_size=len(raw))
        assert count == 1  # whole file still pinned/digested; only the fresh row is indexed
    _assert_no_unguarded_v3(monitor, guard)


def test_research_v3_store_load_ids_is_declared_inventory(env):
    root, guard, monitor = env
    from research_v3_store import V3EvidenceStore
    path = _ledger(root, "decision")
    assert len(V3EvidenceStore._load_ids(path)) == 4  # store dedup/truncation proof covers the whole durable file
    report = _assert_no_unguarded_v3(monitor, guard)
    assert report["inventory_stream_opens"].get("v3/ledgers/decision.jsonl")


def test_no_declared_epoch_leaves_readers_unchanged(tmp_path):
    import research_v3_report as rep
    import research_v3_candidates as cand
    path = _ledger(tmp_path, "decision")
    assert len(rep._read_ledger(path)) == 4 and len(cand._read_jsonl(path)) == 4


def test_every_known_reader_module_uses_the_guarded_opener():
    """Static backstop for readers too entangled to drive here (entry_baseline_replay, local_scan_reconciliation)."""
    modules = ("research/entry_baseline_replay.py", "research/local_scan_reconciliation.py",
               "research/research_v3_report.py", "research/v3_policy_report_adapter.py", "research_v3_candidates.py",
               "research/policy_evidence_evaluator.py", "research/policy_evidence_bindings.py",
               "research/evidence_coverage_triage.py", "research/fill_model.py", "strategy_lab/simulator.py",
               "research/shared_context_coverage.py", "research/baseline_execution_context.py",
               "research/v3_ledger_index.py")
    for rel in modules:
        text = (HERE / rel).read_text(encoding="utf-8")
        assert "open_v3_ledger(" in text, rel
        if rel != "research/v3_ledger_index.py":
            assert "admit_v3_row(" in text, rel
    replay = (HERE / "research/entry_baseline_replay.py").read_text(encoding="utf-8")
    assert replay.count("open_v3_ledger(") >= 3 and replay.count("admit_v3_row(") >= 3
    lsr = (HERE / "research/local_scan_reconciliation.py").read_text(encoding="utf-8")
    assert lsr.count("open_v3_ledger(") == 2 and lsr.count("admit_v3_row(") == 2
