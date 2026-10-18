"""--allow-open: open paper trades never block a deploy; live/force_paper/status still do."""
import importlib.util, pathlib
spec = importlib.util.spec_from_file_location("g", pathlib.Path(__file__).with_name("deploy_gate_open_trades.py"))
g = importlib.util.module_from_spec(spec); spec.loader.exec_module(g)


def test_open_trades_are_info_only():
    r = g.apply_allow_open({"ok": False, "reasons": ["OPEN_TRADES:3", "COUNT_MISMATCH:monitor=3 trades_feed=2"]})
    assert r["ok"] and r["reasons"] == [] and len(r["info_open_trades_will_be_flattened"]) == 2


def test_live_armed_force_paper_and_status_failure_still_block():
    for bad in ("LIVE_NOT_OFF", "FORCE_PAPER_NOT_TRUE", "STATUS_READ_FAILED:URLError"):
        r = g.apply_allow_open({"ok": False, "reasons": ["OPEN_TRADES:1", bad]})
        assert not r["ok"] and r["reasons"] == [bad]


def test_strict_mode_unchanged():
    assert not g.evaluate(lambda p: (_ for _ in ()).throw(RuntimeError("x")))["ok"]
