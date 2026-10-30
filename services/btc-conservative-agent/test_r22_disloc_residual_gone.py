"""R22 residual proof — DISLOC / DISLOC-FOI (Tiles 6/7, retired 23 Oct 2026).

Executable engine/policy already deleted (#879 / tile67 removal). This file locks the
residual contract for FREEZE remediation item (7): no revival of DISLOC execution paths;
KEEP reserved cids, RETIRED_TILE_LANES tokens, and R5 ledger stream names.
"""
from pathlib import Path

import combo_pathway_config as registry
import live_tile_gate as ltg
import paper_fill

HERE = Path(__file__).resolve().parent
DISLOC = ("FAMILY_DISLOC_BFX_DISLOCATION_1M", "FAMILY_DISLOC_FOI_LONG_1M")


def test_disloc_engines_and_policies_physically_gone():
    for name in (
        "disloc_engine.py",
        "disloc_foi_engine.py",
        "disloc_live_copy.py",
        "paper_policy_family_disloc_bfx_dislocation_1m.py",
        "paper_policy_family_disloc_foi_long_1m.py",
    ):
        assert not (HERE / name).exists(), name


def test_disloc_lanes_retired_not_active_not_fill_sites():
    for lane in DISLOC:
        assert lane in registry.RETIRED_TILE_LANES
        assert lane not in registry.ACTIVE_TILE_ORDER
        assert lane not in registry.ACTIVE_TILE_REGISTRY
        assert lane not in registry.COMBO_LANE_SPECS
        assert lane not in paper_fill.TILE_FILL_SITES


def test_disloc_cids_reserved_forever_not_live_keys():
    assert ltg.RESERVED_TILE_KEY_CODES.get("dsl") == 6
    assert ltg.RESERVED_TILE_KEY_CODES.get("dsf") == 7
    assert "dsl" not in ltg.TILE_KEY_CODES
    assert "dsf" not in ltg.TILE_KEY_CODES
    assert ltg.tile_key_for_trade_id("dsl-1") is None
    assert ltg.tile_key_for_trade_id("dsf-1") is None
