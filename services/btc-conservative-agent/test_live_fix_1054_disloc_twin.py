"""LIVE-FIX-1054: DISLOC live copies get a paper twin in the live lifecycle."""
import os

os.environ.setdefault("FORCE_PAPER_MODE", "true")
import bot  # noqa: E402


def test_disloc_bridge_events_build_paper_twin():
    bot._DISLOC_PAPER_TWINS.clear()
    base = {"direction": "LONG"}
    bot._disloc_note_paper_twin("ORDER_PLACED", "dsl-t1", base, {})
    assert bot._live_copy_paper_twins()["dsl-t1"]["side"] == "LONG"
    bot._disloc_note_paper_twin("POSITION_OPENED", "dsl-t1", {**base, "fill_price": 83834.0, "qty": 0.0003}, {})
    bot._disloc_note_paper_twin("POSITION_CLOSED", "dsl-t1",
                                {**base, "exit_price": 83796.0, "exit_reason": "TP"}, {"exit_ts": 1791762771.3})
    twin = bot._live_copy_paper_twins()["dsl-t1"]
    assert twin["entry_price"] == 83834.0
    assert twin["exit_price"] == 83796.0 and twin["exit_ts"] == 1791762771.3
