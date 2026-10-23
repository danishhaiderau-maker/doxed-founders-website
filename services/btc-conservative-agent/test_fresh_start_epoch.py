"""HM 14:10: fresh-start epoch route + every epoch-facing path cuts at it (static checks; bot.py is too big to boot)."""
import ast
import pathlib

SRC = pathlib.Path(__file__).with_name("bot.py").read_text(encoding="utf-8")
TREE = ast.parse(SRC)


def _fn(name):
    return next(n for n in ast.walk(TREE) if isinstance(n, ast.FunctionDef) and n.name == name)


def test_routes_exist_get_and_post():
    assert "@app.route('/api/monitor/fresh-start', methods=[\"GET\"])" in SRC
    assert "@app.route('/api/monitor/fresh-start', methods=[\"POST\"])" in SRC
    assert '"/api/monitor/fresh-start",' in SRC  # monitor-token GET allow-list


def test_epoch_boundary_includes_fresh_start():
    body = ast.unparse(_fn("_current_epoch_boundary"))
    assert "_fresh_start_epoch()" in body and "'fresh_start'" in body


def test_trade_paths_use_the_boundary():
    assert "_current_epoch_boundary()" in ast.unparse(_fn("_showcase_trade_session_start"))
    assert "_current_epoch_boundary()" in ast.unparse(_fn("_monitor_tiles_scope"))
    # _disloc_epoch_since was removed with Tiles 6/7 (23 Oct 2026); no DISLOC path is left to cut.
    assert not any(isinstance(n, ast.FunctionDef) and n.name == "_disloc_epoch_since" for n in ast.walk(TREE))


def test_setter_is_guarded_and_never_deletes():
    body = ast.unparse(_fn("monitor_fresh_start_set_route"))
    for needle in ("_admin_authed_strict()", "SET_FRESH_START_EPOCH", "live_armed", "EPOCH_NOT_FORWARD"):
        assert needle in body
    for bad in ("os.remove", "unlink", "rmtree", "truncate", "order_intent"):
        assert bad not in body
