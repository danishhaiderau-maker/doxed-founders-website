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


# --- repo-wide scan: 0 live market_segment reads outside the gate -----------
# HM #873 R2 (b): AST scan (not a line grep). It catches literal ledger/segment
# paths in any form (``"v3/ledgers/market_segment.jsonl"``, ``root / "market_segments"``,
# ``os.path.join(.., "market_segments")``, ``Path("v3/market_segments")``, f-strings)
# and ``f"{name}.jsonl"`` / ledger loops whose iterable includes ``"market_segment"``.
# A hit is acceptable only when
#   * it is an argument of an ``_msr3.*`` call (the gate itself), or
#   * an ``_msr3.`` gate appears earlier in the same function, or the loop filters
#     ``market_segment`` out, or
#   * it is the store module's own ``segment_dir`` definition, or
#   * the line is annotated ``# R3-NON-INPUT: <reason>``, performs no read itself and
#     is one of the pinned NON_INPUT_SITES below (retention/reset/rotation/monitor
#     housekeeping, a report label). Adding a site needs a reviewed edit here.
# There is no whole-file exemption.

import ast  # noqa: E402

_SEG_PATH = re.compile(r"(?:^|/)(?:recovery_)?ledgers/market_segment\.jsonl$|(?:^|/)market_segments/|/market_segments$|^market_segment\.jsonl$")
_READ_CALL = re.compile(r"\b(?:open|read_text|read_bytes|_read_jsonl|read_jsonl|glob|rglob|iterdir|load|loads|scandir|listdir)\s*\(")
NON_INPUT_SITES = {
    ("data_epoch.py", 'EPOCH_AUDIT_SKIP_DIRS = ("v3/receipts", "v3/market_segments", "v3/lifecycle_bundle_index",'),
    ("epoch_boundary_rotation.py", "for ledger in V3_LEDGERS:"),
    ("ledger_stamp_monitor.py", "for name in LEDGERS:"),
    ("nonaccum_plans.py", "for led in LEDGERS:"),
    ("r5_retention.py", 'base = root / "v3" / "market_segments"'),
    ("research_reset_inventory.py", 'if (len(parts) == 4 and parts[:2] == ("v3", "market_segments")'),
    ("recover_archived_future_paths.py", 'RECOVERY_LEDGER_RELATIVE = Path("v3/recovery_ledgers/market_segment.jsonl")'),
}
def _parents(tree):
    parent = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parent[child] = node
    return parent


def _str_consts(node):
    return {n.value for n in ast.walk(node) if isinstance(n, ast.Constant) and isinstance(n.value, str)}


def _segment_sites(tree):
    """Yield (lineno, node, kind) for every syntactic market_segment path or ledger loop."""
    parent = _parents(tree)
    consts = {}
    for st in tree.body:
        if isinstance(st, ast.Assign) and isinstance(st.value, (ast.Tuple, ast.List, ast.Set)):
            for t in st.targets:
                if isinstance(t, ast.Name):
                    consts[t.id] = _str_consts(st.value)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            value = node.value
            up = parent.get(node)
            if isinstance(up, ast.JoinedStr):
                value = "".join(v.value if isinstance(v, ast.Constant) else "{}" for v in up.values)
            path_ctx = isinstance(up, ast.BinOp) and isinstance(up.op, ast.Div) or (
                isinstance(up, ast.Call) and ast.unparse(up.func).split(".")[-1] in {"join", "Path", "PurePath", "open", "ledger_path"})
            if _SEG_PATH.search(value) or (path_ctx and value in {"market_segments", "market_segment", "market_segment.jsonl"}):
                yield node.lineno, node, "path"
        if isinstance(node, (ast.For, ast.comprehension)) and isinstance(node.target, ast.Name):
            it = node.iter
            names = _str_consts(it) | (consts.get(it.id, set()) if isinstance(it, ast.Name) else set())
            if "market_segment" not in names:
                continue
            var = node.target.id
            tests = list(getattr(node, "ifs", []))
            holder = parent.get(node)
            scope = node.body if isinstance(node, ast.For) else ([holder] if holder is not None else [])
            for sub in scope:
                tests += [n.test for n in ast.walk(sub) if isinstance(n, (ast.If, ast.IfExp))]
            if any(var in ast.unparse(t) and "market_segment" in ast.unparse(t) for t in tests):
                continue  # market_segment explicitly routed/filtered
            yield it.lineno, node, "ledger-loop"


def _enclosing(tree, lineno):
    best = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.lineno <= lineno <= node.end_lineno:
            if best is None or node.lineno >= best.lineno:
                best = node
    return best


def _accepted(rel, tree, lines, lineno, node, parent):
    line = lines[lineno - 1]
    up = parent.get(node)
    while up is not None and not isinstance(up, ast.stmt):
        if isinstance(up, ast.Call) and ast.unparse(up.func).startswith("_msr3."):
            return True
        up = parent.get(up)
    func = _enclosing(tree, lineno)
    if func is not None and any("_msr3." in l.split("#", 1)[0] for l in lines[func.lineno - 1:lineno]):
        return True
    if rel == "research_v3_store.py" and re.fullmatch(r"(?:self|instance)\.segment_dir = (?:self\.root|resolved) / \"v3\" / \"market_segments\"", line.strip()):
        return True
    note = re.search(r"# R3-NON-INPUT: (.{10,})", line)
    if note and not _READ_CALL.search(line.split("#", 1)[0]):
        return (rel, line.split("#", 1)[0].strip()) in NON_INPUT_SITES
    return False


def _live_reads():
    hits = []
    for path in sorted(HERE.rglob("*.py")):
        rel = path.relative_to(HERE).as_posix()
        if path.name.startswith("test_") or rel.startswith("tests/") or rel == "market_segment_r3.py":
            continue
        src = path.read_text(errors="ignore")
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        lines = src.splitlines()
        parent = _parents(tree)
        for lineno, node, kind in _segment_sites(tree):
            if not _accepted(rel, tree, lines, lineno, node, parent):
                hits.append(f"{rel}:{lineno}:{kind}:{lines[lineno - 1].strip()}")
    return sorted(set(hits))


def test_zero_live_market_segment_reads():
    assert _live_reads() == []


def test_non_input_sites_are_pinned_and_present():
    present = set()
    for rel, code in NON_INPUT_SITES:
        lines = (HERE / rel).read_text().splitlines()
        if any(l.split("#", 1)[0].strip() == code and "# R3-NON-INPUT:" in l for l in lines):
            present.add((rel, code))
    assert present == NON_INPUT_SITES, sorted(NON_INPUT_SITES - present)


@pytest.mark.parametrize("snippet", [
    'def f(root):\n    return open(root / "v3" / "ledgers" / "market_segment.jsonl")\n',
    'def f(root):\n    return list((root / "market_segments").glob("*/*.json"))\n',
    'import os\ndef f(root):\n    return os.listdir(os.path.join(root, "v3", "market_segments"))\n',
    'L = ("decision", "market_segment")\ndef f(root):\n    return [open(root / f"{n}.jsonl") for n in L]\n',
    'def f(root):\n    for name in ("lifecycle", "market_segment"):\n        open(root / f"{name}.jsonl")\n',
    'def f(root):\n    return open(f"{root}/v3/market_segments/x.json")\n',
    'def f(root):\n    return open(root / "market_segments" / "a")  # R3-NON-INPUT: claims to be housekeeping\n',
])
def test_scan_catches_live_read_forms(snippet):
    tree = ast.parse(snippet)
    lines, parent = snippet.splitlines(), _parents(tree)
    found = [l for l, n, _ in _segment_sites(tree) if not _accepted("x.py", tree, lines, l, n, parent)]
    assert found, snippet


@pytest.mark.parametrize("snippet", [
    'import market_segment_r3 as _msr3\ndef f():\n    return _msr3.rows("v3/ledgers/market_segment.jsonl")\n',
    'import market_segment_r3 as _msr3\ndef f(root):\n    if not _msr3.INPUT_ALLOWED:\n        return []\n    return list((root / "market_segments").glob("*"))\n',
    'L = ("decision", "market_segment")\ndef f(root):\n    return [open(root / f"{n}.jsonl") for n in L if n != "market_segment"]\n',
])
def test_scan_accepts_gated_forms(snippet):
    tree = ast.parse(snippet)
    lines, parent = snippet.splitlines(), _parents(tree)
    assert not [l for l, n, _ in _segment_sites(tree) if not _accepted("x.py", tree, lines, l, n, parent)]


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
