"""HM R7 A1 (RK 11:53): G6 / G4(c') bind FADE3's own tile key gf3, never GS-06's gs6."""
import live_tile_gate as g


def test_g6_gated_tile_is_fade3_key():
    assert g.G6_GATED_TILE == "gf3"
    assert g.G6_GATED_TILE != "gs6"


def test_g4c_disarmed_tiles_exact():
    assert g.G4C_DISARMED_TILES == ("gs1", "gf3")
    assert "gs6" not in g.G4C_DISARMED_TILES
