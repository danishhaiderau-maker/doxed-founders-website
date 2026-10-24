"""realism-v2/integration: #842/#847 x #848 r17_exclusion resolution (RK 12:54 + HM conditions).

- every proof reason (liq, book receive, 1 s tape via each path, each entry_proof failure) is rejected;
- a row tripping both tape checks gets ENTRY_PRICED_ON_1S_TAPE exactly once;
- every production caller of proof_failures still reaches liq_failure / book_receive_failures, and
  entry_proof_failures is the single shared entry check;
- #842's helpers are byte-identical to a615fd6f.
"""
import ast
import hashlib
from pathlib import Path

import pytest

import r17_exclusion as r17
import r17_fields as F
from test_r17_exclusion import good_row  # noqa: F401  (also pulls the epoch fixture's module state)
from test_r17_exclusion import _epoch  # noqa: F401  autouse fixture

ROOT = Path(__file__).resolve().parent
TAPE = F.REASON_ENTRY_PRICED_ON_1S_TAPE

CASES = [
    ("liq_unverified", {"liq_unverified": True}, "LIQ_UNVERIFIED"),
    ("liq_missing", {"liq_unverified": None}, "LIQ_CHECK_MISSING"),
    ("liq_marks", {"liq_mark_rows_n": 0}, "LIQ_MARKS_MISSING"),
    ("liq_reached", {"liq_trigger_ts": 1234.5}, "LIQ_DISTANCE_REACHED"),
    ("book_rx_entry", {"entry_book_receive_ts_source": "INFERRED_BUCKET_LAG"}, "BOOK_RECEIVE_TS_MISSING"),
    ("book_rx_exit", {"exit_book_receive_ts": None}, "EXIT_BOOK_RECEIVE_TS_MISSING"),
    ("tape_source", {"entry_price_source": "MARKET_MICROSTRUCTURE_1S"}, TAPE),
    ("tape_status", {"r17_entry_status": F.R17_UNPROVEN_ENTRY}, TAPE),
    ("entry_trigger", {"trigger_price_source": "ticker"}, "TRIGGER_NOT_BOOK"),
    ("entry_trigger_missing", {"trigger_price_source": None}, "TRIGGER_SOURCE_MISSING"),
    ("entry_delay", {"delay_source": "DEFAULT"}, "DELAY_NOT_MEASURED"),
    ("entry_delay_missing", {"delay_source": None}, "DELAY_SOURCE_MISSING"),
    ("entry_evidence", {"evidence_status": "PARTIAL"}, "ENTRY_EVIDENCE_NOT_COMPLETE"),
    ("entry_age", {"entry_book_age_ms": None}, "ENTRY_PRICE_AGE_MISSING"),
]


def test_clean_row_passes():
    assert r17.proof_failures(good_row()) == []
    assert r17.entry_proof_failures(good_row()) == []
    assert r17.non_countable_reason(good_row(), ids=set(), load_errors=[]) is None


@pytest.mark.parametrize("name,extra,reason", CASES, ids=[c[0] for c in CASES])
def test_each_reason_rejects(name, extra, reason):
    row = good_row(**extra)
    failed = r17.proof_failures(row)
    assert reason in failed, failed
    nc = r17.non_countable_reason(row, ids=set(), load_errors=[])
    assert nc and reason in nc


def test_tape_both_checks_reason_once():
    row = good_row(entry_price_source="TICKER", r17_entry_status=F.R17_UNPROVEN_ENTRY)
    assert r17.entry_priced_on_1s_tape(row)
    assert TAPE in r17.entry_proof_failures(row)
    assert r17.proof_failures(row).count(TAPE) == 1


def test_tape_via_entry_split_path_only(monkeypatch):
    monkeypatch.setattr(r17, "entry_priced_on_1s_tape", lambda row: False)
    monkeypatch.setattr(r17, "entry_proof_failures", lambda row: [TAPE])
    assert r17.proof_failures(good_row()).count(TAPE) == 1


def test_tape_via_direct_predicate_only(monkeypatch):
    monkeypatch.setattr(r17, "entry_proof_failures", lambda row: [])
    monkeypatch.setattr(r17, "entry_priced_on_1s_tape", lambda row: True)
    assert r17.proof_failures(good_row()).count(TAPE) == 1


def test_proof_failures_still_calls_liq_and_book_receive(monkeypatch):
    seen = []
    monkeypatch.setattr(r17, "liq_failure", lambda row: seen.append("liq") or None)
    monkeypatch.setattr(r17, "book_receive_failures", lambda row: seen.append("rx") or [])
    r17.proof_failures(good_row())
    assert seen == ["liq", "rx"]
    seen.clear()
    r17.non_countable_reason(good_row(), ids=set(), load_errors=[])
    assert seen == ["liq", "rx"]


def _calls(path, name):
    t = ast.parse(path.read_text())
    return [n for n in ast.walk(t) if isinstance(n, ast.Call)
            and ((isinstance(n.func, ast.Name) and n.func.id == name)
                 or (isinstance(n.func, ast.Attribute) and n.func.attr == name))]


def test_callers_grep():
    prod = [p for p in ROOT.glob("*.py") if not p.name.startswith("test_")]
    pf = {p.name for p in prod if _calls(p, "proof_failures")}
    epf = {p.name for p in prod if _calls(p, "entry_proof_failures")}
    # proof_failures is only reached via r17_exclusion (non_countable_reason / stamp_on_write)
    assert pf == {"r17_exclusion.py"}, pf
    # one shared entry check: r17_exclusion (proof_failures) and book_limit_entry (research receipts)
    assert epf == {"r17_exclusion.py", "book_limit_entry.py"}, epf
    defs = [p.name for p in prod if "def entry_proof_failures" in p.read_text()]
    assert defs == ["r17_exclusion.py"]


def _fn_src(src, name):
    t = ast.parse(src)
    n = next(x for x in t.body if getattr(x, "name", "") == name)
    return "\n".join(src.splitlines()[n.lineno - 1:n.end_lineno])


# sha256 of each helper's source as of #842 @a615fd6f7590677d031e6534b5093354b9a12f8c
HELPER_SHA_842 = {
    "_false": "d49c639648798b3b6e3db0f09a7dab830829da78f3466080392efdea230365a7",
    "liq_failure": "1677886c6b7fa0dc608e7944e8ccc14ad87839589aaf7fede3a44300d2a72f4d",
    "book_receive_failures": "b658c01a8432279a10969be03b148273324b5cb902ddd44bda41c0c7f7cf4561",
}


@pytest.mark.parametrize("name", sorted(HELPER_SHA_842))
def test_842_helpers_byte_identical(name):
    src = (ROOT / "r17_exclusion.py").read_text()
    assert hashlib.sha256(_fn_src(src, name).encode()).hexdigest() == HELPER_SHA_842[name]


def test_helpers_before_entry_proof_failures():
    names = [getattr(x, "name", "") for x in ast.parse((ROOT / "r17_exclusion.py").read_text()).body]
    i = names.index("entry_proof_failures")
    for n in HELPER_SHA_842:
        assert names.index(n) < i
