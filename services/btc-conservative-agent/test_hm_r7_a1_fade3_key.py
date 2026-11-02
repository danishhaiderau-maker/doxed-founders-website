"""HM R7 A1 (updated post-R22 #942): FADE3 gf3 retired — G4(c') binds GS-01 only; no gf3/gs6 gated key."""
import live_tile_gate as g


def test_g4c_disarmed_tiles_exact_ctrl_gs01_era():
    # OBSERVED after R22 retire tiles 3–6: FADE3 gone; G4(c') keeps GS-01 disarmed only.
    assert g.G4C_DISARMED_TILES == ("gs1",)
    assert "gf3" not in g.G4C_DISARMED_TILES
    assert "gs6" not in g.G4C_DISARMED_TILES


def test_paper_only_lanes_empty_after_fade3_retire():
    assert g.PAPER_ONLY_LANES == frozenset()
    assert g.PAPER_ONLY_TILE_KEYS == frozenset()


def test_g6_gated_tile_attribute_absent_or_not_fade3():
    # G6 was FADE3-era; must not reintroduce gf3 as a live gate key.
    if hasattr(g, "G6_GATED_TILE"):
        assert g.G6_GATED_TILE not in ("gf3", "gs6")


def test_bitfinex_live_switch_paper_only_lanes_empty():
    import bitfinex_live_switch as bls
    assert bls.PAPER_ONLY_LANES == frozenset()
