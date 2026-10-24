"""Scoped, strict xfail for R3 legacy market_segment tests (RK ruling on #873).

A test listed in ci/r3_legacy_segment_tests.txt is reported as xfailed ONLY if it
fails because of the R3 market_segment gate:

* the market_segment_r3 gate fired during that test (HIT_COUNT grew; reading
  ``_msr3.REASON`` does not count), and
* the failure is ``MarketSegmentInputInvalid`` itself, or its failure text carries
  ``INVALID_R3_MARKET_SEGMENT_INPUT``.

Anything else is a real failure and breaks CI: a pass (strict XPASS), any other
failure (a bare AssertionError, KeyError, StopIteration, TypeError, ...), or any
failure without a gate hit. HM #873 R2: the list is now empty; the plugin stays so
that any re-listed test is held to this rule. ``R3_LEGACY_LIST`` overrides the list path (used by the self-test).
"""
from __future__ import annotations

import os

import pytest

import market_segment_r3 as _msr3

_DEFAULT_LIST = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ci", "r3_legacy_segment_tests.txt")
SCOPED_TYPES = (_msr3.MarketSegmentInputInvalid,)
R3_MARKER = "INVALID_R3_MARKET_SEGMENT_INPUT"
XFAIL_REASON = "INVALID_R3_MARKET_SEGMENT_INPUT (scoped: gate hit + R3 error)"
_START = pytest.StashKey[int]()
_LEGACY = pytest.StashKey[bool]()


def load_list(path: str | None = None) -> set[str]:
    path = path or os.environ.get("R3_LEGACY_LIST") or _DEFAULT_LIST
    try:
        with open(path, encoding="utf-8") as handle:
            return {line.strip() for line in handle if line.strip() and not line.startswith("#")}
    except FileNotFoundError:
        return set()


def classify(excinfo, gate_hits: int, text: str = "") -> str:
    """'xfail' only when the failure IS the R3 error, else 'fail'."""
    if excinfo is None:
        return "xpass"
    if gate_hits <= 0:
        return "fail"
    if isinstance(excinfo.value, SCOPED_TYPES):
        return "xfail"
    if R3_MARKER in (text or "") or R3_MARKER in str(excinfo.value):
        return "xfail"
    return "fail"


def pytest_collection_modifyitems(config, items):
    legacy = load_list()
    for item in items:
        item.stash[_LEGACY] = item.nodeid.rsplit("/", 1)[-1] in legacy


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item):
    item.stash[_START] = _msr3.HIT_COUNT


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    if not item.stash.get(_LEGACY, False):
        return
    report = outcome.get_result()
    hits = _msr3.HIT_COUNT - item.stash.get(_START, _msr3.HIT_COUNT)
    if call.when == "call" and report.passed:
        report.outcome = "failed"
        out = os.environ.get("R3_XPASS_OUT")
        if out:
            with open(out, "a", encoding="utf-8") as handle:
                handle.write(item.nodeid.rsplit("/", 1)[-1] + "\n")
        report.longrepr = f"[XPASS(strict)] R3 legacy test passed; remove it from ci/r3_legacy_segment_tests.txt"
        return
    if call.when in ("setup", "call") and report.failed:
        verdict = classify(call.excinfo, hits, str(report.longrepr))
        if verdict == "xfail":
            report.outcome = "skipped"
            report.wasxfail = f"{XFAIL_REASON}; last gate touch={_msr3.LAST_HIT}"
        else:
            report.longrepr = (f"[R3 SCOPE] legacy test failed for a NON-R3 reason "
                               f"(gate_hits={hits}, type={type(call.excinfo.value).__name__}); "
                               f"this breaks CI.\n{report.longrepr}")
