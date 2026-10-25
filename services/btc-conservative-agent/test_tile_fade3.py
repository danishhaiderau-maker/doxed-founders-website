"""GS-FADE3-TP30 PAPER tile (gf3) contract tests. RK PASS 25 Oct 06:24 AEDT; HM nits T6 / T13 / T16 / T23."""
from __future__ import annotations

import ast
import hashlib
import json
import inspect
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

import bitfinex_live_switch as bls
import live_tile_gate as ltg
import order_model
import paper_fill
import quote_trigger
import tile_fade3 as tf
import tile_fill

HERE = Path(__file__).resolve().parent
SIM = HERE / "fixtures" / "fade3_sim"
# T16: the sim sources the port is checked against, pinned byte-for-byte.
FEAT_SHA256 = "97b8ec661d32ae095797dbbb9134fa62b8efc89f5557e0c748db7cfead8bbe56"
FAMILY_SHA256 = "a733fe3af0bbe37a6aa8bc6d193bad54ec4f6b85f01b49f059e25b6b44b8cd45"
SIM_NPZ_LITERAL = "/workspace/grok-strategist/scratch/sim-20261023/data.npz"


# ------------------------------------------------------------------ T16 + E8 parity
def test_t16_sim_sources_pinned():
    assert hashlib.sha256((SIM / "feat.py").read_bytes()).hexdigest() == FEAT_SHA256
    assert hashlib.sha256((SIM / "family.py").read_bytes()).hexdigest() == FAMILY_SHA256
    assert SIM_NPZ_LITERAL in (SIM / "feat.py").read_text()
    assert '("E8","FADE","ALL",3)' in (SIM / "family.py").read_text()


def _synth(seed=8):
    """Seeded synthetic tape (the sim's data.npz is not in the repo): 7 h of 1 s rows, sparse 1 s gaps, four
    injected flow/momentum/imbalance bursts so both E8 sides fire."""
    r = np.random.default_rng(seed)
    N, s0 = 7 * 3600, 1792263600
    S = np.delete(np.arange(s0, s0 + N, dtype=np.int64), r.choice(N, 300, replace=False))
    n = len(S)
    step, flowb = r.normal(0, 0.6, n), r.normal(0, 1, n)
    ev = [int(n * f) for f in (0.88, 0.91, 0.94, 0.97)]
    for k, e in enumerate(ev):
        sg = 1 if k % 2 == 0 else -1
        step[e - 300:e] += sg * 0.25
        flowb[e - 180:e] += sg * 6
    mid = np.round(65000 * np.exp(np.cumsum(step) / 1e4), 1)
    B, A = np.zeros((n, 10, 2)), np.zeros((n, 10, 2))
    for k in range(10):
        B[:, k, 0], A[:, k, 0] = mid - 0.5 - k, mid + 0.5 + k
        B[:, k, 1], A[:, k, 1] = r.uniform(0.1, 1, n), r.uniform(0.1, 1, n)
    for k, e in enumerate(ev):
        (A if k % 2 == 0 else B)[e - 20:e + 5, :5, 1] *= 6
    T, tid = [], 1
    for i in range(n):
        for _ in range(r.poisson(2)):
            T.append((tid, (S[i] + r.uniform(0, 1)) * 1000, r.normal(0.02 * flowb[i], 0.05), mid[i]))
            tid += 1
    T = np.array(sorted(T, key=lambda x: (x[1], x[0])))
    D = np.c_[S, mid * (1 + r.normal(2e-4, 2e-5, n)), mid]
    return S, B, A, T, D


def test_e8_parity_with_sim_feat_family(tmp_path):
    S, B, A, T, D = _synth()
    npz = tmp_path / "data.npz"
    np.savez_compressed(npz, S=S, B=B, A=A, T=T, D=D)
    (tmp_path / "feat.py").write_text((SIM / "feat.py").read_text().replace(SIM_NPZ_LITERAL, str(npz)))
    (tmp_path / "family.py").write_text((SIM / "family.py").read_text())
    out = tmp_path / "sim_out.npz"
    code = ("from family import *\nL,Sh=entry_signals('FADE','ALL',3)\n"
            f"np.savez({str(out)!r},L=L,Sh=Sh,z3=FZ[3],mom5=mom5,ATR30=ATR30,BZ=BZ,imb5=imb5,spread=spread)\n")
    subprocess.run([sys.executable, "-c", code], cwd=tmp_path, check=True, timeout=300)
    sim = np.load(out)
    F = tf.compute_features(S, B, A, T, D)
    L, Sh = tf.entry_signals_e8(F)
    for k in ("z3", "mom5", "ATR30", "BZ", "imb5", "spread"):
        np.testing.assert_array_equal(F[k], sim[k], err_msg=k)
    np.testing.assert_array_equal(L, sim["L"])
    np.testing.assert_array_equal(Sh, sim["Sh"])
    assert L.sum() > 0 and Sh.sum() > 0  # both sides exercised


def test_ffill_matches_pandas_semantics():
    x = np.array([np.nan, 1.0, np.nan, np.nan, 3.0, np.nan])
    np.testing.assert_array_equal(tf._ffill(x), [np.nan, 1, 1, 1, 3, 3])


# ------------------------------------------------------------------ identity / T13 / costs / T23
def test_identity_and_spec_constants():
    assert (tf.TILE_KEY, tf.TILE_KEY_CODE, tf.DISPLAY_SLOT) == ("gf3", 8, 6)
    assert ltg.TILE_KEY_CODES["gf3"] == 8 and 8 not in ltg.RESERVED_TILE_KEY_CODES.values()
    assert ltg.decode_cid(ltg.encode_cid("gf3", 12345)) == "gf3"
    assert (tf.TP_BP, tf.HARD_STOP_BP, tf.EXCHANGE_STOP_BP, tf.TIME_EXIT_S, tf.COOLDOWN_S, tf.NOTIONAL_USD) == \
        (30.0, 25.0, 35.0, 1200, 60.0, 25.0)
    assert (tf.Z_ENTRY, tf.MOM_ATR_MULT, tf.IMB_MIN, tf.BZ_LIMIT, tf.SPREAD_MAX_BP, tf.FLOW_W_MIN) == \
        (2.5, 2.0, 0.2, 2.0, 2.0, 3)
    assert tf.ENTRY_CAP_BP == 5.0 and tf.LIQ_MODEL == "NONE_NOT_ESTIMATED"
    assert tf.FEE_MODEL == "NO_TRADING_FEE" and tf.FUNDING_MODEL == "IGNORED"


def test_t13_gate_tile_constants():
    assert ltg.G6_GATED_TILE == "gf3"
    assert ltg.G4C_DISARMED_TILES == ("gs1", "gf3")
    assert ltg.G6_GATED_TILE != "gs6" and "gs6" not in ltg.G4C_DISARMED_TILES


def test_t23_no_code_path_changes_the_18bp_margin():
    """E10 is a process rule: the gf3 module carries a note and assigns no margin / leverage value."""
    assert "18 bp" in tf.MARGIN_BP_NOTE
    tree = ast.parse(inspect.getsource(tf))
    names = {t.id for n in ast.walk(tree) if isinstance(n, ast.Assign) for t in n.targets if isinstance(t, ast.Name)}
    assert not {n for n in names if "MARGIN" in n and n != "MARGIN_BP_NOTE"} and not {n for n in names if "LEVERAGE" in n}


def test_funding_ignored_and_no_forbidden_price_source():
    src = inspect.getsource(tf)
    tree = ast.parse(src)
    assert not [n for n in ast.walk(tree) if isinstance(n, ast.Attribute) and "funding" in n.attr.lower()]
    forbidden = ("ticker", "candles", "book_tape_1s", "microstructure_1s")
    allowed = set(forbidden)
    for n in ast.walk(tree):
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and n.value not in allowed:
            for bad in forbidden:
                assert bad not in n.value.lower() or n is tree.body[0].value, (bad, n.value)
        if isinstance(n, (ast.Name, ast.Attribute)):
            ident = n.id if isinstance(n, ast.Name) else n.attr
            assert not any(b in ident.lower() for b in ("ticker", "candle", "microstructure", "book_tape")), ident


# ------------------------------------------------------------------ A5 rest fill (shared helper, one TTL)
def _tr(ts, px, amt=0.01):
    return {"ts": ts, "px": px, "amt": amt, "id": int(ts * 1000), "recv_s": int(ts)}


def test_a5_one_ttl_shared_with_order_model():
    assert paper_fill.REST_TTL_S == order_model.DEFAULT_TAKER_TTL_S == 3.0
    assert paper_fill.REST_RULE_A5 == "A5_TRADE_THROUGH"


def test_a5_strictly_beyond_window_and_limit_price():
    t0, lim = 100.0, 65000.0
    trades = [_tr(100.0, 64990), _tr(100.5, 65000.0), _tr(101.0, 64999.0, 0.004), _tr(102.0, 64998.0, 0.01),
              _tr(103.5, 64000.0)]
    r = paper_fill.rest_fill("LONG", 0.01, lim, t0, trades)
    assert r["status"] == "FILLED" and r["price"] == lim and abs(r["filled_qty"] - 0.01) < 1e-12
    assert r["fill_ts"] == 102.0 and r["rest_rule"] == "A5_TRADE_THROUGH" and r["rest_ttl_s"] == 3.0
    p = paper_fill.rest_fill("LONG", 0.01, lim, t0, trades[:3])
    assert p["status"] == "PARTIAL" and abs(p["filled_qty"] - 0.004) < 1e-12
    n = paper_fill.rest_fill("LONG", 0.01, lim, t0, [_tr(100.0, 1.0), _tr(101.0, 65000.0), _tr(103.01, 1.0)])
    assert n["status"] == paper_fill.NO_FILL_REST_TTL and n["price"] is None and n["filled_qty"] == 0
    s = paper_fill.rest_fill("SHORT", 0.01, lim, t0, [_tr(101.0, 65001.0, -0.02)])
    assert s["status"] == "FILLED" and s["price"] == lim


def test_a5_rest_site_is_the_one_shared_helper():
    assert paper_fill.REST_FILL_SITES[tf.LANE] == (("tile_fill.py", "price_entry_rest"),)
    assert paper_fill.rest_site_violations((HERE / "tile_fill.py").read_text(), "price_entry_rest") == []
    bad = "def price_entry_rest(o):\n    return paper_fill.rest_fill(o['side'], 1, 2, 3, quote_trigger.ticker_between(0, 1))\n"
    assert paper_fill.rest_site_violations(bad, "price_entry_rest")
    assert tf.LANE in paper_fill.TILE_FILL_SITES


# ------------------------------------------------------------------ live gate: paper only
def test_gf3_refused_on_every_arm_path(monkeypatch):
    monkeypatch.setenv("LIVE_HM_GATE_FADE3", "PASS:should-not-matter")
    assert ltg.paper_only_arm_block(tf.LANE) == ltg.DENY_PAPER_ONLY_TILE
    assert ltg.paper_only_arm_block(None, "gf3") == ltg.DENY_PAPER_ONLY_TILE
    assert ltg.paper_only_arm_block("FAMILY_GS01_XV_PREMIUM_ATR_TP", "gs1") is None
    ok, why, _ = ltg.admit_entry(lane=tf.LANE, trade_id="gf3-1", now=1.0, chains=[], mirror={}, decisions=[],
                                 armed_lanes=[], entry_margin_usd=0.25, caps={}, book={})
    assert not ok and why == [ltg.DENY_PAPER_ONLY_TILE]
    assert bls.PAPER_ONLY_LANES == ltg.PAPER_ONLY_LANES and bls.DENY_PAPER_ONLY_TILE == ltg.DENY_PAPER_ONLY_TILE
    src = (HERE / "bot.py").read_text()
    i = src.index("def _live_guardian_tile_arm_block")
    body = src[i:i + 1500]
    assert body.index("paper_only_arm_block") < body.index("arm_gate_block(")


def test_live_switch_denies_gf3_even_operator_eligible(tmp_path):
    sw = bls.BitfinexLiveSwitch(tmp_path / "switch.json")
    ev = sw.evaluate(tf.LANE, global_arm={"force_paper_mode": False, "live_armed": True}, operator_eligible=True)
    assert bls.DENY_PAPER_ONLY_TILE in ev["denials"] and not ev.get("eligible")


def test_live_hm_gate_fade3_not_set_anywhere_in_repo_config():
    root = HERE.parent.parent
    for p in [*root.glob("**/fly*.toml"), *(root / ".github" / "workflows").glob("*.yml")]:
        if "node_modules" in p.parts:
            continue
        assert "LIVE_HM_GATE_FADE3" not in p.read_text(errors="ignore"), p


# ------------------------------------------------------------------ countability / G4 / labels
PARITY = {tf.E8_REAL_PARITY_ENV: "PASS:test-parity"}
CLOSE_OK = {"event": "CLOSE", "pool_ready_at_write": True, "evidence_status": "COMPLETE"}


def test_g4_bands():
    assert [tf.g4_band(x) for x in (None, 28, 27.9, 15.01, 15, 3)] == [None, "a", "b", "b", "c'", "c'"]
    assert tf.countability(CLOSE_OK, a5_verified_now=True, latest_real_l_bp=30, clamp_hit=False, env=PARITY)["countable"]
    assert tf.countability(CLOSE_OK, a5_verified_now=True, latest_real_l_bp=20, clamp_hit=False, env=PARITY)["countable"]
    c = tf.countability(CLOSE_OK, a5_verified_now=True, latest_real_l_bp=20, clamp_hit=True, env=PARITY)
    assert not c["countable"] and c["non_countable_reasons"] == ["G4B_CLAMP_HIT"]
    c = tf.countability(CLOSE_OK, a5_verified_now=True, latest_real_l_bp=15, clamp_hit=False, env=PARITY)
    assert c["non_countable_reasons"] == ["G4C_L_LE_15BP"] and c["liq_model"] == "NONE_NOT_ESTIMATED"
    assert "G4_NO_REAL_L" in tf.countability(CLOSE_OK, a5_verified_now=True, latest_real_l_bp=None,
                                             clamp_hit=False, env=PARITY)["non_countable_reasons"]


def test_t6_time_exit_with_pool_n_below_10_is_non_countable():
    entry = {"delay_source_kind": "live_ack", "pool_n": 12, "delay_unverified": False}
    exit_ = {"delay_source_kind": "live_ack", "pool_n": 9, "delay_unverified": True}
    assert not tf.pool_ready(entry, exit_)
    assert tf.pool_ready(entry, {"delay_source_kind": "resident", "pool_n": None})
    row = {**CLOSE_OK, "exit_reason": "TIME_EXIT", "pool_ready_at_write": tf.pool_ready(entry, exit_)}
    c = tf.countability(row, a5_verified_now=True, latest_real_l_bp=30, clamp_hit=False, env=PARITY)
    assert not c["countable"] and "WRITTEN_BEFORE_POOL_N10" in c["non_countable_reasons"]


def test_no_back_stamp():
    early = {**CLOSE_OK, "pool_ready_at_write": False}
    for r in tf.recount([early, {"event": "OPEN"}], a5_verified_now=True, latest_real_l_bp=40, clamp_hit=False, env=PARITY):
        assert r["pool_ready_at_write"] is False and not r["countable"]
    assert len(tf.recount([early, {"event": "OPEN"}], a5_verified_now=True, latest_real_l_bp=40, clamp_hit=False, env=PARITY)) == 1


def test_a5_verification_needs_marker_and_five_trades():
    assert not tf.a5_verified(10, {})
    assert not tf.a5_verified(4, {tf.A5_VERIFIED_ENV: "VERIFIED:x"})
    assert tf.a5_verified(5, {tf.A5_VERIFIED_ENV: "VERIFIED:x"})
    assert "A5_NOT_VERIFIED" in tf.countability(CLOSE_OK, a5_verified_now=False, latest_real_l_bp=30,
                                                clamp_hit=False, env=PARITY)["non_countable_reasons"]


def test_dashboard_label():
    assert tf.dashboard_label(None, True) == "PAPER, NON-COUNTABLE (warm-up)"
    assert tf.dashboard_label(9, True) == tf.LABEL_WARMUP
    assert tf.dashboard_label(10, False) == tf.LABEL_WARMUP
    assert tf.dashboard_label(10, True) == tf.LABEL_PAPER


# ------------------------------------------------------------------ engine scenarios (injected IO)
class Harness:
    def __init__(self, side="LONG", pool_n=12, exit_px=None, ioc_frac=1.0, rest_status="FILLED"):
        self.rows, self.side, self.calls = [], side, []
        self.exit_px, self.ioc_frac, self.rest_status = exit_px, ioc_frac, rest_status
        self.book, self.trades, self.exit_ok = [], [], True
        src = f"POOLED_LIVE_ACK_CROSS_LANE:LIMIT_IOC_max_n{pool_n}"
        self.eng = tf.Fade3PaperEngine(
            features_fn=self.features, delay_fn=lambda leg: (1.5, src), write_fn=self.rows.append,
            book_rows_fn=lambda t0, t1: [b for b in self.book if t0 < b["s"] + 1 <= t1],
            trades_fn=lambda t0, t1: [t for t in self.trades if t0 < t["ts"] <= t1],
            entry_fill_fn=self.entry, rest_fill_fn=self.rest, exit_fill_fn=self.exit,
            resident_fill_fn=self.resident, forced_fill_fn=self.exit,
            stamp_fn=lambda s: ({"delay_source_kind": "resident", "pool_n": None, "delay_unverified": False}
                                if s == "EXCHANGE_RESIDENT_NO_DELAY" else
                                {"delay_source_kind": "live_ack", "pool_n": pool_n, "delay_unverified": pool_n < 10}))
        self.sig_ts = 1000.0

    def features(self, now):
        return (self.sig_ts, self.side, {"mid": 65000.0}) if now >= self.sig_ts else (None, None, None)

    def entry(self, side, qty, ts, d, src):
        self.calls.append("entry")
        return {"status": "FILLED" if self.ioc_frac == 1 else "PARTIAL", "price": 65000.0,
                "filled_qty": qty * self.ioc_frac, "fill_ts": ts + d, "cap_px": 65032.5}

    def rest(self, o):
        self.calls.append(("rest", o["limit_px"]))
        if self.rest_status == "NO_FILL":
            return {"status": paper_fill.NO_FILL_REST_TTL, "price": None, "filled_qty": 0.0}
        return {"status": "FILLED", "price": o["limit_px"], "filled_qty": o["qty_remaining"], "fill_ts": o["t_start"] + 2}

    def exit(self, side, qty, ts, d, src):
        self.calls.append(("exit", ts))
        if not self.exit_ok:
            return {"status": "REJECT_NO_BOOK", "price": None, "filled_qty": 0.0}
        return {"status": "FILLED", "price": self.exit_px, "filled_qty": qty, "fill_ts": ts + d}

    def resident(self, side, qty, ts):
        self.calls.append(("resident", ts))
        return {"status": "FILLED", "price": self.exit_px, "filled_qty": qty, "fill_ts": ts}

    def bookrow(self, s, bid, ask):
        self.book.append({"s": s, "b": [[bid, 1.0]], "a": [[ask, 1.0]]})


def test_engine_tp_exit_on_book_side_and_cooldown():
    h = Harness(exit_px=65196.0)
    out = h.eng.step(1000.0)
    assert out[0]["event"] == "OPEN" and out[0]["tile_key"] == "gf3" and out[0]["display_slot"] == 6
    tp = out[0]["tp_px"]
    h.bookrow(1003, tp - 1, tp + 1)        # bid below TP: no trigger
    assert h.eng.step(1004.5) == []
    h.bookrow(1004, tp + 0.5, tp + 2)      # executable bid at/through TP
    close = h.eng.step(1006.0)[0]
    assert close["event"] == "CLOSE" and close["exit_reason"] == "TP" and close["exit_trigger"]["trigger_ts"] == 1005.0
    assert close["fee_usd"] == 0.0 and close["funding_model"] == "IGNORED" and close["pool_ready_at_write"] is True
    assert h.eng.step(1007.0) == []        # cooldown 60 s, signal ts unchanged anyway
    assert h.eng.cooldown_until == pytest.approx(1006.5 + 60)


def test_engine_exchange_stop_first_print_resident_zero_delay():
    h = Harness(side="SHORT", exit_px=65230.0)
    h.eng.step(1000.0)
    lvl = h.eng.pos["exch_px"]
    assert lvl == float(f"{65000 * 1.0035:.5g}")
    h.trades.append(_tr(1010.2, lvl + 1))
    h.bookrow(1010, lvl - 50, lvl - 40)  # hard stop (-25 bp on the ask) NOT before the print: ask ready at 1011
    close = h.eng.step(1012.0)[0]
    assert close["exit_reason"] == "EXCHANGE_STOP" and ("resident", 1010.2) in h.calls
    assert close["exit_delay"]["delay_source_kind"] == "resident"


def test_engine_hard_stop_book_side():
    h = Harness(exit_px=64830.0)
    h.eng.step(1000.0)
    hp = h.eng.pos["hard_px"]
    h.bookrow(1020, hp - 0.5, hp + 1)
    close = h.eng.step(1022.0)[0]
    assert close["exit_reason"] == "HARD_STOP" and close["exit_trigger"]["trigger_ts"] == 1021.0


def test_engine_time_exit_and_exits_only_on_real_fills():
    h = Harness(exit_px=65010.0)
    h.eng.step(1000.0)
    te = h.eng.pos["entry_fill_ts"] + 1200
    h.exit_ok = False
    assert h.eng.step(te + 1) == [] and h.eng.pos is not None   # unpriced: still open, nothing written
    h.exit_ok = True
    close = h.eng.step(te + 2)[0]
    assert close["exit_reason"] == "TIME_EXIT" and close["exit_trigger"]["trigger_basis"] == "CLOCK"
    assert ("exit", te) in h.calls


def test_engine_t6_pool_below_10_rows_frozen_non_countable():
    h = Harness(pool_n=7, exit_px=65010.0)
    h.eng.step(1000.0)
    close = h.eng.step(h.eng.pos["entry_fill_ts"] + 1201)[0]
    assert close["exit_reason"] == "TIME_EXIT" and close["pool_ready_at_write"] is False
    assert h.eng.status(env={})["label"] == tf.LABEL_WARMUP and h.eng.min_pool_n is None


def test_engine_rest_remainder_uses_a5_after_window():
    h = Harness(ioc_frac=0.5, exit_px=65010.0)
    assert h.eng.step(1000.0) == []                  # rest window not yet received
    out = h.eng.step(1000.0 + 1.5 + 3.0 + 1.0)
    assert out[0]["event"] == "OPEN" and ("rest", 65032.5) in h.calls
    assert out[0]["entry_px"] == pytest.approx((65000.0 + 65032.5) / 2)
    h2 = Harness(ioc_frac=0.0, rest_status="NO_FILL")
    h2.eng.step(1000.0)
    nf = h2.eng.step(1006.0)
    assert nf[0]["event"] == "ENTRY_NO_FILL" and h2.eng.pos is None


def test_engine_one_position():
    h = Harness(exit_px=65010.0)
    h.eng.step(1000.0)
    h.sig_ts = 1001.0
    assert h.eng.step(1002.0) == [] and h.calls.count("entry") == 1


# ------------------------------------------------------------------ slot-6 registry activation + live feed + route
def test_gf3_is_registry_slot_6_paper_only_and_skipped_by_ai_and_evaluator():
    import combo_pathway_config as c
    spec = c.ACTIVE_TILE_REGISTRY[tf.LANE]
    assert c.ACTIVE_TILE_ORDER.index(tf.LANE) + 1 == tf.DISPLAY_SLOT and spec["id_prefix"] == tf.TILE_KEY
    assert spec["raw_policy_id"] == tf.POLICY_ID and spec["implementation_modules"][0] == "tile_fade3.py"
    assert spec["paper_only"] is True and spec["execution_scope"] == "PAPER_ONLY"
    assert spec["live_copy_eligible"] is False and spec["platform_relay_eligible"] is False
    assert spec["self_simulated_paper"] is True and spec["default_enabled"] is True
    assert c.is_evaluator_clock_lane(tf.LANE) and tf.LANE not in c.evaluator_loop_lanes()
    assert spec["exit_policy"]["exit_order"] == ("HARD_STOP", "EXCHANGE_STOP", "TAKE_PROFIT", "TIME_EXIT")
    ex = spec["exit_policy"]
    assert (ex["hard_stop_bps"], ex["take_profit_bps"], ex["exchange_stop_bps"], ex["max_duration_sec"]) == (
        tf.HARD_STOP_BP, tf.TP_BP, tf.EXCHANGE_STOP_BP, tf.TIME_EXIT_S)
    import live_tile_gate
    assert tf.TILE_KEY in live_tile_gate.PAPER_ONLY_TILE_KEYS
    assert tf.exit_config("x")["raw_policy_id"] == tf.POLICY_ID and tf.dashboard_policy()["filter_chips"][1] == "LIVE REFUSED"


def _feed_files(root, n, s0=1_761_000_000):
    rows = {"b": [], "t": [], "d": []}
    for i in range(n):
        s, px = s0 + i, 100000.0 + (i % 7)
        rows["b"].append({"s": s, "b": [[px - 1 - k, 1.0] for k in range(5)], "a": [[px + 1 + k, 1.0] for k in range(5)]})
        rows["t"].append({"s": s, "tr": [[i, s * 1000 + 1, 0.01, px]]})
        rows["d"].append({"s": s, "deriv": px + 2, "spot": px, "mark": px})
    for name, key in zip(tf.PRICE_SOURCES, "btd"):
        (root / name).write_text("".join(json.dumps(r) + "\n" for r in rows[key]))
    return s0


def test_live_feature_feed_is_incremental_causal_and_pads_books(tmp_path):
    s0 = _feed_files(tmp_path, 900)
    feed = tf.LiveFeatureFeed(str(tmp_path))
    assert tf.LiveFeatureFeed(str(tmp_path / "missing"))(s0 + 10) == (None, None, {})
    ts, side, snap = feed(s0 + 500.5)  # only seconds with s + 1 <= now are complete
    assert ts == s0 + 500 and side is None and snap["mid"] == pytest.approx(100000.0 + 499 % 7)
    assert all(len(v[0]) == 10 for v in feed.book.rows.values())  # 5 levels padded to 10
    with open(tmp_path / tf.PRICE_SOURCES[0], "a") as fh:
        fh.write(json.dumps({"s": s0 + 900, "b": [[1, 1]], "a": [[2, 1]]}) + "\n")
    off = feed.book._offset
    ts, _, _ = feed(s0 + 1000)
    assert ts == s0 + 901 and feed.book._offset > off and len(feed.book.rows) == 901


def test_gf3_route_is_census_fixtured_and_monitored():
    import pathlib
    here = pathlib.Path(__file__).resolve().parent
    fx = json.loads((here / "ci" / "route_census_fields.json").read_text())["routes"]["/api/monitor/tiles/gf3"]
    eng = tf.Fade3PaperEngine(features_fn=lambda now: (None, None, {}), delay_fn=lambda leg: (0.0, "x"),
                              write_fn=lambda row: None)
    assert set(fx["status"]) <= set(eng.status())
    src = (here / "bot.py").read_text()
    assert "@app.route('/api/monitor/tiles/gf3'" in src and '"/api/monitor/tiles/gf3"' in src
    assert 'target=gf3_paper_thread, name="gf3-paper"' in src
    assert tf.read_ledger("/nonexistent/gf3.jsonl") == []


# ------------------------------------------------------------------ HM #877 R2 pins
def test_b1_e8_real_parity_unproven_blocks_every_row():
    for env in ({}, {tf.E8_REAL_PARITY_ENV: ""}, {tf.E8_REAL_PARITY_ENV: "PASS:"}, {tf.E8_REAL_PARITY_ENV: "FAIL:x"},
                {tf.E8_REAL_PARITY_ENV: "pass:x"}):
        c = tf.countability(CLOSE_OK, a5_verified_now=True, latest_real_l_bp=30, clamp_hit=False, env=env)
        assert not c["countable"] and c["non_countable_reasons"] == ["E8_REAL_PARITY_UNPROVEN"]
    assert all("E8_REAL_PARITY_UNPROVEN" in r["non_countable_reasons"]
               for r in tf.recount([CLOSE_OK], a5_verified_now=True, latest_real_l_bp=30, clamp_hit=False, env={}))
    assert tf.countability(CLOSE_OK, a5_verified_now=True, latest_real_l_bp=30, clamp_hit=False, env=PARITY)["countable"]


def test_b2_bot_arm_block_refuses_gf3_with_hm_gate_and_first_l_a(monkeypatch):
    import os as _os
    src = (HERE / "bot.py").read_text()
    a = src.index("def _live_guardian_tile_arm_block")
    b = src.index("\ndef ", a + 10)
    monkeypatch.setenv("LIVE_HM_GATE_FADE3", "PASS:x")
    ns = {"os": _os, "_LIVE_GUARDIAN_STATE": {"g4_first_l": {"outcome": ltg.G4_A}}}
    exec(src[a:b], ns)
    assert ns["_live_guardian_tile_arm_block"]("FAMILY_GS_FADE3_TP30") == ltg.DENY_PAPER_ONLY_TILE


def test_b3_rest_trades_count_only_filled_and_survive_restart():
    h = Harness(ioc_frac=0.5, rest_status="NO_FILL")
    h.eng.step(1000.0)
    h.eng.step(1000.0 + 1.5 + paper_fill.REST_TTL_S + 1.0)
    assert any(r.get("rest_fill") for r in h.rows) and h.eng.rest_trades_n == 0
    assert tf.rest_fill_count(h.rows) == 0
    h2 = Harness(ioc_frac=0.5)
    h2.eng.step(1000.0)
    h2.eng.step(1000.0 + 1.5 + paper_fill.REST_TTL_S + 1.0)
    assert h2.eng.rest_trades_n == 1 == tf.rest_fill_count(h2.rows)
    ledger = h.rows + h2.rows + h2.rows + [{"event": "OPEN", "signal_ts": 7.0, "rest_fill": {"status": "PARTIAL"}}]
    assert tf.rest_fill_count(h2.rows + h2.rows) == 1  # a duplicated row (retried append) counts once
    eng = tf.Fade3PaperEngine(features_fn=lambda now: (None, None, None), delay_fn=lambda leg: (1.5, "x"),
                              write_fn=lambda r: None, ledger_rows=ledger)
    assert eng.rest_trades_n == 2 and eng.status(env={})["a5_rest_trades_n"] == 2


def test_pool_ready_threshold_pinned_at_n10_independent_of_unverified():
    entry = {"delay_source_kind": "live_ack", "pool_n": 12, "delay_unverified": False}
    assert not tf.pool_ready(entry, {"delay_source_kind": "live_ack", "pool_n": 9, "delay_unverified": False})
    assert tf.pool_ready(entry, {"delay_source_kind": "live_ack", "pool_n": 10, "delay_unverified": False})


@pytest.mark.cpu_budget
def test_feature_step_cpu_within_budget_on_full_window(tmp_path):
    import time as _time
    s0 = _feed_files(tmp_path, int(tf.HISTORY_S) + 60)
    feed = tf.LiveFeatureFeed(str(tmp_path))
    end = s0 + int(tf.HISTORY_S) + 1.5
    feed(end - 5)  # first call ingests the files (one-off)
    worst = 0.0
    for k in range(5):
        t0 = _time.process_time()
        feed(end - 4 + k)
        worst = max(worst, _time.process_time() - t0)
    assert worst < tf.STEP_CPU_CI_CEILING_S, worst
    assert tf.STEP_CPU_BUDGET_S == pytest.approx(0.1)
    assert "STEP_CPU_BUDGET_S" in (HERE / "bot.py").read_text()
