"""Mechanical proof for the R3 legacy xfail list (RK ruling on #873).

1. xfail is strict and scoped (HM #873 R2 a1): XPASS fails; only a failure that
   IS ``MarketSegmentInputInvalid`` or carries ``INVALID_R3_MARKET_SEGMENT_INPUT``
   after a real gate hit xfails. A bare AssertionError/KeyError/StopIteration, or a
   test that merely reads ``_msr3.REASON``, fails (unit + real pytest subprocess).
2. No forbidden-category test is listed: whole-word scan of the node id AND the
   test body (a2), topics include funding/protection/emergency.
3. The list is shrink-only (b): every entry must be in the committed PINNED set
   (currently empty), count <= CEILING (0), a subset of the base-branch list when
   CI provides R3_BASE_LIST, and every entry must resolve to an existing test.
"""
from __future__ import annotations

import ast
import os
import re
import subprocess
import sys
import textwrap

import pytest

import market_segment_r3 as msr3
import r3_legacy_xfail as plugin

HERE = os.path.dirname(os.path.abspath(__file__))
LIST = os.path.join(HERE, "ci", "r3_legacy_segment_tests.txt")
CEILING = 0  # shrink-only: HM #873 R2 emptied the list; never raise it.
# Committed base (b): the list may only ever be a subset of this set.
PINNED_ENTRIES: frozenset[str] = frozenset()
FORBIDDEN = re.compile(
    r"(?<![A-Za-z0-9])(?:safety|arm|armed|arming|guard|guardian|fence|fences|fill|fills|filled|quote|quotes"
    r"|trigger|triggers|stop|stops|delay|delays|exit|exits|r17|countab\w*|epoch|epochs|kill|live"
    r"|funding|protection|protect|emergency)(?![A-Za-z0-9])",
    re.IGNORECASE)


def _test_source(entry: str) -> str | None:
    """Source of the listed test function, or None when the entry is stale."""
    rel, _, name = entry.partition("::")
    name = name.split("[", 1)[0]
    path = os.path.join(HERE, rel)
    if not name or not os.path.isfile(path):
        return None
    src = open(path, encoding="utf-8").read()
    parts = name.split("::")
    scope = ast.parse(src).body
    node = None
    for part in parts:
        node = next((n for n in scope if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                     and n.name == part), None)
        if node is None:
            return None
        scope = getattr(node, "body", [])
    return ast.get_source_segment(src, node)


def _banned_hits(entry: str, body: str) -> list[str]:
    text = entry.replace("_", " ") + "\n" + body.replace("_", " ")
    return sorted({m.group(0).lower() for m in FORBIDDEN.finditer(text)})


class _Exc:
    def __init__(self, value):
        self.value = value


def test_classify_is_scoped():
    assert plugin.classify(None, 3) == "xpass"
    assert plugin.classify(_Exc(msr3.MarketSegmentInputInvalid("x")), 0) == "fail"
    for exc in (AssertionError(), KeyError("k"), StopIteration(), TypeError(), ImportError()):
        assert plugin.classify(_Exc(exc), 2) == "fail", exc
        assert plugin.classify(_Exc(exc), 2, "assert 'FILLED' == 'X'") == "fail", exc
    assert plugin.classify(_Exc(msr3.MarketSegmentInputInvalid("x")), 1) == "xfail"
    assert plugin.classify(_Exc(AssertionError()), 1, "E  INVALID_R3_MARKET_SEGMENT_INPUT") == "xfail"
    assert plugin.classify(_Exc(AssertionError("INVALID_R3_MARKET_SEGMENT_INPUT")), 1) == "xfail"


def test_reading_reason_is_not_a_gate_hit():
    before = msr3.HIT_COUNT
    assert msr3.REASON == "INVALID_R3_MARKET_SEGMENT_INPUT"
    assert msr3.HIT_COUNT == before


def test_plugin_end_to_end_in_real_pytest(tmp_path):
    (tmp_path / "test_probe.py").write_text(textwrap.dedent("""
        import market_segment_r3 as m
        def test_gate_hit():
            m._hit("probe"); raise m.MarketSegmentInputInvalid("probe")
        def test_gate_hit_r3_text():
            m._hit("probe"); assert "x" == m.REASON
        def test_unrelated_assert():
            assert False
        def test_hit_then_bare_assert():
            m._hit("probe"); assert False
        def test_hit_then_keyerror():
            m._hit("probe"); {}["k"]
        def test_reason_read_only():
            assert m.REASON is None
        def test_unrelated_type():
            m._hit("probe"); raise TypeError("not R3")
        def test_passes():
            m._hit("probe")
        def test_not_listed():
            pass
    """))
    listed = ["test_gate_hit", "test_gate_hit_r3_text", "test_unrelated_assert", "test_hit_then_bare_assert",
              "test_hit_then_keyerror", "test_reason_read_only", "test_unrelated_type", "test_passes"]
    (tmp_path / "list.txt").write_text("".join(f"test_probe.py::{n}\n" for n in listed))
    env = dict(os.environ, R3_LEGACY_LIST=str(tmp_path / "list.txt"),
               PYTHONPATH=HERE + os.pathsep + os.environ.get("PYTHONPATH", ""))
    env.pop("R3_XPASS_OUT", None)
    run = subprocess.run([sys.executable, "-m", "pytest", "-p", "r3_legacy_xfail", "-p", "no:cacheprovider",
                          "-rA", "-q", "--rootdir", str(tmp_path), "--noconftest", str(tmp_path / "test_probe.py")],
                         cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120)
    out = run.stdout
    assert run.returncode != 0, out
    for name in ("test_gate_hit", "test_gate_hit_r3_text"):
        assert re.search(rf"XFAIL .*{name}\b", out), out
    for name in ("test_unrelated_assert", "test_hit_then_bare_assert", "test_hit_then_keyerror",
                 "test_reason_read_only", "test_unrelated_type", "test_passes"):
        assert re.search(rf"FAILED .*{name}\b", out), out
    assert re.search(r"PASSED .*test_not_listed", out), out
    assert "6 failed" in out and "1 passed" in out and "2 xfailed" in out, out


def test_no_forbidden_category_on_list():
    bad = {}
    for entry in sorted(plugin.load_list(LIST)):
        hits = _banned_hits(entry, _test_source(entry) or "")
        if hits:
            bad[entry] = hits
    assert not bad, f"forbidden-category tests must be rewritten, not listed: {bad}"


def test_banned_scan_is_whole_word_and_reads_bodies():
    assert _banned_hits("t.py::test_plain", "x = 'emergency'") == ["emergency"]
    assert _banned_hits("t.py::test_funding_rate", "") == ["funding"]
    assert _banned_hits("t.py::test_x", "protection_window = 1") == ["protection"]
    assert _banned_hits("t.py::test_param_alarm", "harmless = charm") == []


def test_no_stale_entries():
    stale = sorted(e for e in plugin.load_list(LIST) if _test_source(e) is None)
    assert not stale, f"stale/renamed R3 legacy entries: {stale}"
    assert _test_source("test_r3_legacy_xfail_scope.py::test_no_stale_entries")
    assert _test_source("test_r3_legacy_xfail_scope.py::test_renamed_away") is None


def test_list_is_shrink_only():
    entries = plugin.load_list(LIST)
    assert len(entries) <= CEILING, f"R3 legacy list grew to {len(entries)} > {CEILING}"
    added = sorted(entries - PINNED_ENTRIES)
    assert not added, f"R3 legacy list is pinned (committed base); new entries: {added}"
    base = os.environ.get("R3_BASE_LIST")
    if base and os.path.exists(base):
        added = sorted(entries - plugin.load_list(base))
        assert not added, f"R3 legacy list is shrink-only; new entries: {added}"
