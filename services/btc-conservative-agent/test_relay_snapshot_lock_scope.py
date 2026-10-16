"""G17 (HM 12:20): _build_relay_execution_state_snapshot holds trade_lock only to copy raw containers."""
import ast, pathlib

SRC = (pathlib.Path(__file__).parent / "bot.py").read_text(encoding="utf-8")


def _fn(name):
    for n in ast.walk(ast.parse(SRC)):
        if isinstance(n, ast.FunctionDef) and n.name == name:
            return n
    raise AssertionError(name)


def test_trade_lock_section_copies_containers_only():
    fn = _fn("_build_relay_execution_state_snapshot")
    seg = ast.get_source_segment(SRC, fn)
    i = seg.find("trade_acquired = trade_lock.acquire(")
    j = seg.find("trade_lock.release()", i)
    locked = seg[i:j]
    for heavy in ("_snapshot_relay_trade_projections_locked", "_session_trade_accounting_locked",
                  "_session_forced_close_pnl_usd_locked", "_snapshot_expired_rows_locked",
                  "_snapshot_bounded_trades_map_locked", "_enrich_dashboard_trade_rows"):
        assert heavy not in locked, heavy
    for raw in ("_fast_state_copy(pending_orders)", "_fast_state_copy(open_positions)",
                "_RELAY_EXECUTION_MONEY_STATE_GENERATION", "list(trades)", "list(expired_orders)", "dict(trades_map)"):
        assert raw in locked, raw
    after = seg[j:]
    assert "trades_src=trades_ref" in after and "expired_src=expired_ref" in after and "map_src=trades_map_ref" in after


def test_helpers_default_to_globals():
    for name, param in (("_snapshot_relay_trade_projections_locked", "trades_src"),
                        ("_session_trade_accounting_locked", "trades_src"),
                        ("_session_forced_close_pnl_usd_locked", "trades_src"),
                        ("_snapshot_expired_rows_locked", "expired_src"),
                        ("_snapshot_bounded_trades_map_locked", "map_src")):
        args = [a.arg for a in _fn(name).args.args]
        assert param in args, (name, param)
