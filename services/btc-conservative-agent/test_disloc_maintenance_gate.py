"""LIVE-FIX-1512 (HM 11:16): a deploy/operator pause blocks NEW DISLOC entries but never exits."""
import ast, pathlib
import disloc_engine as dsl

SRC = (pathlib.Path(__file__).parent / "bot.py").read_text(encoding="utf-8")


class _Rule:
    one_signal_per_minute = False
    ttl_sec, stop_bp, hold_sec, max_bbo_age = 5.0, 30.0, 60.0, 5.0
    gap_closed_exit = False
    def side(self, *a): return self._side
    def shadow_short(self, *a): return False
    def gap_closed(self, *a): return False
    def identity(self): return {}



def _make(block_owner):
    cls = next(c for c in vars(dsl).values() if isinstance(c, type) and hasattr(c, "paused_skip_summary"))
    rule = _Rule(); rule._side = None
    e = cls(rule)
    rows = []
    e.bind_runtime(enabled_fn=lambda: True, trades_fn=lambda: [], touch_fn=lambda: (100.0, 100.1, 1e12),
                   append_fn=rows.append, entries_allowed_fn=lambda: block_owner[0])
    e._features = lambda now, live, q, ts: {"stale_reasons": [], "anchor_bucket_ts": now, "prem_bp": 5.0}
    e._shadow_step = lambda *a, **k: None
    return e, rule, rows


def test_pause_blocks_entry_but_exit_still_fires():
    owner = [None]
    e, rule, rows = _make(owner)
    # an open paper position
    e.position = {"side": dsl.LONG, "fill_ts": 1000.0, "stop_px": 50.0, "trade_id": "dsl-x"}
    closed = []
    e._arm_exit = lambda now, reason, *a: (closed.append(reason), setattr(e, "position", None))
    owner[0] = "DEPLOY_MAINTENANCE"
    e._open = lambda *a, **k: (_ for _ in ()).throw(AssertionError("entry opened while paused"))
    e.step(now=1000.0 + rule.hold_sec + 1, live={}, bfx_quotes={}, bfx_bbo_ts=1000.0)
    assert closed == ["TIME_60S"]
    rule._side = dsl.LONG  # rising signal while paused
    e.step(now=1100.0, live={}, bfx_quotes={}, bfx_bbo_ts=1100.0)
    assert e.counters["skipped_paused"] == 1
    s = e.paused_skip_summary()
    assert s["recent"][-1]["reason"] == "PAUSED" and s["recent"][-1]["pause_owner"] == "DEPLOY_MAINTENANCE"


def test_entries_resume_on_first_tick_after_unpause():
    owner = ["OPERATOR"]
    e, rule, rows = _make(owner)
    opened = []
    e._open = lambda *a, **k: opened.append(1)
    rule._side = dsl.LONG
    e.step(now=2000.0, live={}, bfx_quotes={}, bfx_bbo_ts=2000.0)
    assert not opened
    owner[0] = None
    rule._side = None; e.step(now=2001.0, live={}, bfx_quotes={}, bfx_bbo_ts=2001.0)
    rule._side = dsl.LONG; e.step(now=2002.0, live={}, bfx_quotes={}, bfx_bbo_ts=2002.0)
    assert opened == [1]


def _func(name):
    for node in ast.walk(ast.parse(SRC)):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(name)


def test_only_deploy_and_operator_pauses_block():
    fn = _func("_disloc_entry_pause_owner")
    ns = {"state": {}, "PAUSE_OWNER_DEPLOY_MAINTENANCE": "DEPLOY_MAINTENANCE", "PAUSE_OWNER_OPERATOR": "OPERATOR"}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), "x", "exec"), ns)
    f = ns["_disloc_entry_pause_owner"]
    assert f() is None
    ns["state"].update(execution_paused=True)  # SAFETY / relay style pause
    assert f() is None
    for owner, want in (("DEPLOY_MAINTENANCE", "DEPLOY_MAINTENANCE"), ("OPERATOR", "OPERATOR"),
                        ("SAFETY", None), ("UNATTRIBUTED_MANUAL", None)):
        ns["state"].update(manual_admin_pause=True, pause_intent=owner)
        assert f() == want


def test_bot_binds_entry_gate_not_enabled_fn():
    seg = ast.get_source_segment(SRC, _func("_disloc_bind_runtime"))
    assert "entries_allowed_fn=_disloc_entry_pause_owner" in seg
    assert "pause" not in seg.split("enabled_fn=")[1].split(",")[0]


def test_paused_skips_persist_across_restart(tmp_path):
    import json
    path = tmp_path / "p.jsonl"
    owner = ["DEPLOY_MAINTENANCE"]
    e, rule, rows = _make(owner)
    e.paused_append_fn = lambda r: path.open("a").write(json.dumps(r) + "\n")
    rule._side = dsl.LONG
    e.step(now=3000.0, live={}, bfx_quotes={}, bfx_bbo_ts=3000.0)
    # "restart": a fresh engine has an empty in-memory ring, the durable ledger still counts it
    e2, _, _ = _make(owner)
    assert e2.counters["skipped_paused"] == 0
    h = dsl.paused_skip_history(str(path), boot_ts=2999.0)
    assert h["total"] == 1 and h["since_boot"] == 1 and h["by_pause_owner"] == {"DEPLOY_MAINTENANCE": 1}
    assert dsl.paused_skip_history(str(tmp_path / "none.jsonl"))["total"] == 0
