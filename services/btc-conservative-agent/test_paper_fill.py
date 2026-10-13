"""Book-pricing PR A: shared paper_fill + CI guards (ticker / book_tape_1s ban, new-tile rule)."""
import ast, pathlib
import paper_fill as pf

BOOK = {"s": 100, "a": [[100.0, 1.0], [101.0, 1.0]], "b": [[99.0, 1.0], [98.0, 1.0]]}
at = lambda t: BOOK if t >= 100 else None


def test_walks_depth_at_signal_plus_delay():
    r = pf.paper_fill("LONG", 1.5, 99.0, 1.5, at)
    assert r["status"] == "FILLED" and abs(r["price"] - (100 + 0.5 * 101) / 1.5) < 1e-9
    assert r["fill_ts"] == 100.5 and r["fill_model_version"] == 1 and r["book_s"] == 100
    assert r["depth_snapshot_fill"]["a"][0] == [100.0, 1.0]
    assert r["depth_snapshot_signal"] is None  # no book at t=99 in this fixture


def test_exit_sides_and_partial():
    assert pf.paper_fill("LONG", 1, 99, 1.5, at, leg="exit")["price"] == 99.0
    assert pf.paper_fill("SHORT", 1, 99, 1.5, at, leg="exit")["price"] == 100.0
    p = pf.paper_fill("LONG", 5, 99, 1.5, at)
    assert p["status"] == "PARTIAL" and p["filled_qty"] == 2.0


def test_no_book_rejects_never_ticker():
    r = pf.paper_fill("LONG", 1, 10.0, 1.5, lambda t: None)
    assert r["status"] == "REJECT_NO_BOOK" and r["price"] is None


def test_signal_and_fill_books_recorded():
    r = pf.paper_fill("LONG", 1, 100.0, 1.0, at)
    assert r["depth_snapshot_signal"]["s"] == 100 and r["depth_snapshot_fill"]["s"] == 100


def test_book_read_failure_is_no_book():
    def boom(t):
        raise OSError("disk")
    r = pf.paper_fill("LONG", 1, 99, 1.5, boom)
    assert r["status"] == "REJECT_NO_BOOK" and r["price"] is None


def test_top_only_partial_at_top_size_and_counted():
    before = dict(pf.COUNTERS)
    r = pf.paper_fill("LONG", 2, 10.0, 1.5, lambda t: None, top_at=lambda t: (99.0, 5.0, 100.0, 0.5, 11.4))
    assert r["status"] == "PARTIAL_TOP_ONLY" and r["filled_qty"] == 0.5 and r["price"] == 100.0
    assert pf.COUNTERS["top_only"] == before["top_only"] + 1
    r2 = pf.paper_fill("LONG", 2, 10.0, 1.5, lambda t: None, top_at=lambda t: (99.0, 100.0, 11.4))
    assert r2["status"] == "REJECT_NO_BOOK" and pf.COUNTERS["top_only_rejected"] == before["top_only_rejected"] + 1


def test_default_delay_marked():
    assert pf.paper_fill("LONG", 1, 99, None, at)["delay_source"] == "default_until_n10"


def test_delay_floor_and_sampler():
    assert pf.paper_fill("LONG", 1, 99.9, 0.01, at)["delay_s"] == pf.MIN_DELAY_S
    s = pf.DelaySampler()
    assert s.delay("L")[1] == "default_until_n10"
    for _ in range(10):
        s.add("L", "entry", 0.8)
    assert s.delay("L") == (0.8, "lane_p50_n10")


BANNED = ("market_microstructure_1s", "book_tape_1s")
# v0 "old pricing" modules; PR C removes them from fill paths. Nothing may be ADDED to this list.
LEGACY_V0_ALLOWED = {"research/fill_model.py", "fill_pricing_source.py", "research/conservative_limit_fill.py"}


def test_paper_fill_has_no_banned_source():
    src = pathlib.Path(pf.__file__).read_text(encoding="utf-8")
    names = ("paper_fill", "_walk", "taker_side_levels", "_safe", "_snap")
    code = "".join(ast.get_source_segment(src, n) or "" for n in ast.parse(src).body
                   if isinstance(n, ast.FunctionDef) and n.name in names)
    assert not any(b in code for b in BANNED) and "state[" not in code and "ticker" not in code.lower()


# ------------------------------------------------ new-tile rule (HM 12:20)
import combo_pathway_config as _cpc

ROOT = pathlib.Path(__file__).parent


def test_every_registry_tile_declares_fill_sites():
    missing = [lane for lane in _cpc.ACTIVE_TILE_REGISTRY if lane not in pf.TILE_FILL_SITES]
    assert not missing, f"new tile without declared fill sites (must use paper_fill): {missing}"


def test_migrated_tiles_call_paper_fill_and_never_read_state_or_tape():
    bad = []
    for lane, sites in pf.TILE_FILL_SITES.items():
        if lane in pf.PENDING_MIGRATION or lane not in _cpc.ACTIVE_TILE_REGISTRY:
            continue
        for module, func in sites:
            bad += [f"{lane}: {v}" for v in pf.fill_site_violations((ROOT / module).read_text(encoding="utf-8"), func)]
    assert not bad, bad


BASELINE_13OCT = frozenset({
    "FAMILY_RANDOM_CONTROL_TAKER_90", "FAMILY_GS01_XV_PREMIUM_ATR_TP", "FAMILY_GSB1_CVD_DIV_REGIME",
    "FAMILY_GSB2_REGIME_SWITCHER", "FAMILY_GS06_COMMITTED_FADE_ATR_TP", "FAMILY_DISLOC_BFX_DISLOCATION_1M",
    "FAMILY_DISLOC_FOI_LONG_1M"})


def test_pending_migration_only_shrinks():
    # Pinned here, not imported: editing paper_fill alone cannot widen it.
    assert pf.PENDING_MIGRATION <= BASELINE_13OCT
    assert pf.PENDING_MIGRATION_BASELINE <= BASELINE_13OCT


def test_new_registry_lanes_cannot_be_pending():
    new_lanes = set(_cpc.ACTIVE_TILE_REGISTRY) - BASELINE_13OCT
    assert not (new_lanes & pf.PENDING_MIGRATION), "new tiles must use paper_fill from day one"


def test_pending_migration_expires():
    import datetime
    expiry = datetime.date.fromisoformat(pf.PENDING_MIGRATION_EXPIRES)
    assert expiry <= datetime.date(2026, 10, 27), "expiry may not be pushed out"
    if datetime.date.today() > expiry:
        assert not pf.PENDING_MIGRATION, f"pending migration expired {expiry}: {sorted(pf.PENDING_MIGRATION)}"


def test_pending_lanes_excluded_from_fresh_data():
    for lane in pf.PENDING_MIGRATION:
        assert pf.fresh_data_eligible(lane) is False
    assert pf.fresh_data_eligible("SOME_MIGRATED_LANE") is True


def test_pending_sites_are_detected_today():
    # proves the checker sees the current non-compliant code (so it is not vacuous)
    v = pf.fill_site_violations((ROOT / "bot.py").read_text(encoding="utf-8"), "simulate_marketable_limit_fill")
    assert any("last statement must be" in x for x in v)


def test_planted_offender_in_non_fill_file_fails():
    planted = """
def place_tile_order(self, side):
    px = state["ask"] if side == "LONG" else state.get("bid")
    tape = load("book_tape_1s")
    return px
"""
    v = pf.fill_site_violations(planted, "place_tile_order")
    assert any("state['ask']" in x for x in v) and any("state.get('bid')" in x for x in v)
    assert any("book_tape_1s" in x for x in v) and any("last statement must be" in x for x in v)


GOOD = """
import paper_fill
def fill(self, o):
    return paper_fill.paper_fill(o["side"], o["qty"], o["signal_ts"], o["delay_s"], paper_fill.book10l_at,
                                 leg="entry", delay_source=o["delay_source"], cap_px=o["cap"])
"""


def test_compliant_site_passes():
    assert pf.fill_site_violations(GOOD, "fill") == []


def test_aliased_imports_resolve():
    mod_alias = """
import paper_fill as pfx
def fill(self, o):
    return pfx.paper_fill(o["side"], o["qty"], o["ts"], 1.5, pfx.book10l_at)
"""
    fn_alias = """
from paper_fill import paper_fill as f, book10l_at as bk
def fill(self, o):
    return f(o["side"], o["qty"], o["ts"], 1.5, book_at=bk)
"""
    assert pf.fill_site_violations(mod_alias, "fill") == []
    assert pf.fill_site_violations(fn_alias, "fill") == []


def test_unimported_lookalike_does_not_count():
    fake = """
def fill(self, o):
    return helpers.paper_fill(o["side"], o["qty"], o["ts"], 1.5, helpers.book10l_at)
"""
    assert pf.fill_site_violations(fake, "fill")


def test_dynamic_and_indirect_routes_fail():
    for src in ("""
import paper_fill
def fill(self, o):
    return getattr(paper_fill, "paper_fill")(o["side"], 1, 1, 1.5, paper_fill.book10l_at)
""", """
import importlib, paper_fill
def fill(self, o):
    return importlib.import_module("paper_fill").paper_fill(o["side"], 1, 1, 1.5, paper_fill.book10l_at)
""", """
from paper_fill import paper_fill, book10l_at
def fill(self, o):
    return run_with(paper_fill, o, book10l_at)
"""):
        assert pf.fill_site_violations(src, "fill"), src


# HM 13:20: the seven planted offenders that the 12:35 checker missed
OFFENDERS = {
    "returns_last_px_after_call": """
import paper_fill
def fill(self, o):
    paper_fill.paper_fill(o["side"], 1, 1, 1.5, paper_fill.book10l_at)
    return self.last_px
""",
    "state_alias": """
import paper_fill
def fill(self, o):
    s = state
    o["px"] = s['ask']
    return paper_fill.paper_fill(o["side"], 1, 1, 1.5, paper_fill.book10l_at)
""",
    "state_non_literal_key": """
import paper_fill
K = "ask"
def fill(self, o):
    o["px"] = state[K]
    return paper_fill.paper_fill(o["side"], 1, 1, 1.5, paper_fill.book10l_at)
""",
    "split_banned_string": """
import paper_fill
def fill(self, o):
    o["src"] = load('book_tape' + '_1s')
    return paper_fill.paper_fill(o["side"], 1, 1, 1.5, paper_fill.book10l_at)
""",
    "sys_modules": """
import sys, paper_fill
def fill(self, o):
    return sys.modules['paper_fill'].paper_fill(o["side"], 1, 1, 1.5, paper_fill.book10l_at)
""",
    "fake_book_lambda": """
import paper_fill
def fill(self, o):
    return paper_fill.paper_fill(o["side"], 1, 1, 1.5,
        lambda t: {"s": t, "a": [[self.last_ask, 9]], "b": [[self.last_bid, 9]]})
""",
    "update_price_ticker": """
import paper_fill
def fill(self, o):
    r = paper_fill.paper_fill(o["side"], 1, 1, 1.5, paper_fill.book10l_at)
    r.update(price=ticker)
    return r
""",
}


def test_each_planted_offender_is_caught():
    missed = [k for k, src in OFFENDERS.items() if not pf.fill_site_violations(src, "fill")]
    assert not missed, missed


def test_tile_module_scan_catches_aliases_and_folds():
    src = "import sys\nK='bid'\ndef a():\n    s = state\n    return state[K], sys.modules['x'], 'book_tape' + '_1s'\n"
    v = pf.tile_module_violations(src, "m.py")
    assert any("aliases state" in x for x in v) and any("non-literal" in x for x in v)
    assert any("sys.modules" in x for x in v) and any("book_tape_1s" in x for x in v)


def test_tile_modules_never_read_state_prices():
    mods = {m for sites in pf.TILE_FILL_SITES.values() for m, _ in sites if m != "bot.py"}
    bad = [x for m in mods for x in pf.tile_module_violations((ROOT / m).read_text(encoding="utf-8"), m)]
    assert not bad, bad


def _bot_fn(name, **ns):
    src = (ROOT / "bot.py").read_text(encoding="utf-8")
    node = next(n for n in ast.parse(src).body if isinstance(n, ast.FunctionDef) and n.name == name)
    g = {"_paper_fill": pf, **ns}
    exec(compile(ast.Module([node], []), "bot.py", "exec"), g)
    return g[name]


def test_fresh_data_wired_into_epoch_rows():
    import datetime as _dt
    pending = sorted(pf.PENDING_MIGRATION)[0]
    in_epoch = {"ts": "2026-10-13T03:00:00+00:00", "entry_ts": 2e9}
    row_session = _bot_fn("_trade_row_in_session", parse_ts=lambda v: 2e9)
    assert row_session({**in_epoch, "research_lane": "MIGRATED_LANE"}, 1e9) is True
    assert row_session({**in_epoch, "research_lane": pending}, 1e9) is False
    only = _bot_fn("_session_trades_only", _showcase_trade_session_start=lambda: 1e9, datetime=_dt.datetime)
    kept = only([{**in_epoch, "research_lane": pending}, {**in_epoch, "research_lane": "MIGRATED_LANE"}])
    assert [r["research_lane"] for r in kept] == ["MIGRATED_LANE"]


def test_fresh_data_wired_into_disloc_ledger():
    import disloc_engine as de
    pending = sorted(pf.PENDING_MIGRATION)[0]
    rows = [{"schema": de.SCHEMA, "event": "DISLOC_CLOSED", "exit_ts": 1, "lane": pending},
            {"schema": de.SCHEMA, "event": "DISLOC_CLOSED", "exit_ts": 2, "lane": "MIGRATED_LANE"}]
    assert [r["lane"] for r in de.closed_rows(rows)] == ["MIGRATED_LANE"]


def test_disloc_migrated():
    assert "FAMILY_DISLOC_BFX_DISLOCATION_1M" not in pf.PENDING_MIGRATION
    assert "FAMILY_DISLOC_FOI_LONG_1M" not in pf.PENDING_MIGRATION


def test_book_tail_no_look_ahead(tmp_path):
    import json
    p = tmp_path / "bfx_book_10l_1s.jsonl"
    p.write_text("".join(json.dumps({"s": s, "b": [[1.0, 1]], "a": [[2.0, 1]]}) + "\n" for s in range(100, 110)))
    bt = pf.BookTail(str(p))
    assert bt.at(105.5)["s"] == 104      # second 105 is not complete until 106
    assert bt.at(106.0)["s"] == 105
    assert bt.at(100.9) is None


def test_ioc_cap_filters_levels():
    book = lambda t: {"s": int(t) - 1, "b": [[99.0, 1]], "a": [[100.05, 0.1], [100.5, 5]]}
    r = pf.paper_fill("LONG", 1.0, 100.0, 1.5, book, cap_px=100.07)
    assert r["status"] == "PARTIAL" and r["price"] == 100.05 and r["filled_qty"] == 0.1
    r = pf.paper_fill("LONG", 1.0, 100.0, 1.5, book, cap_px=100.0)
    assert r["status"] == "NO_FILL_IOC_CAP" and r["price"] is None
