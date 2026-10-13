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


def test_book_tape_and_ticker_banned_in_fill_pricing_modules():
    root = pathlib.Path(__file__).parent
    offenders = []
    for p in root.rglob("*.py"):
        rel = p.relative_to(root).as_posix()
        if rel.startswith("test_") or "/test_" in rel or rel in LEGACY_V0_ALLOWED or rel == "paper_fill.py":
            continue
        if not any(k in rel for k in ("fill", "paper_policy", "binding")):
            continue
        src = p.read_text(encoding="utf-8", errors="ignore")
        if any(b in src for b in BANNED):
            offenders.append(rel)
    assert not offenders, f"book_tape_1s / microstructure tape used for fill pricing: {offenders}"


def test_paper_fill_has_no_banned_source():
    src = pathlib.Path(pf.__file__).read_text(encoding="utf-8")
    names = ("paper_fill", "_walk", "taker_side_levels", "_safe", "_snap")
    code = "".join(ast.get_source_segment(src, n) or "" for n in ast.parse(src).body
                   if isinstance(n, ast.FunctionDef) and n.name in names)
    assert not any(b in code for b in BANNED) and "state[" not in code and "ticker" not in code.lower()


def test_meta_guard_catches_synthetic_offender(tmp_path):
    bad = tmp_path / "my_new_tile_fill.py"
    bad.write_text("px = read('market_microstructure_1s')\n")
    assert any(b in bad.read_text() for b in BANNED)


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
    v = pf.fill_site_violations((ROOT / "disloc_engine.py").read_text(encoding="utf-8"), "_taker_fill")
    assert any("does not call paper_fill" in x for x in v) and any("_touch" in x for x in v)


def test_planted_offender_in_non_fill_file_fails():
    planted = """
def place_tile_order(self, side):
    px = state["ask"] if side == "LONG" else state.get("bid")
    tape = load("book_tape_1s")
    return px
"""
    v = pf.fill_site_violations(planted, "place_tile_order")
    assert any("state['ask']" in x for x in v) and any("state.get('bid')" in x for x in v)
    assert any("book_tape_1s" in x for x in v) and any("does not call paper_fill" in x for x in v)


def test_compliant_site_passes():
    good = """
import paper_fill
def fill(self, side, qty, ts):
    return paper_fill.paper_fill(side, qty, ts, self.delay(), self.book_at)
"""
    assert pf.fill_site_violations(good, "fill") == []


def test_aliased_imports_resolve():
    mod_alias = """
import paper_fill as pfx
def fill(self, side, qty, ts):
    return pfx.paper_fill(side, qty, ts, 1.5, self.book_at)
"""
    fn_alias = """
from paper_fill import paper_fill as f
def fill(self, side, qty, ts):
    return f(side, qty, ts, 1.5, self.book_at)
"""
    assert pf.fill_site_violations(mod_alias, "fill") == []
    assert pf.fill_site_violations(fn_alias, "fill") == []


def test_unimported_lookalike_does_not_count():
    fake = """
def fill(self, side, qty, ts):
    return helpers.paper_fill(side, qty, ts, 1.5, self.book_at)
"""
    assert any("does not call paper_fill" in x for x in pf.fill_site_violations(fake, "fill"))


def test_dynamic_and_indirect_routes_fail():
    via_getattr = """
import paper_fill
def fill(self, side, qty, ts):
    return getattr(paper_fill, "paper_fill")(side, qty, ts, 1.5, self.book_at)
"""
    via_importlib = """
import importlib
def fill(self, side, qty, ts):
    return importlib.import_module("paper_fill").paper_fill(side, qty, ts, 1.5, self.book_at)
"""
    via_helper = """
from paper_fill import paper_fill
def fill(self, side, qty, ts):
    return run_with(paper_fill, side, qty, ts)
"""
    for src in (via_getattr, via_importlib, via_helper):
        v = pf.fill_site_violations(src, "fill")
        assert v, src


def test_tile_modules_never_read_state_prices():
    planted = "def anywhere():\n    return state['bid'] + state.get('price')\n"
    v = pf.tile_module_violations(planted, "x.py")
    assert len(v) == 2
    mods = {m for sites in pf.TILE_FILL_SITES.values() for m, _ in sites if m != "bot.py"}
    bad = [x for m in mods for x in pf.tile_module_violations((ROOT / m).read_text(encoding="utf-8"), m)]
    assert not bad, bad
