"""RK (24 Oct) item A: proof_failures always runs; every reason is recorded; the countable set is unchanged."""
import importlib.util
import itertools
import subprocess
from pathlib import Path

import r17_exclusion as rx
from test_r17_exclusion import _epoch, good_row  # noqa: F401  (fixture: fresh epoch)

HERE = Path(__file__).resolve().parent


def _old_module():
    src = subprocess.run(["git", "show", "8d9de158:services/btc-conservative-agent/r17_exclusion.py"],
                         cwd=HERE, capture_output=True, text=True)
    if src.returncode != 0:
        return None
    path = HERE / ".r17_exclusion_before.py"
    path.write_text(src.stdout)
    try:
        spec = importlib.util.spec_from_file_location("r17_before", path)
        mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
        return mod
    finally:
        path.unlink()


def _rows():
    base = good_row()
    for nc, reason, plural, proof in itertools.product(
            [None, False, True], [None, "", "R17_STAMPED", "DEPLOY_FLATTEN"],
            [None, [], ["DEFAULT_DELAY_ENTRY", "DEFAULT_DELAY_EXIT"], "DEFAULT_DELAY_ENTRY"], [False, True]):
        row = dict(base, non_countable=nc, non_countable_reason=reason, non_countable_reasons=plural)
        if proof:
            row["entry_price_source"] = "TICKER"
        yield row


def test_no_bare_r17_stamped_when_reasons_exist():
    row = {"trade_id": "t", "non_countable": True, "non_countable_reasons": ["DEFAULT_DELAY_ENTRY", "DEFAULT_DELAY_EXIT"],
           "entry_price_source": "TICKER"}
    reason = rx.non_countable_reason(row, ids=set(), load_errors=[])
    assert reason.startswith("R17_PRECOUNT:DEFAULT_DELAY_ENTRY|DEFAULT_DELAY_EXIT;R17_UNPROVEN:")
    assert reason != rx.REASON_STAMPED
    stamped = rx.stamp_on_write(dict(row))
    assert stamped["non_countable_reason"] != rx.REASON_STAMPED


def test_proof_failures_run_even_when_already_stamped():
    row = {"trade_id": "t", "non_countable": True, "non_countable_reason": "DEPLOY_FLATTEN"}
    reason = rx.non_countable_reason(row, ids=set(), load_errors=[])
    assert reason.startswith("DEPLOY_FLATTEN;R17_UNPROVEN:")


def test_restamping_is_stable():
    row = {"trade_id": "t", "non_countable": True, "non_countable_reasons": ["DEFAULT_DELAY_EXIT"]}
    once = rx.stamp_on_write(dict(row))
    twice = rx.stamp_on_write(dict(once))
    assert once["non_countable_reason"] == twice["non_countable_reason"]


def test_countable_set_identical_before_and_after():
    old = _old_module()
    assert old is not None, "base revision must be readable"
    seen = set()
    for row in _rows():
        before = old.non_countable_reason(dict(row), ids=set(), load_errors=[]) is None
        after = rx.non_countable_reason(dict(row), ids=set(), load_errors=[]) is None
        assert before == after, row
        seen.add(after)
    assert seen == {True, False}, "fixture must include countable and non-countable rows"
