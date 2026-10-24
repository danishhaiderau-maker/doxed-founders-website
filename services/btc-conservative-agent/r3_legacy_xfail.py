"""Scoped, strict xfail for R3 legacy market_segment tests (RK ruling on #873).

A test listed in ci/r3_legacy_segment_tests.txt is reported as xfailed ONLY if it
fails because of the R3 market_segment gate:

* the market_segment_r3 gate was touched during that test (HIT_COUNT grew), and
* the failure is ``MarketSegmentInputInvalid``, or one of the assertion-style
  types that fail-closed outputs produce (AssertionError, KeyError, StopIteration).

Anything else is a real failure and breaks CI: a pass (strict XPASS), a failure
of any other type (TypeError, ImportError, ...), or any failure without a gate
hit. ``R3_LEGACY_LIST`` overrides the list path (used by the self-test).
"""
from __future__ import annotations

import os

import pytest

import market_segment_r3 as _msr3

_DEFAULT_LIST = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ci", "r3_legacy_segment_tests.txt")
SCOPED_TYPES = (_msr3.MarketSegmentInputInvalid, AssertionError, KeyError, StopIteration)
XFAIL_REASON = "INVALID_R3_MARKET_SEGMENT_INPUT (scoped: gate hit + allowed failure type)"
_START = pytest.StashKey[int]()
_LEGACY = pytest.StashKey[bool]()


def load_list(path: str | None = None) -> set[str]:
    path = path or os.environ.get("R3_LEGACY_LIST") or _DEFAULT_LIST
    try:
        with open(path, encoding="utf-8") as handle:
            return {line.strip() for line in handle if line.strip() and not line.startswith("#")}
    except FileNotFoundError:
        return set()


def classify(excinfo, gate_hits: int) -> str:
    """'xfail' when the failure is attributable to the R3 gate, else 'fail'."""
    if excinfo is None:
        return "xpass"
    if gate_hits <= 0:
        return "fail"
    if not isinstance(excinfo.value, SCOPED_TYPES):
        return "fail"
    return "xfail"


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
        verdict = classify(call.excinfo, hits)
        if verdict == "xfail":
            report.outcome = "skipped"
            report.wasxfail = f"{XFAIL_REASON}; last gate touch={_msr3.LAST_HIT}"
        else:
            report.longrepr = (f"[R3 SCOPE] legacy test failed for a NON-R3 reason "
                               f"(gate_hits={hits}, type={type(call.excinfo.value).__name__}); "
                               f"this breaks CI.\n{report.longrepr}")
