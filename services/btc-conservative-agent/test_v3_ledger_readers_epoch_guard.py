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
               "research/v3_ledger_index.py", "research/genome_grid_study.py", "research/genome_mix_match.py")
    for rel in modules:
        text = (HERE / rel).read_text(encoding="utf-8")
        assert "open_v3_ledger(" in text, rel
        if rel.startswith("research/genome_"):
            assert "admit_stream_row(" in text and "open(path" not in text, rel  # raw open at L126 / L98 is gone
        elif rel != "research/v3_ledger_index.py":
            assert "admit_v3_row(" in text, rel
    replay = (HERE / "research/entry_baseline_replay.py").read_text(encoding="utf-8")
    assert replay.count("open_v3_ledger(") >= 3 and replay.count("admit_v3_row(") >= 3
    lsr = (HERE / "research/local_scan_reconciliation.py").read_text(encoding="utf-8")
    assert lsr.count("open_v3_ledger(") == 2 and lsr.count("admit_v3_row(") == 2


# ---- HM 19 Oct (a): hard-rejected straddle rows are their own number, separate from "unguarded" --------------------
def test_receipt_block_reports_hard_rejected_straddle_and_unstamped_separately(env):
    root, guard, monitor = env
    import research_v3_report as rep
    from research.generation_receipt import build_generation_receipt
    rep._read_ledger(_ledger(root, "market_segment"))
    block = guard.receipt_block()
    assert block["straddle_rows_rejected"] == 1 and block["unstamped_rows_rejected"] == 1
    assert block["straddle_rows_rejected_by_stream"] == {"v3/ledgers/market_segment.jsonl": 1}
    reads = _assert_no_unguarded_v3(monitor, guard)  # guarded: 0 unguarded while the rejections are counted
    block.update(straddle_rows_unguarded=reads["straddle_rows_unguarded"],
                 unstamped_rows_unguarded=reads["unstamped_rows_unguarded"])
    receipt = build_generation_receipt({"required_report_status": {}}, integrity={"report_status": "VALID"},
                                       data_epoch=block)
    assert receipt["hard_rejected_rows"] == {"straddle_rows_rejected": 1, "unstamped_rows_rejected": 1}
    assert not any("unguarded" in r for r in receipt["reasons"])


# ---- HM 19 Oct (b): the genome-grid scheduled job (separate process) installs the guard itself --------------------
def test_guard_from_roots_takes_manifest_and_floor_from_their_own_roots(tmp_path):
    mirror, canon = tmp_path / "tree", tmp_path / "canonical-research-data"
    mirror.mkdir()
    canon.mkdir()
    (mirror / "data_epoch.json").write_text(json.dumps(de.new_manifest("ce-20261018-fresh", started_at_ts=FLOOR)))
    no_floor = eg.guard_from_roots([mirror])
    assert no_floor.declared and no_floor.floor_ts is None and not no_floor.admit_v3_ledger_row("v3/ledgers/d.jsonl", OK)
    (canon / "fresh_start_epoch.json").write_text(json.dumps({"epoch_ts": FLOOR}))
    g = eg.guard_from_roots([mirror, canon])
    assert g.floor_ts == FLOOR and [g.admit_v3_ledger_row("v3/ledgers/d.jsonl", r) for r in ROWS] == [False, False, True, False]
    assert not eg.guard_from_roots([tmp_path / "missing"]).declared


def test_genome_grid_and_mix_match_readers_hard_reject(env):
    root, guard, monitor = env
    from research import genome_grid_study as gg
    from research import genome_mix_match as mm
    for name in ("decision", "opportunity", "lifecycle"):
        _ledger(root, name)
    for reader in (gg._read_jsonl, mm._read_jsonl):
        for name in ("decision", "opportunity", "lifecycle"):
            assert _ids(reader(root / "v3" / "ledgers" / f"{name}.jsonl")) == ["ok"], (reader.__module__, name)
    # non-ledger stream rows go through EpochGuard.admit (pre-epoch ts rejected); tape is epoch-independent
    sr = root / "signal_replay.jsonl"
    sr.write_text(json.dumps({"trade_id": "old", "ts": FLOOR - 600}) + "\n" + json.dumps({"trade_id": "new", "ts": FLOOR + 600}) + "\n")
    assert [r["trade_id"] for r in gg._read_jsonl(sr)] == ["new"]
    tape = root / "market_microstructure_1s.jsonl"
    tape.write_text(json.dumps({"ts": FLOOR - 600, "bid": 1}) + "\n")
    assert len(list(gg._read_jsonl(tape))) == 1
    _assert_no_unguarded_v3(monitor, guard)


def test_genome_grid_run_installs_the_guard_before_reading(monkeypatch, tmp_path):
    from research import genome_grid_study as gg
    (tmp_path / "data_epoch.json").write_text(json.dumps(de.new_manifest("ce-20261018-fresh", started_at_ts=FLOOR)))
    canon = tmp_path / "canon"
    canon.mkdir()
    (canon / "fresh_start_epoch.json").write_text(json.dumps({"epoch_ts": FLOOR}))
    monkeypatch.setattr(gg, "epoch_guard_roots", lambda mirror: [mirror, canon])
    seen = {}

    def stop(mirror, tier_a):
        seen["guard"] = eg.process_guard()
        raise RuntimeError("stop after guard install")

    monkeypatch.setattr(gg, "load_tape", stop)
    try:
        with pytest.raises(RuntimeError):
            gg.run(tmp_path, tmp_path / "tierA", tmp_path / "out")
        assert seen["guard"].declared and seen["guard"].floor_ts == FLOOR
    finally:
        eg.set_process_guard(None)
    src = (HERE / "research/genome_grid_study.py").read_text(encoding="utf-8")
    assert 'report["data_epoch"] = guard.receipt_block()' in src


# ---- HM 19 Oct CHANGES on 27778ae7 --------------------------------------------------------------------------------
def _declare(root: Path, *, manifest=True, floor=True):
    root.mkdir(parents=True, exist_ok=True)
    if manifest:
        (root / "data_epoch.json").write_text(json.dumps(de.new_manifest("ce-20261018-fresh", started_at_ts=FLOOR)))
    if floor:
        (root / "fresh_start_epoch.json").write_text(json.dumps({"epoch_ts": FLOOR}))
    return root


@pytest.mark.parametrize("manifest_text", [None, "{not json", json.dumps({"epoch_id": "bad id"})])
def test_missing_or_invalid_manifest_with_a_floor_fails_closed_and_alarms(tmp_path, manifest_text):
    """Item 3: fresh_start_epoch.json present + data_epoch.json missing/corrupt must NOT become EpochGuard(None)."""
    from research.generation_receipt import build_generation_receipt
    root = _declare(tmp_path / "data", manifest=False)
    if manifest_text is not None:
        (root / "data_epoch.json").write_text(manifest_text)
    for guard in (eg.EpochGuard.from_data_dir(root), eg.guard_from_roots([tmp_path / "nothing", root])):
        assert guard.manifest_missing and guard.declared and guard.floor_missing
        assert [guard.admit_v3_ledger_row("v3/ledgers/decision.jsonl", r) for r in ROWS] == [False] * 4
        assert guard.admit("signal_replay.jsonl", {"ts": FLOOR + 600}) is False
        assert guard.rejected["v3/ledgers/decision.jsonl"] == {eg.MANIFEST_MISSING: 4}
        block = guard.receipt_block()
        assert block["manifest_missing_fail_closed"] is True
        assert block["fresh_rule"]["status"] == "RED" and block["fresh_rule"]["reason"] == eg.MANIFEST_MISSING
        receipt = build_generation_receipt({"required_report_status": {}}, integrity={"report_status": "VALID"},
                                           data_epoch=block)
        assert receipt["level"] == "RED" and any("FAIL-CLOSED" in r for r in receipt["reasons"])
    # no floor and no manifest anywhere: still "nothing declared" (pre-reset deploys unchanged)
    assert not eg.EpochGuard.from_data_dir(tmp_path / "nothing").declared


def test_missing_manifest_with_floor_makes_every_reader_return_nothing(env, tmp_path):
    root, _, _ = env
    import research_v3_report as rep
    from research import genome_grid_study as gg
    eg.set_process_guard(eg.EpochGuard.from_data_dir(_declare(tmp_path / "d", manifest=False)))
    assert rep._read_ledger(_ledger(root, "decision")) == []
    assert list(gg._read_jsonl(_ledger(root, "opportunity"))) == []


def test_analyzer_prefers_a_declaring_root_and_otherwise_fails_closed(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(HERE))  # research/ carries a fail-closed stub of the same name
    import analyzer_research_engine_v62 as engine
    bad, good = _declare(tmp_path / "bad", manifest=False), _declare(tmp_path / "good")
    monkeypatch.setattr(engine, "_EPOCH_MANIFEST_DIR", None)
    for roots, missing in (([bad, good], False), ([bad], True)):
        monkeypatch.setattr(engine, "_EPOCH_GUARD", None)
        monkeypatch.setattr(engine, "_epoch_manifest_roots", lambda roots=roots: roots)
        guard = engine._epoch_guard()
        assert guard.declared and guard.manifest_missing is missing and eg.process_guard() is guard
        assert guard.admit_v3_ledger_row("v3/ledgers/decision.jsonl", OK) is (not missing)


def test_declared_epoch_with_missing_floor_via_new_helpers_admits_nothing(tmp_path):
    """Item 4: a declared manifest whose floor is nowhere (mirror tree only) fails closed through every helper."""
    mirror = _declare(tmp_path / "tree", floor=False)
    for guard in (eg.guard_from_roots([mirror]), eg.ensure_process_guard([mirror])):
        assert guard.declared and guard.floor_missing and not guard.manifest_missing
        assert [guard.admit_v3_ledger_row("v3/ledgers/decision.jsonl", r) for r in ROWS] == [False] * 4
        assert eg.admit_stream_row(mirror / "v3/ledgers/decision.jsonl", OK) is False
        assert guard.fresh_rule_status() == {"status": "RED", "reason": eg.NO_FRESH_FLOOR,
                                             "v3_readers": guard.fresh_rule_status()["v3_readers"]}
    assert eg.process_guard().floor_missing  # ensure_process_guard installed it


def test_ensure_process_guard_keeps_an_installed_guard_and_skips_undeclared(tmp_path):
    assert not eg.ensure_process_guard([tmp_path / "none"]).declared
    assert eg._PROCESS_GUARD is None  # nothing declared: nothing installed
    analyzer = eg.EpochGuard(de.new_manifest("ce-20261018-fresh", started_at_ts=FLOOR), floor_ts=FLOOR)
    eg.set_process_guard(analyzer)
    assert eg.ensure_process_guard([_declare(tmp_path / "other", manifest=False)]) is analyzer


def test_scoped_process_guard_is_restored_after_a_library_call(tmp_path):
    with eg.scoped_process_guard([_declare(tmp_path / "d")]) as guard:
        assert eg.process_guard() is guard and guard.declared
    assert eg._PROCESS_GUARD is None


def test_fill_model_latency_installs_the_guard_when_run_standalone(tmp_path):
    """Item 2: measure_decision_latency outside the analyzer (genome grid / walk-forward) still hard-rejects."""
    from research import fill_model as fm
    root = _declare(tmp_path / "data")
    pre = dict(PRE, signal_ts=FLOOR - 400)
    _ledger(root, "opportunity", [dict(pre, episode_id=f"p{i}") for i in range(30)])
    intent = {"chase_schedule": {"action_timing_receipts": [{"action_type": "INITIAL_SUBMIT",
                                                            "eligibility_ts": FLOOR - 399}]}}
    _ledger(root, "order_intent", [dict(pre, episode_id=f"p{i}", **intent) for i in range(30)])
    assert eg._PROCESS_GUARD is None
    out = fm.measure_decision_latency(root / "v3" / "ledgers")
    assert out["source"] == "DEFAULT_NO_MEASUREMENT" and out["n"] == 0  # pre-epoch rows never measured
    assert eg._PROCESS_GUARD is None  # scoped: the library call leaves no process guard behind


@pytest.mark.parametrize("module,argv_fn", [
    ("research.walk_forward_scorer", lambda d: ["--table", str(d / "t.csv"), "--data-dir", str(d), "--out-dir", str(d)]),
    ("research.shadow_exit_report", lambda d: ["--mirror", str(d), "--out-dir", str(d / "out")]),
    ("research.local_scan_reconciliation", lambda d: ["--repo-root", str(d), "--data-root", str(d),
                                                      "--source-revision", "x", "--config-signature", "y"]),
])
def test_standalone_entry_points_install_the_guard_before_reading(tmp_path, monkeypatch, module, argv_fn):
    import importlib
    mod = importlib.import_module(module)
    root = _declare(tmp_path / "data")
    seen = {}

    def stop(*a, **k):
        seen["guard"] = eg.process_guard()
        raise KeyboardInterrupt

    target = {"research.walk_forward_scorer": "run", "research.shadow_exit_report": "build_report",
              "research.local_scan_reconciliation": "reconcile_scans"}[module]
    monkeypatch.setattr(mod, target, stop)
    with pytest.raises(KeyboardInterrupt):
        mod.main(argv_fn(root))
    assert seen["guard"].declared and seen["guard"].floor_ts == FLOOR


def test_evidence_coverage_triage_main_installs_the_guard(tmp_path, monkeypatch):
    from research import evidence_coverage_triage as ect
    root = _declare(tmp_path / "data")
    (root / "v3").mkdir()
    seen = {}

    def stop(*a, **k):
        seen["guard"] = eg.process_guard()
        raise KeyboardInterrupt

    monkeypatch.setattr(ect, "build_evidence_coverage_triage_report", stop)
    monkeypatch.setattr(ect, "verify_archive_receipts", lambda root: None)
    monkeypatch.setattr(ect, "ledger_source_counts", lambda root: None)
    (tmp_path / "b.json").write_text("{}")
    with pytest.raises(KeyboardInterrupt):
        ect.main([str(tmp_path / "b.json"), "--v3-root", str(root / "v3"), "--output", str(tmp_path / "o.json")])
    assert seen["guard"].declared and seen["guard"].floor_ts == FLOOR


# ---- item 4: behavioural tests through the real report builders ---------------------------------------------------
def test_entry_baseline_replay_materializer_admits_only_fresh_opportunities(env):
    root, guard, monitor = env
    from research.entry_baseline_replay import materialize_v3_opportunity_replay
    _ledger(root, "opportunity")
    _ledger(root, "market_segment")
    report = materialize_v3_opportunity_replay(root)
    assert [e["episode_id"] for e in report["episode_receipts"]] == ["e-ok"]
    assert report["same_opportunity_count"] == 1
    assert guard.rejected["v3/ledgers/opportunity.jsonl"] == {"PRE_EPOCH": 1, "STRADDLE": 1, "UNSTAMPED": 1}
    assert guard.hard_rejected_counts()["straddle_rows_rejected_by_stream"]["v3/ledgers/market_segment.jsonl"] == 1
    _assert_no_unguarded_v3(monitor, guard)


def test_local_scan_reconciliation_cached_reference_rejects_a_pre_epoch_row(env):
    root, guard, monitor = env
    from research import local_scan_reconciliation as lsr
    path = _ledger(root, "decision", (PRE, OK))
    lines = [json.dumps(r, sort_keys=True, separators=(",", ":")).encode() + b"\n" for r in (PRE, OK)]

    class Store:
        def ledger_path(self, name):
            return root / "v3" / "ledgers" / f"{name}.jsonl"

    def ref(i):
        return {"ledger": "decision", "byte_offset": sum(map(len, lines[:i])), "row_length": len(lines[i])}

    with pytest.raises(ValueError, match="CENSUS_CACHED_REFERENCE_PRE_EPOCH"):
        lsr._verify_page_ref(Store(), ref(0), {"epoch": EPOCH, "revision": "r"}, "cfg")
    with pytest.raises(ValueError, match="CENSUS_CACHED_REFERENCE_INVALID"):  # fresh row passes the gate
        lsr._verify_page_ref(Store(), ref(1), {"epoch": EPOCH, "revision": "r"}, "cfg")
    _assert_no_unguarded_v3(monitor, guard)


def test_research_v3_report_builder_counts_only_fresh_opportunities(env):
    root, guard, _ = env
    from research_v3_store import V3EvidenceStore
    from research.research_v3_report import build_safe_policy_genome_v3_report
    data, reports = root / "data", root / "reports"
    reports.mkdir()
    store = V3EvidenceStore(data, epoch_id="epoch-v3")
    for rid, ts in (("pre", FLOOR - 500), ("ok", FLOOR + 10)):
        store.append("opportunity", {"record_id": f"opportunity:{rid}", "opportunity_id": f"opportunity:{rid}",
                                     "episode_id": rid, "signal_ts": ts})
    eg.set_process_guard(None)
    unguarded = build_safe_policy_genome_v3_report(data, reports, candidates=[])
    eg.set_process_guard(guard)
    guarded = build_safe_policy_genome_v3_report(data, reports, candidates=[])
    assert unguarded["epoch_scope"]["included_fresh_rows"] == 2  # control: the pre-epoch row is otherwise counted
    assert guarded["epoch_scope"]["included_fresh_rows"] == 1
    assert guarded["collection"]["independent_opportunities"] == 1
    assert guard.rejected["v3/ledgers/opportunity.jsonl"] == {"PRE_EPOCH": 1}


# ---- HM 19 Oct CHANGES on fe334474 --------------------------------------------------------------------------------
def test_shadow_exit_report_reader_drops_pre_epoch_rows(env):
    """#1: shadow_exit_paths.jsonl is epoch-scoped; the analyzer calls _read_jsonl in-process (engine L18842)."""
    root, guard, monitor = env
    from research import shadow_exit_report as ser
    path = root / "shadow_exit_paths.jsonl"
    path.write_text(json.dumps({"record_id": "old", "ts": FLOOR - 600}) + "\n"
                    + json.dumps({"record_id": "new", "ts": FLOOR + 600}) + "\n")
    assert [r["record_id"] for r in ser._read_jsonl(path)] == ["new"]
    assert guard.rejected["shadow_exit_paths.jsonl"]
    report = monitor.report(guard.manifest, FLOOR)
    assert "shadow_exit_paths.jsonl" not in report["unguarded_stream_reads"]
    eg.set_process_guard(None)
    assert len(list(ser._read_jsonl(path))) == 2  # no declared epoch: unchanged


def test_fail_closed_genome_run_exits_non_zero_and_dashboard_shows_red(tmp_path, monkeypatch, capsys):
    """#2: a fail-closed genome run must not print OK / exit 0, and /api/genome-grid must show RED."""
    from research import genome_grid_study as gg
    from research import research_dashboard as dash
    root = _declare(tmp_path / "data", manifest=False)  # floor present, manifest missing -> fail closed
    out = tmp_path / "out"
    monkeypatch.setattr(gg, "epoch_guard_roots", lambda mirror: [root])
    monkeypatch.setattr(gg, "load_tape", lambda *a, **k: pytest.fail("must not read tape when fail-closed"))
    monkeypatch.setattr(gg, "analyzer_cycle_busy", lambda path: False)
    monkeypatch.setattr(gg, "_below_normal", lambda: None)
    code = gg.main(["--mirror", str(root), "--tier-a", str(tmp_path / "tierA"), "--out-dir", str(out)])
    printed = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert code != 0 and printed["status"] == "FAIL_CLOSED" and printed["reason"] == eg.MANIFEST_MISSING
    report = json.loads((out / "genome_grid_report.json").read_text())
    assert report["status"] == "FAIL_CLOSED" and report["data_epoch"]["fresh_rule"]["status"] == "RED"
    monkeypatch.setattr(dash, "GENOME_GRID_REPORT_PATH", out / "genome_grid_report.json")
    payload = dash._genome_grid_payload()
    assert payload["status"] == "FAIL_CLOSED" and payload["level"] == "RED" and payload["reason"] == eg.MANIFEST_MISSING
    # an older OK-looking report whose epoch block is RED is also served as FAIL_CLOSED
    (out / "genome_grid_report.json").write_text(json.dumps({"schema": "genome_grid_report_v1", "generated_at": None,
                                                              "data_epoch": {"fresh_rule": {"status": "RED", "reason": "X"}}}))
    assert dash._genome_grid_payload()["status"] == "FAIL_CLOSED"


def test_engine_v3_ledger_rows_use_the_nested_straddle_rule(tmp_path, monkeypatch):
    """#3: _v3_ledger_rows (missed-opportunity proof, fill_time_guard_counterfactual) applies admit_v3_row, so a row
    straddling the floor (top-level ts after it, nested window before it) and an unstamped row are rejected."""
    monkeypatch.syspath_prepend(str(HERE))
    import analyzer_research_engine_v62 as engine
    from research_v3_store import V3EvidenceStore
    store = V3EvidenceStore(tmp_path, epoch_id="epoch-v3")
    straddle = {"record_id": "execution:str", "episode_id": "str", "ts": FLOOR + 100, "signal_ts": FLOOR + 100,
                "segment_ref": {"start_ts": FLOOR - 50, "end_ts": FLOOR + 50}}
    rows = [{"record_id": "execution:pre", "episode_id": "pre", "ts": FLOOR - 500, "signal_ts": FLOOR - 500}, straddle,
            {"record_id": "execution:ok", "episode_id": "ok", "ts": FLOOR + 100, "signal_ts": FLOOR + 100},
            {"record_id": "execution:uns", "episode_id": "uns", "ts": FLOOR + 100}]  # only a bare ts: UNSTAMPED
    for row in rows:
        store.append("execution", row)
    monkeypatch.setenv("BTC_AGENT_DATA_DIR", str(tmp_path))
    guard = eg.EpochGuard(de.new_manifest("ce-20261018-fresh", started_at_ts=FLOOR), floor_ts=FLOOR, require_floor=True)
    monkeypatch.setattr(engine, "_EPOCH_GUARD", guard)
    eg.set_process_guard(guard)
    assert guard.admit("execution.jsonl", straddle) is True  # control: the old single-ts check admitted it
    got = [r["episode_id"] for r in engine._v3_ledger_rows("execution")]
    assert got == ["ok"]
    assert guard.rejected["v3/ledgers/execution.jsonl"] == {"PRE_EPOCH": 1, "STRADDLE": 1, "UNSTAMPED": 1}
    src = (HERE / "analyzer_research_engine_v62.py").read_text(encoding="utf-8")
    body = src[src.index("def _v3_ledger_generation_rows"):src.index("def _compressed_shadow_rows")]
    assert "admit_v3_row(" in body and "_epoch_admit(" not in body and "_load_jsonl_rows(" not in body


def test_scoped_process_guard_never_clears_a_guard_installed_meanwhile(tmp_path):
    """#4: the scoped guard clears only itself."""
    other = eg.EpochGuard(de.new_manifest("ce-20261018-fresh", started_at_ts=FLOOR), floor_ts=FLOOR)
    with eg.scoped_process_guard([_declare(tmp_path / "d")]) as mine:
        assert eg.process_guard() is mine
        eg.set_process_guard(other)  # e.g. the analyzer installed its own guard during the call
    assert eg._PROCESS_GUARD is other


@pytest.mark.parametrize("setup", ["env", "marker", "corrupt_floor"])
def test_undeclared_epoch_fails_closed_when_required(tmp_path, monkeypatch, setup):
    """#5: both files missing (env/marker required) or a corrupt floor with no manifest -> RED, admits nothing."""
    from research.generation_receipt import build_generation_receipt
    root = tmp_path / "data"
    root.mkdir()
    monkeypatch.delenv(eg.REQUIRE_EPOCH_ENV, raising=False)
    if setup == "env":
        monkeypatch.setenv(eg.REQUIRE_EPOCH_ENV, "1")
    elif setup == "marker":
        (root / eg.REQUIRE_EPOCH_MARKER).write_text("{}")
    else:
        (root / "fresh_start_epoch.json").write_text("{corrupt")
    guard = eg.guard_from_roots([root])
    assert guard.declared and guard.manifest_missing and guard.floor_missing
    expect = eg.MANIFEST_MISSING if setup == "corrupt_floor" else eg.EPOCH_REQUIRED_UNDECLARED
    assert guard.fail_closed_reason == expect
    assert [guard.admit_v3_ledger_row("v3/ledgers/decision.jsonl", r) for r in ROWS] == [False] * 4
    assert guard.admit("signal_replay.jsonl", {"ts": FLOOR + 600}) is False
    block = guard.receipt_block()
    assert block["fresh_rule"] == {"status": "RED", "reason": expect, "v3_readers": block["fresh_rule"]["v3_readers"]}
    receipt = build_generation_receipt({"required_report_status": {}}, integrity={"report_status": "VALID"},
                                       data_epoch=block)
    assert receipt["level"] == "RED"


def test_undeclared_epoch_not_required_still_admits_and_is_logged_loudly(tmp_path, monkeypatch, capsys):
    """#5/#7: without a requirement nothing changes for pre-reset tools, but ensure_process_guard says so on stderr."""
    monkeypatch.delenv(eg.REQUIRE_EPOCH_ENV, raising=False)
    guard = eg.ensure_process_guard([tmp_path / "nothing"])
    assert not guard.declared and eg._PROCESS_GUARD is None
    err = capsys.readouterr().err
    assert "EPOCH GUARD NOT DECLARED" in err and "data_epoch.json" in err and eg.REQUIRE_EPOCH_ENV in err
    monkeypatch.setenv(eg.REQUIRE_EPOCH_ENV, "1")
    required = eg.ensure_process_guard([tmp_path / "nothing"])
    assert required.declared and eg._PROCESS_GUARD is required
    assert "EPOCH GUARD FAIL-CLOSED" in capsys.readouterr().err


def test_analyzer_and_standalone_roots_pick_the_same_floor(tmp_path, monkeypatch):
    """#6: one root list (epoch_roots) and one resolver for the analyzer and every standalone job, including
    canonical-research-data, so the floor is the same."""
    monkeypatch.syspath_prepend(str(HERE))
    import analyzer_research_engine_v62 as engine
    monkeypatch.delenv("BTC_DATA_EPOCH_DIR", raising=False)
    monkeypatch.delenv(eg.REQUIRE_EPOCH_ENV, raising=False)
    canon = _declare(tmp_path / "agent" / "canonical-research-data")  # manifest + the real floor
    mirror = tmp_path / "tree"
    mirror.mkdir()
    (mirror / "data_epoch.json").write_text(json.dumps(de.new_manifest("ce-20261018-fresh", started_at_ts=FLOOR)))
    (tmp_path / "fresh_start_epoch.json").write_text(json.dumps({"epoch_ts": FLOOR - 9999}))  # a stale floor above
    monkeypatch.setattr(eg, "AGENT_DIR", tmp_path / "agent")
    monkeypatch.setattr(eg, "LAPTOP_MIRROR_TREE", str(mirror))
    monkeypatch.setattr(engine, "_analyzer_data_root", lambda: str(tmp_path / "agent" / "missing"))
    monkeypatch.delenv("BTC_AGENT_DATA_DIR", raising=False)
    analyzer_roots = [Path(r) for r in engine._epoch_manifest_roots()]
    assert canon in analyzer_roots and mirror in analyzer_roots
    a_guard, a_root = eg.resolve_epoch_guard(analyzer_roots)
    s_guard = eg.guard_from_roots(eg.standalone_guard_roots(mirror))  # the genome grid passes the mirror tree
    assert a_root == canon and a_guard.floor_ts == s_guard.floor_ts == FLOOR
    monkeypatch.setattr(engine, "_EPOCH_GUARD", None)
    assert engine._epoch_guard().floor_ts == FLOOR
