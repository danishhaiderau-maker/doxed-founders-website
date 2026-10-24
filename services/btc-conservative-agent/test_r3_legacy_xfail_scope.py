"""Mechanical proof for the R3 legacy xfail list (RK ruling on #873).

1. xfail is strict and scoped: XPASS fails, a non-R3 failure fails, only a
   gate-hit failure of an allowed type xfails (unit + real pytest subprocess).
2. No forbidden-category test (safety, arm/guardian, fence, fill model,
   quote/trigger, stop, delay, exit, R17/countability, epoch) is listed.
3. The list is shrink-only: count <= CEILING, and a subset of the base-branch
   list when CI provides it via R3_BASE_LIST.
"""
from __future__ import annotations

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
CEILING = 94  # shrink-only: lower this when entries are removed; never raise it.
FORBIDDEN = re.compile(
    r"safety|arm|guard|fence|fill|quote|trigger|stop|delay|exit|r17|countab|epoch|kill|live",
    re.IGNORECASE)


class _Exc:
    def __init__(self, value):
        self.value = value


def test_classify_is_scoped():
    assert plugin.classify(None, 3) == "xpass"
    assert plugin.classify(_Exc(AssertionError()), 0) == "fail"
    assert plugin.classify(_Exc(TypeError()), 2) == "fail"
    assert plugin.classify(_Exc(ImportError()), 2) == "fail"
    assert plugin.classify(_Exc(AssertionError()), 1) == "xfail"
    assert plugin.classify(_Exc(msr3.MarketSegmentInputInvalid("x")), 1) == "xfail"


def test_plugin_end_to_end_in_real_pytest(tmp_path):
    (tmp_path / "test_probe.py").write_text(textwrap.dedent("""
        import market_segment_r3 as m
        def test_gate_hit():
            m._hit("probe"); assert False
        def test_unrelated_assert():
            assert False
        def test_unrelated_type():
            m._hit("probe"); raise TypeError("not R3")
        def test_passes():
            m._hit("probe")
        def test_not_listed():
            pass
    """))
    listed = ["test_gate_hit", "test_unrelated_assert", "test_unrelated_type", "test_passes"]
    (tmp_path / "list.txt").write_text("".join(f"test_probe.py::{n}\n" for n in listed))
    env = dict(os.environ, R3_LEGACY_LIST=str(tmp_path / "list.txt"),
               PYTHONPATH=HERE + os.pathsep + os.environ.get("PYTHONPATH", ""))
    env.pop("R3_XPASS_OUT", None)
    run = subprocess.run([sys.executable, "-m", "pytest", "-p", "r3_legacy_xfail", "-p", "no:cacheprovider",
                          "-rA", "-q", "--rootdir", str(tmp_path), "--noconftest", str(tmp_path / "test_probe.py")],
                         cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120)
    out = run.stdout
    assert run.returncode != 0, out
    assert re.search(r"XFAIL .*test_gate_hit", out), out
    assert re.search(r"FAILED .*test_unrelated_assert", out), out
    assert re.search(r"FAILED .*test_unrelated_type", out), out
    assert re.search(r"FAILED .*test_passes", out), out
    assert re.search(r"PASSED .*test_not_listed", out), out
    assert "3 failed" in out and "1 passed" in out and "1 xfailed" in out, out


def test_no_forbidden_category_on_list():
    bad = sorted(n for n in plugin.load_list(LIST) if FORBIDDEN.search(n))
    assert not bad, f"forbidden-category tests must be rewritten, not listed: {bad}"


def test_list_is_shrink_only():
    entries = plugin.load_list(LIST)
    assert len(entries) <= CEILING, f"R3 legacy list grew to {len(entries)} > {CEILING}"
    base = os.environ.get("R3_BASE_LIST")
    if base and os.path.exists(base):
        added = sorted(entries - plugin.load_list(base))
        assert not added, f"R3 legacy list is shrink-only; new entries: {added}"
