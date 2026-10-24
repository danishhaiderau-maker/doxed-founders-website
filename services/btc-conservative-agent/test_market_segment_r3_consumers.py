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

    real_read_text = Path.read_text
    real_path_open = Path.open

    def fake_read_text(self, *a, **k):
        guard(self)
        return real_read_text(self, *a, **k)

    def fake_path_open(self, mode="r", *a, **k):
        if not set(str(mode)) & set("wax+"):  # writes set up the poison; reads fail
            guard(self)
        return real_path_open(self, mode, *a, **k)

    monkeypatch.setattr(builtins, "open", fake_open)
    monkeypatch.setattr(Path, "read_bytes", fake_read_bytes)
    monkeypatch.setattr(Path, "read_text", fake_read_text)
    monkeypatch.setattr(Path, "open", fake_path_open)


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


_REAL_READ_TEXT = Path.read_text


def _src(rel: str) -> str:
    return _REAL_READ_TEXT(HERE / rel)


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
# HM #873 R2 (b) + R3 (B1): AST scan (not a line grep). Sites:
#   * literal ledger/segment paths in any form: ``"v3/ledgers/market_segment.jsonl"``,
#     ``root / "market_segments"``, ``os.path.join(.., "market_segments")``, ``Path(..)``,
#     f-strings, including module-level string constants (``SEG = "market_segment"``
#     then ``f"{SEG}.jsonl"`` / ``root / SEG``);
#   * loops whose iterable includes ``"market_segment"``: literal tuples, module
#     constants, and ledger lists imported from another module (``LEDGER_NAMES``,
#     ``V3_LEDGERS``), resolved by parsing that module.
# A site is accepted only when
#   * it is an argument of ``_msr3.rows`` / ``_msr3.require_envelope`` / ``_msr3.invalid``
#     (the gate itself), or
#   * a REAL gate precedes it in the same function: ``if not _msr3.INPUT_ALLOWED:``
#     followed by return/continue/raise, or ``x = x if _msr3.INPUT_ALLOWED else []``,
#     or the site sits in the true branch of ``... if _msr3.INPUT_ALLOWED else ...``.
#     A mere mention (``_msr3.REASON``, ``_msr3.invalid(..)``) is NOT a gate; or
#   * a loop skips ``market_segment`` (``if v == "market_segment" ...: continue`` or a
#     comprehension ``if v != "market_segment"``), or iterates a mapping slot that was
#     filled only from ``_msr3.rows(..)``; or
#   * it is the store module's own ``segment_dir`` definition, or
#   * the line is annotated ``# R3-NON-INPUT: <reason>``, is one of the pinned
#     NON_INPUT_SITES below, and performs no read call itself.
# There is no whole-file exemption.

import ast  # noqa: E402
import warnings  # noqa: E402

_SEG_PATH = re.compile(r"(?:^|/)(?:recovery_)?ledgers/market_segment\.jsonl$|(?:^|/)market_segments/|/market_segments$|^market_segment\.jsonl$")
_SEG_WORDS = {"market_segments", "market_segment", "market_segment.jsonl"}
_READ_CALL = re.compile(r"\b(?:open|read_text|read_bytes|_read_jsonl|read_jsonl|glob|rglob|iterdir|load|loads|scandir|listdir)\s*\(")
_GATE_CALLS = {"_msr3.rows", "_msr3.require_envelope", "_msr3.invalid"}
NON_INPUT_SITES = {
    ("data_epoch.py", 'EPOCH_AUDIT_SKIP_DIRS = ("v3/receipts", "v3/market_segments", "v3/lifecycle_bundle_index",'),
    ("epoch_boundary_rotation.py", "for ledger in V3_LEDGERS:"),
    ("ledger_stamp_monitor.py", "for name in LEDGERS:"),
    ("nonaccum_plans.py", "for led in LEDGERS:"),
    ("r5_retention.py", 'base = root / "v3" / "market_segments"'),
    ("research_reset_inventory.py", 'if (len(parts) == 4 and parts[:2] == ("v3", "market_segments")'),
    ("recover_archived_future_paths.py", 'RECOVERY_LEDGER_RELATIVE = Path("v3/recovery_ledgers/market_segment.jsonl")'),
    # HM R3 B1: imported ledger lists (LEDGER_NAMES / V3_LEDGERS) now resolved; these
    # loops are store integrity / rotation / reset housekeeping, never evidence.
    ("bot.py", "for ledger in LEDGER_NAMES:"),
    ("clean_epoch_reset_plan.py", "for ledger in V3_LEDGERS:"),
    ("production_rotation_orchestrator.py", "for ledger in LEDGER_NAMES:"),
    ("research_reset_inventory.py", 'if any(re.fullmatch(re.escape(n) + r"\\.jsonl(?:\\.\\d+)?", parts[-1]) for n in LEDGER_NAMES):'),
    ("research_reset_recovery_audit.py", 'if path.name not in {n + ".json" for n in LEDGER_NAMES}:'),
    ("research_v3_store.py", "for name in LEDGER_NAMES:"),
    ("research_v3_store.py", "for ledger in LEDGER_NAMES:"),
    ("transactional_receipt_store.py", "for ledger in LEDGER_NAMES:"),
}


def _parse(src):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SyntaxWarning)
        return ast.parse(src)


def _parents(tree):
    parent = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parent[child] = node
    return parent


def _str_consts(node):
    return {n.value for n in ast.walk(node) if isinstance(n, ast.Constant) and isinstance(n.value, str)}


def _module_constants(tree):
    """Module-level ``NAME = "str"`` and ``NAME = (..str..)`` constants."""
    strs, seqs = {}, {}
    for st in tree.body:
        targets, value = [], None
        if isinstance(st, ast.Assign):
            targets, value = st.targets, st.value
        elif isinstance(st, ast.AnnAssign) and st.value is not None:
            targets, value = [st.target], st.value
        for t in targets:
            if not isinstance(t, ast.Name):
                continue
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                strs[t.id] = value.value
            elif isinstance(value, (ast.Tuple, ast.List, ast.Set, ast.Call)):
                seqs[t.id] = _str_consts(value)
    return strs, seqs


_MODULE_CACHE: dict = {}


def _module_seqs(modname, modules):
    if modname in modules:
        return _module_constants(_parse(modules[modname]))[1]
    if modname not in _MODULE_CACHE:
        rel = modname.replace(".", "/") + ".py"
        path = HERE / rel
        _MODULE_CACHE[modname] = (_module_constants(_parse(path.read_text(errors="ignore")))[1]
                                  if path.is_file() else {})
    return _MODULE_CACHE[modname]


def _seq_env(tree, modules):
    """Name / attribute -> string set, including imported ledger lists."""
    strs, seqs = _module_constants(tree)
    env = dict(seqs)
    for st in ast.walk(tree):
        if isinstance(st, ast.ImportFrom) and st.module and st.level == 0:
            remote = _module_seqs(st.module, modules)
            for alias in st.names:
                if alias.name in remote:
                    env[alias.asname or alias.name] = remote[alias.name]
        elif isinstance(st, ast.Import):
            for alias in st.names:
                remote = _module_seqs(alias.name, modules)
                for key, value in remote.items():
                    env[f"{alias.asname or alias.name}.{key}"] = value
    return strs, env


def _resolved_text(node, up, strs):
    """String value of a literal, f-string or Name constant (``{}`` for unknown parts)."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        if isinstance(up, ast.JoinedStr):
            return None  # reported via the JoinedStr itself
        return node.value
    if isinstance(node, ast.JoinedStr):
        out = []
        for v in node.values:
            if isinstance(v, ast.Constant):
                out.append(str(v.value))
            elif isinstance(v, ast.FormattedValue) and isinstance(v.value, ast.Name) and v.value.id in strs:
                out.append(strs[v.value.id])
            else:
                out.append("{}")
        return "".join(out)
    if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id in strs:
        return strs[node.id]
    return None


def _path_ctx(up):
    return (isinstance(up, ast.BinOp) and isinstance(up.op, ast.Div)) or (
        isinstance(up, ast.Call) and ast.unparse(up.func).split(".")[-1]
        in {"join", "Path", "PurePath", "open", "ledger_path", "joinpath"})


def _segment_sites(tree, modules=None):
    """Yield (lineno, node, kind) for every syntactic market_segment path or ledger loop."""
    modules = modules or {}
    parent = _parents(tree)
    strs, env = _seq_env(tree, modules)
    for node in ast.walk(tree):
        up = parent.get(node)
        text = _resolved_text(node, up, strs)
        if text is not None and not (isinstance(up, (ast.Assign, ast.AnnAssign)) and up in tree.body
                                     and isinstance(node, ast.Constant)):
            jtext = text.replace("{}", "x")
            if _SEG_PATH.search(jtext) or (_path_ctx(up) and text in _SEG_WORDS) or (
                    isinstance(node, ast.JoinedStr) and re.search(r"(?:^|/)market_segments?(?:\.jsonl|/|$)", text)
                    and any(isinstance(v, ast.FormattedValue) and isinstance(v.value, ast.Name)
                            and v.value.id in strs for v in node.values)):
                yield node.lineno, node, "path"
        if isinstance(node, (ast.For, ast.comprehension)) and isinstance(node.target, ast.Name):
            it = node.iter
            names = _str_consts(it) | env.get(ast.unparse(it), set())
            if "market_segment" not in names:
                continue
            yield it.lineno, node, "ledger-loop"


def _enclosing(tree, lineno):
    best = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.lineno <= lineno <= node.end_lineno:
            if best is None or node.lineno >= best.lineno:
                best = node
    return best


def _is_allowed_flag(expr):
    return ast.unparse(expr) == "_msr3.INPUT_ALLOWED"


def _is_closed_guard(stmt):
    """``if not _msr3.INPUT_ALLOWED: return/continue/raise``."""
    return (isinstance(stmt, ast.If) and isinstance(stmt.test, ast.UnaryOp) and isinstance(stmt.test.op, ast.Not)
            and _is_allowed_flag(stmt.test.operand) and stmt.body
            and isinstance(stmt.body[0], (ast.Return, ast.Continue, ast.Raise)))


def _is_closed_assign(stmt):
    """``x = x if _msr3.INPUT_ALLOWED else []`` (or ``()`` / ``None``)."""
    return (isinstance(stmt, ast.Assign) and isinstance(stmt.value, ast.IfExp) and _is_allowed_flag(stmt.value.test)
            and ast.unparse(stmt.value.orelse) in {"[]", "()", "None", "{}", "set()"})


def _real_gate_before(func, lineno, node, parent):
    if func is None:
        return False
    for stmt in ast.walk(func):
        if getattr(stmt, "lineno", lineno) >= lineno:
            continue
        if _is_closed_guard(stmt) or _is_closed_assign(stmt):
            return True
    cur, child = parent.get(node), node
    while cur is not None and cur is not func:
        if isinstance(cur, (ast.IfExp, ast.If)) and _is_allowed_flag(cur.test) and (
                child is cur.body or child in (cur.body if isinstance(cur.body, list) else [cur.body])):
            return True
        cur, child = parent.get(cur), cur
    return False


def _loop_skips_segment(loop, parent):
    var = loop.target.id
    probe = re.compile(rf"\b{re.escape(var)}\b\s*(?:==|!=)\s*['\"]market_segment['\"]|['\"]market_segment['\"]\s*(?:==|!=)\s*\b{re.escape(var)}\b")
    if isinstance(loop, ast.comprehension):
        if any(isinstance(c, ast.Compare) and isinstance(c.ops[0], ast.NotEq) and probe.search(ast.unparse(c))
               for c in loop.ifs):
            return True
        holder = parent.get(loop)
        value = getattr(holder, "value", None) or getattr(holder, "elt", None)
        # dict/list comprehension routing market_segment to the gate: ``_msr3.rows(..) if v == "market_segment" else ..``
        return any(isinstance(n, ast.IfExp) and probe.search(ast.unparse(n.test))
                   and ast.unparse(n.body).startswith("_msr3.rows(") for n in ast.walk(value or holder))
    first = loop.body[0] if loop.body else None
    return (isinstance(first, ast.If) and probe.search(ast.unparse(first.test))
            and isinstance(first.body[0], (ast.Continue, ast.Break)))


def _loop_over_gated_slot(loop, func):
    """``for r in X["market_segment"]`` where every write to that slot is ``_msr3.rows(..)``."""
    slot = ast.unparse(loop.iter)
    if not re.fullmatch(r"\w+\[['\"]market_segment['\"]\]", slot) or func is None:
        return False
    base = slot.split("[", 1)[0]
    writes, gated = 0, 0
    for n in ast.walk(func):
        if isinstance(n, ast.Assign) and any(ast.unparse(t) == slot for t in n.targets):
            writes += 1
            calls = [c for c in ast.walk(n.value) if isinstance(c, ast.Call)]
            gated += bool(calls) and all(ast.unparse(c.func) == "_msr3.rows" for c in calls
                                         if "market_segment" in ast.unparse(c) or ast.unparse(c.func).startswith("_msr3"))
        if isinstance(n, ast.Assign) and any(ast.unparse(t) == base for t in n.targets) and isinstance(n.value, ast.DictComp):
            writes += 1
            gated += any(_loop_skips_segment(g, _parents(n)) for g in n.value.generators)
    return writes > 0 and writes == gated


def _accepted(rel, tree, lines, lineno, node, parent):
    line = lines[lineno - 1]
    up = parent.get(node)
    while up is not None and not isinstance(up, ast.stmt):
        if isinstance(up, ast.Call) and ast.unparse(up.func) in _GATE_CALLS:
            return True
        up = parent.get(up)
    func = _enclosing(tree, lineno)
    if isinstance(node, (ast.For, ast.comprehension)):
        if _loop_skips_segment(node, parent) or _loop_over_gated_slot(node, func):
            return True
    if _real_gate_before(func, lineno, node, parent):
        return True
    if rel == "research_v3_store.py" and re.fullmatch(
            r"(?:self|instance)\.segment_dir = (?:self\.root|resolved) / \"v3\" / \"market_segments\"", line.strip()):
        return True
    if re.search(r"# R3-NON-INPUT: (.{10,})", line) and not _READ_CALL.search(line.split("#", 1)[0]):
        return (rel, line.split("#", 1)[0].strip()) in NON_INPUT_SITES
    return False


def _scan_source(src, rel="x.py", modules=None):
    tree = _parse(src)
    lines, parent = src.splitlines(), _parents(tree)
    return sorted({f"{rel}:{lineno}:{kind}:{lines[lineno - 1].strip()}"
                   for lineno, node, kind in _segment_sites(tree, modules)
                   if not _accepted(rel, tree, lines, lineno, node, parent)})


def _live_reads():
    hits = []
    for path in sorted(HERE.rglob("*.py")):
        rel = path.relative_to(HERE).as_posix()
        if path.name.startswith("test_") or rel.startswith("tests/") or rel == "market_segment_r3.py":
            continue
        try:
            hits += _scan_source(path.read_text(errors="ignore"), rel)
        except SyntaxError:
            continue
    return sorted(hits)


def test_zero_live_market_segment_reads():
    assert _live_reads() == []


def test_non_input_sites_are_pinned_and_present():
    present = set()
    for rel, code in NON_INPUT_SITES:
        lines = (HERE / rel).read_text().splitlines()
        if any(l.split("#", 1)[0].strip() == code and "# R3-NON-INPUT:" in l for l in lines):
            present.add((rel, code))
    assert present == NON_INPUT_SITES, sorted(NON_INPUT_SITES - present)


_LEDGER_MOD = {"fake_contract": 'LEDGER_NAMES = ("decision", "market_segment", "lifecycle")\n'}
_GATE = "import market_segment_r3 as _msr3\n"


@pytest.mark.parametrize("snippet", [
    'def f(root):\n    return open(root / "v3" / "ledgers" / "market_segment.jsonl")\n',
    'def f(root):\n    return list((root / "market_segments").glob("*/*.json"))\n',
    'import os\ndef f(root):\n    return os.listdir(os.path.join(root, "v3", "market_segments"))\n',
    'L = ("decision", "market_segment")\ndef f(root):\n    return [open(root / f"{n}.jsonl") for n in L]\n',
    'def f(root):\n    for name in ("lifecycle", "market_segment"):\n        open(root / f"{name}.jsonl")\n',
    'def f(root):\n    return open(f"{root}/v3/market_segments/x.json")\n',
    'def f(root):\n    return open(root / "market_segments" / "a")  # R3-NON-INPUT: claims to be housekeeping\n',
    # HM R3 M14: a mention of _msr3.REASON / invalid() is not a gate
    _GATE + 'def f(root):\n    _ = _msr3.REASON\n    return open(root / "v3" / "ledgers" / "market_segment.jsonl")\n',
    _GATE + 'def f(root):\n    marker = _msr3.invalid("x")\n    return list((root / "market_segments").glob("*"))\n',
    _GATE + 'def f(root):\n    _msr3.rows("v3/ledgers/market_segment.jsonl")\n    return open(root / "market_segments" / "a")\n',
    _GATE + 'def f(root):\n    allowed = _msr3.INPUT_ALLOWED\n    return open(root / "market_segments" / "a")\n',
    # HM R3 M7: imported ledger list
    'from fake_contract import LEDGER_NAMES\ndef f(d):\n    for ledger in LEDGER_NAMES:\n        open(d / f"{ledger}.jsonl")\n',
    'import fake_contract\ndef f(d):\n    for ledger in fake_contract.LEDGER_NAMES:\n        open(d / f"{ledger}.jsonl")\n',
    'from fake_contract import LEDGER_NAMES\ndef f(d):\n    for ledger in LEDGER_NAMES:\n        if ledger == "market_segment":\n            print(ledger)\n        open(d / f"{ledger}.jsonl")\n',
    # HM R3 M16: module string constants in f-strings / path joins
    'SEG = "market_segment"\ndef f(d):\n    return open(d / f"{SEG}.jsonl")\n',
    'SEG = "market_segments"\ndef f(root):\n    return list((root / SEG).glob("*"))\n',
    'SEG = "market_segment"\ndef f(d):\n    return open(f"{d}/v3/ledgers/{SEG}.jsonl")\n',
])
def test_scan_catches_live_read_forms(snippet):
    assert _scan_source(snippet, modules=_LEDGER_MOD), snippet


@pytest.mark.parametrize("snippet", [
    _GATE + 'def f():\n    return _msr3.rows("v3/ledgers/market_segment.jsonl")\n',
    _GATE + 'def f(root):\n    if not _msr3.INPUT_ALLOWED:\n        return []\n    return list((root / "market_segments").glob("*"))\n',
    _GATE + 'def f(root, segs):\n    segs = segs if _msr3.INPUT_ALLOWED else []\n    for s in segs:\n        open(root / "market_segments" / s)\n',
    _GATE + 'def f(root):\n    return list((root / "market_segments").glob("*")) if _msr3.INPUT_ALLOWED else []\n',
    'L = ("decision", "market_segment")\ndef f(root):\n    return [open(root / f"{n}.jsonl") for n in L if n != "market_segment"]\n',
    _GATE + 'from fake_contract import LEDGER_NAMES\ndef f(d):\n    for ledger in LEDGER_NAMES:\n        if ledger == "market_segment" and not _msr3.INPUT_ALLOWED:\n            continue\n        open(d / f"{ledger}.jsonl")\n',
    _GATE + 'def f(root):\n    ledgers = {"market_segment": []}\n    ledgers["market_segment"] = _msr3.rows("v3/ledgers/market_segment.jsonl")\n    for row in ledgers["market_segment"]:\n        print(row)\n',
])
def test_scan_accepts_gated_forms(snippet):
    assert not _scan_source(snippet, modules=_LEDGER_MOD), snippet


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


# --- HM R3 B2: each production gate has a behavioural test that fails if removed ---

_SEG_ID = {"collection_epoch_id": "epoch-1", "epoch_id": "epoch-1", "episode_id": "episode-1",
           "policy_signature": "policy-1", "research_lane": "CONTINUOUS"}


def test_lifecycle_bundles_collect_skips_segment_ledger(tmp_path, no_segment_reads):
    """Gate: lifecycle_bundles.collect_lifecycle_rows `if ledger == "market_segment" ... continue`."""
    import json as _json
    from lifecycle_bundles import collect_lifecycle_rows
    v3 = _poison(tmp_path)
    (v3 / "ledgers" / "market_segment.jsonl").write_bytes(
        (_json.dumps({"ledger": "market_segment", **_SEG_ID}) + "\n").encode())
    grouped = collect_lifecycle_rows(tmp_path)
    assert not any(r.get("ledger") == "market_segment" for rows in grouped.values() for r in rows)


def test_lifecycle_completion_reconciler_reports_dropped_segment():
    """Gate: evaluate_lifecycle_completion adds _msr3.REASON for a dropped segment row."""
    from lifecycle_bundles import LifecycleKey
    from lifecycle_completion_reconciler import evaluate_lifecycle_completion
    key = LifecycleKey("epoch-1", "episode-1", "policy-1", "CONTINUOUS")
    row = {"ledger": "market_segment", "context_role": "FULL_LIFECYCLE", **_SEG_ID}
    out = evaluate_lifecycle_completion(key, [row], now=10_000.0)
    assert REASON in out["blockers"]
    assert out["ready"] is False
    assert out["market_segment_input"]["reason"] == REASON
    clean = evaluate_lifecycle_completion(key, [], now=10_000.0)
    assert REASON not in clean["blockers"] and "market_segment_input" not in clean


def test_candidates_load_segment_ignores_valid_envelope_with_rows(tmp_path, no_segment_reads):
    """Gate: research_v3_candidates._load_segment `if not _msr3.INPUT_ALLOWED: return []`."""
    from research_v3_candidates import _load_segment
    rel = "v3/market_segments/ab/" + "ab" * 32 + ".json"
    target = tmp_path / rel
    target.parent.mkdir(parents=True)
    target.write_bytes(b'{"schema":"market_segment_v3","rows":[{"ts":1,"mid":100.0},{"ts":2,"mid":101.0}]}')
    assert _load_segment(tmp_path, {"relative_path": rel, "sha256": "ab" * 32}) == []
