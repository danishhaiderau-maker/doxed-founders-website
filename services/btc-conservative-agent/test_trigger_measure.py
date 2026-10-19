"""#807 measurement fields: trigger price vs the 1 s book at the trigger second, ticker BBO age at the trigger."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import live_vs_paper as lvp  # noqa: E402
import trigger_measure as tm  # noqa: E402

BOOK = {"b": [[83143, 0.0355], [83137, 0.03]], "a": [[83156, 0.08], [83160, 0.03]], "s": 1792395274}


def test_long_uses_best_bid_and_positive_means_book_better_than_trigger():
    # gs1-0f552a03a035 (19 Oct 18:34:37 AEDT): fired on a stale ticker bid 83019 while the book bid was 83143
    assert tm.trigger_vs_book_bp("LONG", 83019.0, BOOK) == round((83143 / 83019 - 1) * 1e4, 3)
    assert tm.trigger_vs_book_bp("LONG", 83019.0, BOOK) > 14.9


def test_short_uses_best_ask_and_sign_flips():
    # SHORT: trigger ask 83200 vs book ask 83156 -> buying back is cheaper on the book -> positive
    assert tm.trigger_vs_book_bp("SHORT", 83200.0, BOOK) == round(-(83156 / 83200 - 1) * 1e4, 3)
    assert tm.trigger_vs_book_bp("SHORT", 83100.0, BOOK) < 0


def test_missing_or_bad_inputs_are_none_never_raise():
    for args in (("FLAT", 1.0, BOOK), ("LONG", None, BOOK), ("LONG", 0, BOOK), ("LONG", "x", BOOK),
                 ("LONG", 83000.0, None), ("LONG", 83000.0, {"b": []}), ("LONG", 83000.0, {"a": [[1, 1]]}),
                 ("LONG", float("nan"), BOOK)):
        assert tm.trigger_vs_book_bp(*args) is None


def test_tape_age():
    assert tm.trigger_tape_age_s(1792395275.306, 1792395273.5) == 1.806
    assert tm.trigger_tape_age_s(100.0, 101.0) == 0.0          # BBO newer than the trigger clock: clamp, never negative
    assert tm.trigger_tape_age_s(None, 1.0) is None and tm.trigger_tape_age_s(1.0, None) is None
    assert tm.trigger_tape_age_s(1.0, 0) is None


def test_fields_shape():
    out = tm.fields(side="LONG", trigger_price=83019.0, trigger_ts=10.0, bbo_ts=7.5, book_signal=BOOK)
    assert set(out) == {"trigger_tape_age_s", "trigger_vs_book_bp"} and out["trigger_tape_age_s"] == 2.5


def test_live_vs_paper_pair_carries_paper_trigger_fields():
    twin = {"side": "LONG", "entry_price": 83166.0, "exit_price": 83142.98, "exit_ts": 5.0,
            "trigger_vs_book_bp": 14.936, "trigger_tape_age_s": 1.806}
    row = lvp.pair_row("gs1-x", {}, twin)
    assert row["paper_trigger_vs_book_bp"] == 14.936 and row["paper_trigger_tape_age_s"] == 1.806
    bare = lvp.pair_row("gs1-y", {}, {"side": "LONG"})
    assert bare["paper_trigger_vs_book_bp"] is None and bare["paper_trigger_tape_age_s"] is None


def test_bot_wiring_present():
    src = open(os.path.join(os.path.dirname(__file__), "bot.py"), encoding="utf-8").read()
    assert 'pos["exit_trigger_bbo_ts"] = state.get("bbo_ts")' in src
    assert '"exit_trigger_bbo_ts": _ts_or_none(pos.get("exit_trigger_bbo_ts"))' in src
    assert src.count("**_twin_trigger_fields(row)") == 2
