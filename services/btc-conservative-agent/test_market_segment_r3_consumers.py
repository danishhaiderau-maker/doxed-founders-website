"""R3: every market_segment consumer drops the input or fails closed visibly."""
from __future__ import annotations

import builtins
import re
from pathlib import Path

import pytest

import market_segment_r3 as msr3

HERE = Path(__file__).resolve().parent
REASON = "INVALID_R3_MARKET_SEGMENT_INPUT"


@pytest.fixture
def no_segment_reads(monkeypatch):
    """Fail the test if anything opens a market_segment ledger or envelope."""
    real_open = builtins.open
    real_read_bytes = Path.read_bytes

    def guard(path):
        text = str(path)
        if "market_segment" in text:
            raise AssertionError(f"R3 live read of {text}")

    def fake_open(file, *a, **k):
        guard(file)
        return real_open(file, *a, **k)

    def fake_read_bytes(self):
        guard(self)
        return real_read_bytes(self)

    monkeypatch.setattr(builtins, "open", fake_open)
    monkeypatch.setattr(Path, "read_bytes", fake_read_bytes)


def _poison(root: Path) -> Path:
    v3 = root / "v3"
    (v3 / "ledgers").mkdir(parents=True)
    (v3 / "recovery_ledgers").mkdir(parents=True)
    (v3 / "market_segments" / "ab").mkdir(parents=True)
    row = '{"ledger":"market_segment","context_role":"FULL_LIFECYCLE","segment_ref":{"sha256":"' + "ab" * 32 + '","relative_path":"v3/market_segments/ab/' + "ab" * 32 + '.json"}}\n'
    (v3 / "ledgers" / "market_segment.jsonl").write_text(row)
    (v3 / "recovery_ledgers" / "market_segment.jsonl").write_text(row)
    (v3 / "market_segments" / "ab" / ("ab" * 32 + ".json")).write_text('{"schema":"market_segment_v3"}')
    return v3


def _src(rel: str) -> str:
    return (HERE / rel).read_text()


# --- the gate itself -------------------------------------------------------

def test_gate_is_closed_and_marker_is_visible():
    assert msr3.INPUT_ALLOWED is False
    marker = msr3.invalid("x")
    assert marker == {"status": "INVALID", "rule": "R3", "reason": REASON, "source": "x", "input_dropped": True}
    assert msr3.rows("v3/ledgers/market_segment.jsonl", loader=lambda: [{"x": 1}]) == []
    with pytest.raises(msr3.MarketSegmentInputInvalid, match=REASON):
        msr3.require_envelope("s")


# --- behavioural tests (poisoned ledgers, any read fails) ------------------

def test_policy_evidence_evaluator_envelope_fails_closed(tmp_path, no_segment_reads):
    from research.policy_evidence_evaluator import _load_envelope
    v3 = _poison(tmp_path)
    assert _load_envelope(v3, {"segment_ref": {"sha256": "ab" * 32}}) == (None, REASON)


def test_policy_evidence_bindings_segment_fails_closed(tmp_path, no_segment_reads):
    from research.policy_evidence_bindings import _verify_segment
    v3 = _poison(tmp_path)
    digest, reasons = _verify_segment(v3, {"segment_ref": {"sha256": "ab" * 32}})
    assert digest is None and reasons == [REASON]


def test_lifecycle_completion_reconciler_ignores_segment_rows():
    from lifecycle_completion_reconciler import _post_observation
    row = {"ledger": "market_segment", "context_role": "FULL_LIFECYCLE",
           "coverage": {"status": "COMPLETE", "start_ts": 1, "end_ts": 2}}
    assert _post_observation([row]) is None


def test_lifecycle_bundles_reference_no_segments(tmp_path, no_segment_reads):
    from lifecycle_bundles import _referenced_market_segments
    v3 = _poison(tmp_path)
    rows = [{"segment_ref": {"sha256": "ab" * 32, "relative_path": "v3/market_segments/ab/" + "ab" * 32 + ".json"}}]
    assert _referenced_market_segments(tmp_path, rows) == []
    assert "if _msr3.INPUT_ALLOWED else []" in _src("lifecycle_bundles.py")


def test_research_v3_future_paths_drops_segment_rows():
    import research_v3_future_paths as m
    assert '_msr3.rows("v3/ledgers/market_segment.jsonl")' in _src("research_v3_future_paths.py")
    assert m.SEGMENT_WRITES_RETIRED_R17 is True


def test_recover_archived_future_paths_has_no_owners(tmp_path, no_segment_reads):
    from recover_archived_future_paths import _latest_unknown_owners
    _poison(tmp_path)
    assert _latest_unknown_owners(tmp_path, "epoch-x") == []


def test_research_v3_candidates_never_loads_segment(tmp_path, no_segment_reads):
    from research_v3_candidates import _load_segment
    _poison(tmp_path)
    ref = {"relative_path": "v3/market_segments/ab/" + "ab" * 32 + ".json", "sha256": "ab" * 32}
    assert _load_segment(tmp_path, ref) == []


def test_evidence_coverage_triage_counts_invalid(tmp_path, no_segment_reads):
    from research.evidence_coverage_triage import ledger_source_counts
    v3 = _poison(tmp_path)
    assert ledger_source_counts(v3)["market_segments"] == REASON


def test_v3_policy_report_adapter_marks_invalid(tmp_path, no_segment_reads):
    from research.v3_policy_report_adapter import candidate_from_genome
    cand = candidate_from_genome({"number_one_strategy": None}, {"market_segments": 999, "execution_rows": 5})
    text = repr(cand)
    assert REASON in text
    src = _src("research/v3_policy_report_adapter.py")
    assert 'segment_count = 0  # R3' in src


def test_discovery_cohort_scorecard_drops_segment_identity():
    import discovery_cohort_scorecard as m
    flat = {a for aliases in m._SEQUENCE_IDENTITIES.values() for a in aliases}
    assert not any("market_segment" in a for a in flat)


def test_discovery_scorecard_publication_ignores_segment_ids():
    src = _src("research/discovery_scorecard_publication.py")
    assert 'row.get("market_segment_ids")' not in src


def test_policy_evidence_library_authority_excludes_segments():
    assert "CONTENT_ADDRESSED_MARKET_SEGMENTS" not in _src("research/policy_evidence_library.py")


def test_shared_context_coverage_indexes_only_opportunity():
    src = _src("research/shared_context_coverage.py")
    assert "'market_segment'" not in src and "for ledger in ('opportunity',):" in src


def test_baseline_execution_context_fails_closed():
    src = _src("research/baseline_execution_context.py")
    assert src.count("_msr3.require_envelope(") == 2


def test_conservative_shadow_report_blocks_payload_read():
    src = _src("research/conservative_shadow_report.py")
    i = src.index("blockers.append(_msr3.REASON)")
    assert i < src.index("payload = candidate.read_bytes()")


def test_conservative_shadow_terminal_blocks():
    assert "blockers.append(_msr3.REASON)" in _src("research/conservative_shadow_terminal.py")


def test_entry_baseline_replay_reports_invalid_sources():
    src = _src("research/entry_baseline_replay.py")
    assert '"status": "INVALID", "rule": _msr3.RULE' in src
    assert '_msr3.rows("v3/ledgers/market_segment.jsonl")' in src


def test_research_v3_report_marks_invalid():
    src = _src("research/research_v3_report.py")
    assert 'market_segment_rows = _msr3.rows(' in src and '"market_segment_input": _msr3.invalid(' in src


def test_best_policy_research_marks_invalid():
    src = _src("research/best_policy_research.py")
    assert '"market_segments": 0,  # R3' in src and '_msr3.invalid(' in src


def test_research_dashboard_exports_no_segments():
    src = _src("research/research_dashboard.py")
    assert '"canonical_v3_market_segments": False' in src
    assert "count(c.market_segments)" not in src
    assert '{"market_segments": _msr3.REASON}' in src


# --- repo-wide grep: 0 live reads outside the gate --------------------------

# Writer (retired behind R17 flags), store, retention/reset/epoch modules.
NON_INPUT = {
    "research_v3_bridge.py", "research_v3_store.py", "r5_retention.py", "r5_keep.py", "bot.py",
    "data_epoch.py", "epoch_boundary_rotation.py", "nonaccum_plans.py", "research_v3_contract.py",
    "research/research_reset_inventory.py", "ledger_stamp_monitor.py", "ledger_write_stamp.py",
    "data_retention_policy.py", "qualification_horizon_index.py", "lifecycle_index_recovery.py",
    "market_segment_r3.py",
}
RAW_READ = re.compile(
    r'(?<!_msr3\.rows\()"v3/(?:recovery_)?ledgers/market_segment\.jsonl"'
    r'|ledger_path\("market_segment"\)'
    r'|_read_jsonl\([^)]*market_segment'
)


def _live_reads():
    hits = []
    for path in sorted(HERE.rglob("*.py")):
        rel = path.relative_to(HERE).as_posix()
        if path.name.startswith("test_") or rel.startswith("tests/") or rel in NON_INPUT:
            continue
        for n, line in enumerate(path.read_text(errors="ignore").splitlines(), 1):
            if "# R3-NON-INPUT:" in line:
                continue
            code = line.split("#", 1)[0]
            if RAW_READ.search(code) and "_msr3." not in code:
                hits.append(f"{rel}:{n}:{line.strip()}")
    return hits


def test_zero_live_market_segment_reads():
    assert _live_reads() == []


def test_every_consumer_imports_gate():
    consumers = [
        "research/research_v3_report.py", "research/policy_evidence_evaluator.py",
        "research/policy_evidence_bindings.py", "research/entry_baseline_replay.py",
        "research/conservative_shadow_report.py", "research/conservative_shadow_terminal.py",
        "research/baseline_execution_context.py", "research/shared_context_coverage.py",
        "lifecycle_completion_reconciler.py", "research_v3_future_paths.py",
        "recover_archived_future_paths.py", "research/v3_policy_report_adapter.py",
        "research_v3_candidates.py", "lifecycle_bundles.py", "research/research_dashboard.py",
        "research/evidence_coverage_triage.py", "research/best_policy_research.py",
    ]
    for rel in consumers:
        assert "import market_segment_r3 as _msr3" in _src(rel), rel
