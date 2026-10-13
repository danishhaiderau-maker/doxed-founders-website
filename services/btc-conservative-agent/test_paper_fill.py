"""Book-pricing PR A: shared paper_fill + CI guards (ticker / book_tape_1s ban, new-tile rule)."""
import ast, pathlib
import paper_fill as pf

BOOK = {"s": 100, "a": [[100.0, 1.0], [101.0, 1.0]], "b": [[99.0, 1.0], [98.0, 1.0]]}
at = lambda t: BOOK if t >= 100 else None


def test_walks_depth_at_signal_plus_delay():
    r = pf.paper_fill("LONG", 1.5, 99.0, 1.5, at)
    assert r["status"] == "FILLED" and abs(r["price"] - (100 + 0.5 * 101) / 1.5) < 1e-9
    assert r["fill_ts"] == 100.5 and r["fill_model_version"] == 1 and r["book_s"] == 100
    assert r["depth_snapshot"]["a"][0] == [100.0, 1.0]


def test_exit_sides_and_partial():
    assert pf.paper_fill("LONG", 1, 99, 1.5, at, leg="exit")["price"] == 99.0
    assert pf.paper_fill("SHORT", 1, 99, 1.5, at, leg="exit")["price"] == 100.0
    p = pf.paper_fill("LONG", 5, 99, 1.5, at)
    assert p["status"] == "PARTIAL" and p["filled_qty"] == 2.0


def test_no_book_rejects_never_ticker():
    r = pf.paper_fill("LONG", 1, 10.0, 1.5, lambda t: None)
    assert r["status"] == "REJECT_NO_BOOK" and r["price"] is None


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
    code = "".join(ast.get_source_segment(src, n) or "" for n in ast.parse(src).body
                   if isinstance(n, (ast.FunctionDef, ast.ClassDef)))
    assert not any(b in code for b in BANNED) and "state[" not in code and "ticker" not in code.lower()


def test_meta_guard_catches_synthetic_offender(tmp_path):
    bad = tmp_path / "my_new_tile_fill.py"
    bad.write_text("px = read('market_microstructure_1s')\n")
    assert any(b in bad.read_text() for b in BANNED)
