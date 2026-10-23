"""Tiles 6/7 removal (23 Oct 2026): renumbering is DISPLAY-ONLY.

Tile number = 1-based position in ACTIVE_TILE_ORDER, computed at read time. Internal keys (lane tokens,
id prefixes, live-switch rows) are not position-derived and must not move when tiles are removed.
"""
from __future__ import annotations

import re
from pathlib import Path

import combo_pathway_config as c

HERE = Path(__file__).resolve().parent
GEN = HERE.parents[1] / "packages/utils/src/generated/tile-registry.generated.ts"

EXPECTED = (
    ("FAMILY_RANDOM_CONTROL_TAKER_90", "rnd", "Tile 1 · CTRL"),
    ("FAMILY_GS01_XV_PREMIUM_ATR_TP", "gs1", "Tile 2 · GS-01"),
    ("FAMILY_GSB1_CVD_DIV_REGIME", "gb1", "Tile 3 · B1"),
    ("FAMILY_GSB2_REGIME_SWITCHER", "gb2", "Tile 4 · B2"),
    ("FAMILY_GS06_COMMITTED_FADE_ATR_TP", "gs6", "Tile 5 · GS-06"),
)
DISLOC = ("FAMILY_DISLOC_BFX_DISLOCATION_1M", "FAMILY_DISLOC_FOI_LONG_1M")


def test_active_order_prefixes_and_labels():
    assert c.ACTIVE_TILE_ORDER == tuple(lane for lane, _, _ in EXPECTED)
    for n, (lane, prefix, label) in enumerate(EXPECTED, start=1):
        assert c.ACTIVE_TILE_REGISTRY[lane]["id_prefix"] == prefix, lane  # key unchanged
        assert c.tile_number(lane) == n and c.tile_display_name(lane) == label, lane


def test_disloc_lanes_retired_and_not_numbered():
    for lane in DISLOC:
        assert lane in c.RETIRED_TILE_LANES and lane not in c.ACTIVE_TILE_ORDER
    prefixes = {c.ACTIVE_TILE_REGISTRY[lane]["id_prefix"] for lane in c.ACTIVE_TILE_ORDER}
    assert not prefixes & {"dsl", "dsf"}


def test_generated_ts_matches_registry():
    if not GEN.exists():
        return  # image build without the website source
    m = re.search(r"ACTIVE_TILE_LANES[^=]*=\s*\[([^\]]*)\]", GEN.read_text(encoding="utf-8"))
    assert m and re.findall(r'"([A-Z0-9_]+)"', m.group(1)) == list(c.ACTIVE_TILE_ORDER)
