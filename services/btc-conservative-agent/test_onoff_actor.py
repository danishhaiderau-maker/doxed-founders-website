"""HM 10:34 (b): live-copy output history records actor + reason + ua/ip."""
import ast, pathlib
SRC = (pathlib.Path(__file__).parent / "bot.py").read_text(encoding="utf-8")


def _fns():
    tree = ast.parse(SRC)
    return [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name in ("_onoff_actor", "_onoff_reason")]


class _Req:
    def __init__(self, h): self.headers = h; self.remote_addr = "1.2.3.4"


def _ns(headers):
    ns = {"request": _Req(headers)}
    exec(compile(ast.Module(body=_fns(), type_ignores=[]), "x", "exec"), ns)
    return ns


def test_actor_sources():
    assert _ns({})["_onoff_actor"]({}) == "dashboard"
    assert _ns({})["_onoff_actor"]({"actor": "BOSS"}) == "BOSS"
    assert _ns({"X-Caller": "DANISH"})["_onoff_actor"]({}) == "DANISH"
    assert _ns({"X-GitHub-Run-Id": "9"})["_onoff_actor"]({}) == "DEPLOY"


def test_reason_tag():
    r = _ns({"User-Agent": "curl/8", "Fly-Client-IP": "5.6.7.8", "X-GitHub-Run-Id": "9"})["_onoff_reason"](
        "OPERATOR_OFF", {"reason": "PRE_DEPLOY_FLAT"})
    assert r == "OPERATOR_OFF|PRE_DEPLOY_FLAT|gha:9|ua:curl/8|ip:5.6.7.8"


def test_route_uses_helpers():
    assert 'by="dashboard", reason="OPERATOR_OFF"' not in SRC
    assert SRC.count("by=_onoff_actor(data)") == 2
