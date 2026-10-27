"""GS-FADE3-LOOSE (gf3l) contract: looser gates, shared engine family, paper-only."""
from __future__ import annotations

import numpy as np
import pytest

import bitfinex_live_switch as bls
import live_tile_gate as ltg
import paper_fill
import tile_fade3 as tf
import tile_fade3_loose as loose
import tile_fill


def test_loose_identity_and_thresholds():
    assert (loose.TILE_KEY, loose.TILE_KEY_CODE, loose.DISPLAY_SLOT) == ("gf3l", 9, 6)
    assert ltg.TILE_KEY_CODES["gf3l"] == 9 and 9 not in ltg.RESERVED_TILE_KEY_CODES.values()
    assert ltg.decode_cid(ltg.encode_cid("gf3l", 99)) == "gf3l"
    assert (loose.Z_ENTRY, loose.MOM_ATR_MULT, loose.IMB_MIN, loose.SPREAD_MAX_BP) == (1.75, 1.25, 0.10, 3.0)
    assert (loose.TP_BP, loose.HARD_STOP_BP, loose.EXCHANGE_STOP_BP, loose.TIME_EXIT_S, loose.COOLDOWN_S) == (
        25.0, 25.0, 35.0, 1200, 30.0)
    assert loose.POLICY_ID == (
        "GS_FADE3_E8L_FADE_ALL_W3_Z1P75_MOM1P25ATR_IMB0P1_BZ2|TP25_HARD25_XSTOP35_TIME1200_CAP1")


def test_loose_gates_fire_more_than_e8_on_synth():
    """Mutation-facing: with milder thresholds, LOOSE admits a strict subset-or-superset of E8 on the same features."""
    r = np.random.default_rng(8)
    N, s0 = 7 * 3600, 1792263600
    S = np.delete(np.arange(s0, s0 + N, dtype=np.int64), r.choice(N, 300, replace=False))
    n = len(S)
    step, flowb = r.normal(0, 0.6, n), r.normal(0, 1, n)
    mid = np.round(65000 * np.exp(np.cumsum(step) / 1e4), 1)
    B, A = np.zeros((n, 10, 2)), np.zeros((n, 10, 2))
    for k in range(10):
        B[:, k, 0], A[:, k, 0] = mid - 0.5 - k, mid + 0.5 + k
        B[:, k, 1], A[:, k, 1] = r.uniform(0.1, 1, n), r.uniform(0.1, 1, n)
    T, tid = [], 1
    for i in range(n):
        for _ in range(r.poisson(2)):
            T.append((tid, (S[i] + r.uniform(0, 1)) * 1000, r.normal(0.02 * flowb[i], 0.05), mid[i]))
            tid += 1
    T = np.array(sorted(T, key=lambda x: (x[1], x[0])))
    D = np.c_[S, mid * (1 + r.normal(2e-4, 2e-5, n)), mid]
    F = tf.compute_features(S, B, A, T, D)
    e8_L, e8_Sh = tf.entry_signals_e8(F)
    lo_L, lo_Sh = loose.entry_signals_loose(F)
    # Every E8 fire is a LOOSE fire (LOOSE is strictly weaker gates).
    assert np.all(~e8_L | lo_L) and np.all(~e8_Sh | lo_Sh)
    assert lo_L.sum() + lo_Sh.sum() > e8_L.sum() + e8_Sh.sum()


def test_loose_registry_slot_6_paper_only_shared_fills():
    import combo_pathway_config as c
    spec = c.ACTIVE_TILE_REGISTRY[loose.LANE]
    assert c.ACTIVE_TILE_ORDER.index(loose.LANE) + 1 == loose.DISPLAY_SLOT
    assert spec["id_prefix"] == loose.TILE_KEY and spec["raw_policy_id"] == loose.POLICY_ID
    assert spec["implementation_modules"][0] == "tile_fade3_loose.py"
    assert spec["paper_only"] is True and spec["live_copy_eligible"] is False
    assert spec["platform_relay_eligible"] is False and spec["self_simulated_paper"] is True
    assert spec["default_enabled"] is True
    assert spec["exit_policy"]["exit_order"] == ("HARD_STOP", "EXCHANGE_STOP", "TAKE_PROFIT", "TIME_EXIT")
    assert loose.TILE_KEY in ltg.PAPER_ONLY_TILE_KEYS and loose.LANE in ltg.PAPER_ONLY_LANES
    assert loose.LANE in paper_fill.TILE_FILL_SITES
    assert paper_fill.REST_FILL_SITES[loose.LANE] == (("tile_fill.py", "price_entry_rest"),)
    card = c.tile_card_sections(loose.LANE)
    entry_blob = " ".join(card["entry"])
    risk_blob = " ".join(card["risk"])
    assert "1.75" in entry_blob and "1.25" in entry_blob
    assert "max loss" not in risk_blob.lower()
    assert "$0.25 margin" in risk_blob
    # Mutation proof: dashboard_policy must expose entry (build_static_pathway_lane_specs KeyError).
    dash = loose.dashboard_policy()
    assert "entry" in dash and dash["entry"]["entry_path"] == "TILE_FADE3_LOOSE_SELF_SIMULATED"
    assert dash["exit"]["profile"] == "GS_FADE3_LOOSE_FIRST_TRIGGER_WINS"


def test_loose_refused_on_every_arm_path(monkeypatch):
    monkeypatch.setenv("LIVE_HM_GATE_FADE3", "PASS:should-not-matter")
    assert ltg.paper_only_arm_block(loose.LANE) == ltg.DENY_PAPER_ONLY_TILE
    assert ltg.paper_only_arm_block(None, "gf3l") == ltg.DENY_PAPER_ONLY_TILE
    ok, why, _ = ltg.admit_entry(lane=loose.LANE, trade_id="gf3l-1", now=1.0, chains=[], mirror={}, decisions=[],
                                 armed_lanes=[], entry_margin_usd=0.25, caps={}, book={})
    assert not ok and why == [ltg.DENY_PAPER_ONLY_TILE]
    assert bls.PAPER_ONLY_LANES == ltg.PAPER_ONLY_LANES


def test_loose_engine_uses_shared_variant_and_cooldown_30():
    writes = []
    eng = loose.Fade3PaperEngine(
        features_fn=lambda now: (None, None, {}),
        delay_fn=lambda leg: (0.0, "x"),
        write_fn=writes.append,
    )
    assert eng.v.cooldown_s == 30.0 and eng.v.tp_bp == 25.0 and eng.v.lane == loose.LANE
    assert eng.status()["tile_key"] == "gf3l"


def test_gs06_gone_and_gf3l_wired_in_bot():
    from pathlib import Path
    here = Path(__file__).resolve().parent
    assert not (here / "paper_policy_family_gs06_committed_fade_atr_tp.py").exists()
    src = (here / "bot.py").read_text()
    assert "@app.route('/api/monitor/tiles/gf3l'" in src
    assert 'target=gf3l_paper_thread, name="gf3l-paper"' in src
    assert "FAMILY_GS06_COMMITTED_FADE_ATR_TP" not in src or "retired" in src.lower()
