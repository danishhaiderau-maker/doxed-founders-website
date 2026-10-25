"""GS-FADE3-TP30 PAPER tile (key gf3, cid code 8, display slot 6). RK PASS 25 Oct 06:24 AEDT.

PAPER ONLY. Self-simulated engine (like the retired DISLOC engine): registry slot 6 in COMBO_EXECUTION_LANES with
signal_clock SELF_SIMULATED_SIGNAL_CLOCK and self_simulated_paper, so the shared AI call and the XVL evaluator loop both
skip it; bot._gf3_thread steps Fade3PaperEngine once a second over LiveFeatureFeed. live_tile_gate.PAPER_ONLY_TILE_KEYS
refuses every arm path; LIVE_HM_GATE_FADE3 stays unset in this PR.

Signal = sim E8 (FADE, ALL regimes, W=3), ported verbatim from the sim's feat.py / family.py
(test_tile_fade3 pins their sha256 and checks parity on a seeded fixture):
  SHORT: z3 >= 2.5 and mom5 >= 2*ATR30 and imb5 <= -0.2 and BZ > -2;  LONG mirrored;  spread <= 2 bp.
Entry: marketable limit capped at touch +-5 bp (tile_fill.entry_fill, the shared IOC walk); any remainder rests at
the cap and is priced ONLY by the shared AMEND1 A5 helper (tile_fill.price_entry_rest -> paper_fill.rest_fill, one
TTL = paper_fill.REST_TTL_S).
Exits (real fills only, the book rule = first book row at/after the trigger, via paper_fill):
  TP +30 bp and bot hard stop -25 bp on the executable book side (quote_trigger.first_level_trigger KIND_TARGET);
  exchange stop -35 bp: first trade print at/through it, fill = first book row at/after it, delay 0
  (tile_fill.exit_resident_fill); time exit 20 min after the entry fill (CLOCK).
Cooldown 60 s, one position, $25 notional. Prices: order book (and trade prints for the exchange-stop trigger and the
A5 rest) only - never the ticker, candles, book_tape_1s or microstructure_1s.
Costs: no trading fee; funding IGNORED (gf3 never calls tile_fill.funding_fields).
Liquidation: liq_model NONE_NOT_ESTIMATED; countability follows G4 (latest real L: (a) >= 28 countable, (b) 15<L<28
countable only without a clamp hit, (c') <= 15 non-countable).
T23 (E10 is a process rule): no code path in this module (or this PR) changes the 18 bp margin.
"""
from __future__ import annotations

import json
import os
import threading
from typing import Any, Callable, Iterable, Mapping, Optional

import numpy as np

import paper_fill
import quote_trigger
import tile_fill

TILE_KEY = "gf3"
TILE_KEY_CODE = 8
DISPLAY_SLOT = 6
LANE = "FAMILY_GS_FADE3_TP30"
# Registry identity (combo_pathway_config spec raw_policy_id; bot._load_tile_policy_modules checks it at boot).
POLICY_ID = "GS_FADE3_E8_FADE_ALL_W3_Z2P5_MOM2ATR_IMB0P2_BZ2|TP30_HARD25_XSTOP35_TIME1200_CAP1"
LEDGER_FILE = "gf3_paper_ledger.jsonl"
TILE_NAME = "GS-FADE3-TP30"
SIGNAL_ID = "E8-FADE-ALL-W3"

Z_ENTRY = 2.5
MOM_ATR_MULT = 2.0
IMB_MIN = 0.2
BZ_LIMIT = 2.0
SPREAD_MAX_BP = 2.0
FLOW_W_MIN = 3
TP_BP = 30.0
HARD_STOP_BP = 25.0
EXCHANGE_STOP_BP = 35.0
TIME_EXIT_S = 20 * 60
COOLDOWN_S = 60.0
NOTIONAL_USD = 25.0
ENTRY_CAP_BP = tile_fill.IOC_CAP_BP  # touch +-5 bp
LIQ_MODEL = "NONE_NOT_ESTIMATED"
FEE_MODEL = "NO_TRADING_FEE"
FUNDING_MODEL = "IGNORED"
MARGIN_BP_NOTE = "18 bp margin unchanged; E10 is a process rule, no code path here alters it (T23)"
PRICE_SOURCES = ("bfx_book_10l_1s.jsonl", "bfx_trades.jsonl", "bfx_deriv_status_1s.jsonl")
HISTORY_S = 6 * 3600 + 15 * 60  # 6 h minute-z window + 31 completed minutes of ATR, with margin

MIN_POOL_N = paper_fill.MIN_POOL_N if hasattr(paper_fill, "MIN_POOL_N") else 10
A5_VERIFIED_MIN_TRADES = 5
A5_VERIFIED_ENV = "GF3_A5_VERIFIED"  # operator marker (A5 deployed + verified); unset in this PR
LABEL_WARMUP = "PAPER, NON-COUNTABLE (warm-up)"
LABEL_PAPER = "PAPER"
G4_A_MIN_BP = 28.0
G4_C_MAX_BP = 15.0


# ------------------------------------------------------------------ features (verbatim port of sim feat.py)
def _ffill(x: np.ndarray) -> np.ndarray:
    """pandas Series.ffill() on a 1-D float array (leading NaN stays NaN)."""
    x = np.asarray(x, dtype=float)
    ok = ~np.isnan(x)
    idx = np.where(ok, np.arange(len(x)), 0)
    np.maximum.accumulate(idx, out=idx)
    out = x[idx]
    out[np.cumsum(ok) == 0] = np.nan
    return out


def compute_features(S, B, A, T, D) -> dict:
    """S (N,) int seconds; B/A (N,10,2) [px, sz]; T (M,4) [id, ms, amt, px] sorted by (ms, id); D (K,>=3) [s, deriv,
    spot, ...] sorted by s. Same arithmetic, same order as the sim's feat.py."""
    S = np.asarray(S, dtype=np.int64)
    B, A = np.asarray(B, dtype=float), np.asarray(A, dtype=float)
    T = np.asarray(T, dtype=float).reshape(-1, 4)
    D = np.asarray(D, dtype=float)
    D = D.reshape(-1, D.shape[-1] if D.ndim == 2 and D.size else 3)
    TC = S + 1.0
    bid0, ask0 = B[:, 0, 0], A[:, 0, 0]
    mid = (bid0 + ask0) / 2
    spread = (ask0 - bid0) / mid * 1e4
    bs, as_ = B[:, :5, 1].sum(1), A[:, :5, 1].sum(1)
    imb5 = (bs - as_) / (bs + as_)
    tts = T[:, 1] / 1000.0
    tamt = T[:, 2]
    cum = np.r_[0, np.cumsum(tamt)]

    def flow(W):
        hi = np.searchsorted(tts, TC, 'right')
        lo = np.searchsorted(tts, TC - W * 60, 'right')
        return cum[hi] - cum[lo]

    def minute_z(x, hours=6):
        mins = (TC // 60).astype(np.int64)
        m0 = mins[0]
        idx = mins - m0
        nm = idx[-1] + 1
        samp = np.full(nm, np.nan)
        last = np.r_[np.diff(idx) > 0, True]
        samp[idx[last]] = x[last]
        ok = ~np.isnan(samp)
        v = np.where(ok, samp, 0)
        c1 = np.r_[0, np.cumsum(ok)]
        c2 = np.r_[0, np.cumsum(v)]
        c3 = np.r_[0, np.cumsum(v * v)]
        L = hours * 60
        hi = idx
        lo = np.maximum(0, idx - L)
        n = c1[hi] - c1[lo]
        s = c2[hi] - c2[lo]
        ss = c3[hi] - c3[lo]
        mu = np.where(n > 0, s / np.maximum(n, 1), np.nan)
        var = np.where(n > 1, (ss - n * mu * mu) / np.maximum(n - 1, 1), np.nan)
        sd = np.sqrt(np.maximum(var, 1e-18))
        z = (x - mu) / sd
        z[(n < L * 0.8)] = np.nan
        return z

    z3 = minute_z(flow(FLOW_W_MIN))
    j300 = np.searchsorted(TC, TC - 300, 'right') - 1
    mom5 = np.where(j300 >= 0, (mid / mid[np.maximum(j300, 0)] - 1) * 1e4, np.nan)
    mins = (TC // 60).astype(np.int64)
    m0 = mins[0]
    idx = mins - m0
    nm = idx[-1] + 1
    hi_ = np.full(nm, -np.inf)
    lo_ = np.full(nm, np.inf)
    cl = np.full(nm, np.nan)
    np.maximum.at(hi_, idx, mid)
    np.minimum.at(lo_, idx, mid)
    last = np.r_[np.diff(idx) > 0, True]
    cl[idx[last]] = mid[last]
    cl = _ffill(cl) if np.isnan(cl).any() else cl
    with np.errstate(invalid="ignore", divide="ignore"):
        rng = np.where(np.isfinite(hi_), (hi_ - lo_) / cl * 1e4, np.nan)
    rng = np.nan_to_num(rng, nan=0.0)
    cr = np.r_[0, np.cumsum(rng)]
    k = idx
    ok30 = k >= 31
    ATR30 = np.where(ok30, (cr[k] - cr[np.maximum(k - 30, 0)]) / 30, np.nan)
    if len(D):
        dj = np.searchsorted(D[:, 0], S, 'right') - 1
        with np.errstate(invalid="ignore", divide="ignore"):
            basis_raw = np.where(dj >= 0, (D[np.maximum(dj, 0), 1] / D[np.maximum(dj, 0), 2] - 1) * 1e4, np.nan)
    else:
        basis_raw = np.full(len(S), np.nan)
    BZ = minute_z(basis_raw)
    return {"S": S, "TC": TC, "mid": mid, "bid0": bid0, "ask0": ask0, "spread": spread, "imb5": imb5, "z3": z3,
            "mom5": mom5, "ATR30": ATR30, "BZ": BZ}


def entry_signals_e8(F: Mapping[str, np.ndarray]):
    """family.entry_signals("FADE", "ALL", 3): returns (LONG mask, SHORT mask)."""
    z, BZ, mom5, ATR30, imb5 = F["z3"], F["BZ"], F["mom5"], F["ATR30"], F["imb5"]
    gate = F["spread"] <= SPREAD_MAX_BP
    base = gate & np.isfinite(z) & np.isfinite(BZ) & np.isfinite(mom5) & np.isfinite(ATR30)
    Sh = base & (z >= Z_ENTRY) & (mom5 >= MOM_ATR_MULT * ATR30) & (imb5 <= -IMB_MIN) & (BZ > -BZ_LIMIT)
    L = base & (z <= -Z_ENTRY) & (mom5 <= -MOM_ATR_MULT * ATR30) & (imb5 >= IMB_MIN) & (BZ < BZ_LIMIT)
    return L, Sh


# ------------------------------------------------------------------ countability (no back-stamp)
def pool_ready(*legs: Mapping[str, Any]) -> bool:
    """Every priced leg's delay pool has n >= MIN_POOL_N and is not UNVERIFIED (resident / stop-rule legs carry no
    pool and do not block)."""
    for st in legs:
        st = st or {}
        kind = st.get("delay_source_kind")
        if kind in ("resident", "stop_rule"):
            continue
        n = st.get("pool_n")
        if st.get("delay_unverified") or n is None or int(n) < MIN_POOL_N:
            return False
    return True


E8_REAL_PARITY_ENV = "GF3_E8_REAL_PARITY"  # PASS:<ref> only after E8 real-data parity passes; unset = no row counts
REST_COUNTED_STATUSES = ("FILLED", "PARTIAL")
STEP_CPU_BUDGET_S = 0.1  # per-step feature CPU alert budget (R8: incremental feed, see LiveFeatureFeed); test-pinned


def e8_real_parity_passed(env: Optional[Mapping[str, str]] = None) -> bool:
    e = os.environ if env is None else env
    v = str(e.get(E8_REAL_PARITY_ENV, "")).strip()
    return v.startswith("PASS:") and len(v) > len("PASS:")


def rest_fill_count(rows: Iterable[Mapping[str, Any]]) -> int:
    """A5 rest trades = ledger entry rows whose rest leg actually FILLED/PARTIAL (NO_FILL_REST_TTL attempts excluded);
    derived from the ledger so it survives a restart. One count per entry (cid, else signal_ts): a duplicated row
    (e.g. a retried append) is not double-counted."""
    seen = set()
    for r in rows:
        if r.get("event") in ("OPEN", "ENTRY_NO_FILL") and \
                str((r.get("rest_fill") or {}).get("status") or "") in REST_COUNTED_STATUSES:
            seen.add(r.get("cid") or ("signal_ts", r.get("signal_ts")))
    return len(seen)


def a5_verified(n_rest_trades: int, env: Optional[Mapping[str, str]] = None) -> bool:
    e = os.environ if env is None else env
    return bool(str(e.get(A5_VERIFIED_ENV, "")).strip()) and int(n_rest_trades or 0) >= A5_VERIFIED_MIN_TRADES


def g4_band(latest_real_l_bp: Optional[float]) -> Optional[str]:
    if latest_real_l_bp is None:
        return None
    L = float(latest_real_l_bp)
    return "a" if L >= G4_A_MIN_BP else ("b" if L > G4_C_MAX_BP else "c'")


def countability(row: Mapping[str, Any], *, a5_verified_now: bool, latest_real_l_bp: Optional[float],
                 clamp_hit: bool, env: Optional[Mapping[str, str]] = None) -> dict:
    """Reasons a closed gf3 row is NON-COUNTABLE. ``pool_ready_at_write`` is frozen on the row when it is written and
    is never recomputed: a row written while any pool was n<10 stays non-countable forever (no back-stamp)."""
    why = []
    if not e8_real_parity_passed(env):
        why.append("E8_REAL_PARITY_UNPROVEN")  # paper may run; nothing counts until E8 real-data parity passes
    if row.get("pool_ready_at_write") is not True:
        why.append("WRITTEN_BEFORE_POOL_N10")  # T6: includes a time exit priced with pool_n < 10
    if not a5_verified_now:
        why.append("A5_NOT_VERIFIED")
    band = g4_band(latest_real_l_bp)
    if band is None:
        why.append("G4_NO_REAL_L")
    elif band == "c'":
        why.append("G4C_L_LE_15BP")
    elif band == "b" and clamp_hit:
        why.append("G4B_CLAMP_HIT")
    if row.get("evidence_status") != "COMPLETE":
        why.append("EVIDENCE_" + str(row.get("evidence_status") or "MISSING"))
    return {"countable": not why, "non_countable_reasons": why, "g4_band": band, "liq_model": LIQ_MODEL}


def dashboard_label(min_pool_n: Optional[int], a5_verified_now: bool) -> str:
    if min_pool_n is None or int(min_pool_n) < MIN_POOL_N or not a5_verified_now:
        return LABEL_WARMUP
    return LABEL_PAPER


# ------------------------------------------------------------------ engine
def _dir_sign(direction: str) -> int:
    return 1 if direction == "LONG" else -1


def _level(entry: float, direction: str, bp: float, favourable: bool) -> float:
    s = _dir_sign(direction) * (1 if favourable else -1)
    return entry * (1 + s * bp / 1e4)


def _exch_stop_px(entry: float, direction: str) -> float:
    return float(f"{_level(entry, direction, EXCHANGE_STOP_BP, False):.5g}")  # Bitfinex 5 significant figures


def _filled(res: Optional[Mapping]) -> bool:
    return bool(res) and res.get("price") is not None and float(res.get("filled_qty") or 0) > 0


class Fade3PaperEngine:
    """Step-driven paper engine. All IO is injected so tests drive it without files:

    features_fn(now) -> (decision_ts, side or None, feature snapshot) for the newest complete second;
    delay_fn(leg) -> (delay_s, delay_source); book_rows_fn(t0, t1) -> 1 s book rows; trades_fn(t0, t1) -> prints;
    write_fn(row) persists a ledger row. Exits are only ever written on a real paper_fill price.
    """

    def __init__(self, *, features_fn: Callable, delay_fn: Callable, write_fn: Callable,
                 book_rows_fn: Optional[Callable] = None, trades_fn: Optional[Callable] = None,
                 entry_fill_fn: Callable = tile_fill.entry_fill, rest_fill_fn: Callable = tile_fill.price_entry_rest,
                 exit_fill_fn: Callable = tile_fill.exit_fill, resident_fill_fn: Callable = tile_fill.exit_resident_fill,
                 forced_fill_fn: Callable = tile_fill.exit_forced_fill, stamp_fn: Callable = paper_fill.delay_stamp,
                 ledger_rows: Iterable[Mapping[str, Any]] = ()):
        self.features_fn, self.delay_fn, self.write_fn = features_fn, delay_fn, write_fn
        self.book_rows_fn = book_rows_fn or (lambda t0, t1: quote_trigger.book_rows_between(t0, t1, paper_fill.book10l_at))
        self.trades_fn = trades_fn or quote_trigger.trades_between
        self.entry_fill_fn, self.rest_fill_fn = entry_fill_fn, rest_fill_fn
        self.exit_fill_fn, self.resident_fill_fn, self.forced_fill_fn = exit_fill_fn, resident_fill_fn, forced_fill_fn
        self.stamp_fn = stamp_fn
        self.pos: Optional[dict] = None
        self.pending: Optional[dict] = None   # entry awaiting its A5 rest window
        self.pending_exit: Optional[dict] = None
        self.cooldown_until = 0.0
        self.last_decision_ts: Optional[float] = None
        self.rest_trades_n = rest_fill_count(ledger_rows)  # seeded from the ledger: survives a restart
        self.pool_n_by_leg: dict = {}   # latest pool_n per delay key (entry / exit); None = unverified
        self._lock = threading.Lock()

    @property
    def min_pool_n(self) -> Optional[int]:
        v = [self.pool_n_by_leg.get(k) for k in ("entry", "exit")]
        return None if any(x is None for x in v) else min(v)

    # -- delay
    def _delay(self, leg: str):
        d, src = self.delay_fn(leg)
        st = self.stamp_fn(src)
        if st.get("delay_source_kind") not in ("resident", "stop_rule"):
            n = st.get("pool_n")
            self.pool_n_by_leg[leg] = None if n is None or st.get("delay_unverified") else int(n)
        return d, src, st

    def step(self, now: float) -> list:
        with self._lock:
            out = []
            if self.pending is not None:
                out += self._settle_entry(now)
            elif self.pos is not None:
                out += self._manage(now)
            elif now >= self.cooldown_until:
                out += self._maybe_enter(now)
            return out

    # -- entry
    def _maybe_enter(self, now: float) -> list:
        ts, side, snap = self.features_fn(now)
        if ts is None or ts == self.last_decision_ts:
            return []
        self.last_decision_ts = ts
        if side not in ("LONG", "SHORT"):
            return []
        d, src, st = self._delay("entry")
        mid = (snap or {}).get("mid")
        if not mid:
            return []
        qty = NOTIONAL_USD / float(mid)
        res = self.entry_fill_fn(side, qty, ts, d, src)
        if res.get("status") == "REJECT_NO_BOOK" or res.get("cap_px") is None:
            self.cooldown_until = ts + COOLDOWN_S
            return [self._row("ENTRY_REJECTED", side=side, signal_ts=ts, entry_fill=res, features=snap)]
        filled = float(res.get("filled_qty") or 0) if res.get("price") is not None else 0.0
        self.pending = {"side": side, "signal_ts": ts, "qty": qty, "ioc": res, "ioc_filled": filled,
                        "limit_px": res["cap_px"], "t_start": float(res.get("fill_ts") or ts + float(d or 0)),
                        "entry_delay": st, "features": snap}
        return self._settle_entry(now)

    def _settle_entry(self, now: float) -> list:
        p = self.pending
        rem = p["qty"] - p["ioc_filled"]
        rest = None
        if rem > 1e-12:
            if now < p["t_start"] + paper_fill.REST_TTL_S + 1.0:  # the rest window must be fully received
                return []
            rest = self.rest_fill_fn({"side": p["side"], "qty_remaining": rem, "limit_px": p["limit_px"],
                                      "t_start": p["t_start"]})
            if str((rest or {}).get("status") or "") in REST_COUNTED_STATUSES:
                self.rest_trades_n += 1
        self.pending = None
        legs = [(p["ioc_filled"], p["ioc"].get("price"), p["ioc"].get("fill_ts"))]
        if _filled(rest):
            legs.append((float(rest["filled_qty"]), float(rest["price"]), rest.get("fill_ts")))
        q = sum(x[0] for x in legs if x[1] is not None)
        if q <= 0:
            self.cooldown_until = p["signal_ts"] + COOLDOWN_S
            return [self._row("ENTRY_NO_FILL", side=p["side"], signal_ts=p["signal_ts"], entry_fill=p["ioc"],
                              rest_fill=rest, features=p["features"])]
        px = sum(x[0] * x[1] for x in legs if x[1] is not None) / q
        fill_ts = max(float(x[2]) for x in legs if x[1] is not None and x[2] is not None)
        self.pos = {"side": p["side"], "qty": q, "entry_px": px, "entry_fill_ts": fill_ts,
                    "signal_ts": p["signal_ts"], "tp_px": _level(px, p["side"], TP_BP, True),
                    "hard_px": _level(px, p["side"], HARD_STOP_BP, False), "exch_px": _exch_stop_px(px, p["side"]),
                    "scan_ts": fill_ts, "entry_fill": p["ioc"], "rest_fill": rest, "entry_delay": p["entry_delay"],
                    "features": p["features"]}
        return [self._row("OPEN", **{k: v for k, v in self.pos.items() if k not in ("features",)},
                          features=p["features"])]

    # -- exits
    def _manage(self, now: float) -> list:
        p = self.pos
        if self.pending_exit is not None:
            return self._finish_exit(now)
        t0, t1 = p["scan_ts"], float(now)
        if t1 <= t0:
            return []
        trades = self.trades_fn(t0, t1)
        rows = self.book_rows_fn(t0, t1)
        cands = []
        ex = quote_trigger.first_level_trigger(p["side"], quote_trigger.KIND_STOP, p["exch_px"], t0, t1,
                                               book_rows=rows, trades=trades)
        if ex:
            cands.append((float(ex["trigger_ts"]), 0, "EXCHANGE_STOP", ex))
        hs = self._book_adverse(p["side"], p["hard_px"], t0, t1, rows)
        if hs:
            cands.append((float(hs["trigger_ts"]), 1, "HARD_STOP", hs))
        tp = quote_trigger.first_level_trigger(p["side"], quote_trigger.KIND_TARGET, p["tp_px"], t0, t1,
                                               book_rows=rows)
        if tp:
            cands.append((float(tp["trigger_ts"]), 2, "TP", tp))
        te = p["entry_fill_ts"] + TIME_EXIT_S
        if t0 < te <= t1:
            cands.append((te, 3, "TIME_EXIT", {"trigger_ts": te, "trigger_basis": "CLOCK"}))
        p["scan_ts"] = t1
        if not cands:
            return []
        ts, _, why, ev = min(cands, key=lambda c: (c[0], c[1]))
        if why == "EXCHANGE_STOP":
            self.pending_exit = {"reason": why, "trigger_ts": ts, "event": ev, "resident": True,
                                 "delay": self.stamp_fn("EXCHANGE_RESIDENT_NO_DELAY")}
        else:
            d, src, st = self._delay("exit")
            self.pending_exit = {"reason": why, "trigger_ts": ts, "event": ev, "resident": False,
                                 "delay_s": d, "delay_source": src, "delay": st}
        return self._finish_exit(now)

    @staticmethod
    def _book_adverse(side, level, t0, t1, rows):
        """Bot hard stop on the executable book side (RK 17:24: TP / BE / bot hard stop use book rows)."""
        for e in quote_trigger.exit_events(side, t0, t1, book_rows=rows, include_trades=False, include_book=True):
            if quote_trigger.level_reached(side, quote_trigger.KIND_STOP, level, e["px"]):
                return quote_trigger.trigger_stamp(e, t0, t1, kind=quote_trigger.KIND_STOP, level=level)
        return None

    def _finish_exit(self, now: float) -> list:
        p, x = self.pos, self.pending_exit
        if x["resident"]:
            res = self.resident_fill_fn(p["side"], p["qty"], x["trigger_ts"])
        else:
            res = self.exit_fill_fn(p["side"], p["qty"], x["trigger_ts"], x["delay_s"], x["delay_source"])
            if not _filled(res) and now >= x["trigger_ts"] + tile_fill.EXIT_FORCE_AFTER_S:
                res = self.forced_fill_fn(p["side"], p["qty"], x["trigger_ts"], x["delay_s"], x["delay_source"])
        if not _filled(res):
            return []  # no real fill yet: the position stays open (exits only on real fills)
        exit_px = float(res["price"])
        pnl_bp = _dir_sign(p["side"]) * (exit_px / p["entry_px"] - 1) * 1e4
        self.pos, self.pending_exit = None, None
        self.cooldown_until = float(res.get("fill_ts") or x["trigger_ts"]) + COOLDOWN_S
        ready = pool_ready(p["entry_delay"], x["delay"])
        evidence = "COMPLETE" if res.get("status") == "FILLED" and p["entry_fill"].get("status") in ("FILLED", "PARTIAL") \
            and not res.get("unverified") else "UNVERIFIED"
        return [self._row("CLOSE", side=p["side"], qty=p["qty"], entry_px=p["entry_px"],
                          entry_fill_ts=p["entry_fill_ts"], signal_ts=p["signal_ts"], exit_reason=x["reason"],
                          exit_trigger=x["event"], exit_fill=res, exit_px=exit_px, pnl_bp=pnl_bp,
                          pnl_usd=pnl_bp / 1e4 * NOTIONAL_USD, entry_delay=p["entry_delay"], exit_delay=x["delay"],
                          rest_fill=p["rest_fill"], pool_ready_at_write=ready, evidence_status=evidence,
                          countable_at_write=False)]

    def _row(self, event: str, **kw) -> dict:
        row = {"event": event, "lane": LANE, "tile_key": TILE_KEY, "tile_key_code": TILE_KEY_CODE,
               "display_slot": DISPLAY_SLOT, "tile": TILE_NAME, "signal_id": SIGNAL_ID, "paper_only": True,
               "liq_model": LIQ_MODEL, "fee_model": FEE_MODEL, "funding_model": FUNDING_MODEL, "fee_usd": 0.0,
               "rest_rule": paper_fill.REST_RULE_A5, "rest_ttl_s": paper_fill.REST_TTL_S, **kw}
        self.write_fn(row)
        return row

    def status(self, *, latest_real_l_bp: Optional[float] = None, env: Optional[Mapping[str, str]] = None) -> dict:
        a5 = a5_verified(self.rest_trades_n, env)
        return {"tile": TILE_NAME, "tile_key": TILE_KEY, "display_slot": DISPLAY_SLOT, "lane": LANE,
                "label": dashboard_label(self.min_pool_n, a5), "paper_only": True, "min_pool_n": self.min_pool_n,
                "a5_rest_trades_n": self.rest_trades_n, "a5_verified": a5, "g4_band": g4_band(latest_real_l_bp),
                "open_position": bool(self.pos), "liq_model": LIQ_MODEL, "margin_note": MARGIN_BP_NOTE}


def recount(rows: Iterable[Mapping[str, Any]], **kw) -> list:
    """Re-evaluate closed rows for display. pool_ready_at_write is read, never rewritten (no back-stamp)."""
    return [{**r, **countability(r, **kw)} for r in rows if r.get("event") == "CLOSE"]


def read_ledger(path: str) -> list:
    """All parseable rows of the gf3 ledger ([] before the first write); read-only."""
    rows = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    continue
    except FileNotFoundError:
        pass
    return rows


def append_jsonl(path: str) -> Callable[[Mapping], None]:
    def _w(row: Mapping) -> None:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, default=str, separators=(",", ":")) + "\n")
    return _w


# ------------------------------------------------------------------ registry policy interface (dashboard / analyzer)
def exit_config(analyzer_sync_id: str) -> dict:
    """Per-trade exit snapshot (bot.get_exit_config_snapshot) for the self-simulated gf3 rules."""
    return {"policy_snapshot_schema": "exit_policy_v1", "policy_source": "btc-conservative-agent",
            "policy_version": POLICY_ID, "raw_policy_id": POLICY_ID, "analyzer_sync_id": analyzer_sync_id,
            "exit_profile_id": POLICY_ID.split("|", 1)[1], "family": "GS_FADE3_FIRST_TRIGGER_WINS",
            "take_profit_bp": TP_BP, "hard_stop_bp": HARD_STOP_BP, "exchange_stop_bp": EXCHANGE_STOP_BP,
            "path_end_sec": TIME_EXIT_S, "partial_reduction_required": False}


def dashboard_policy() -> dict:
    """Static pathway card view (bot.build_static_pathway_lane_specs)."""
    return {
        "filter_chips": ["PAPER ONLY", "LIVE REFUSED", f"Cap touch +-{ENTRY_CAP_BP:g} bp", f"TP {TP_BP:g} bp",
                         f"Hard stop {HARD_STOP_BP:g} bp", f"Exchange stop {EXCHANGE_STOP_BP:g} bp",
                         f"{TIME_EXIT_S // 60}m cap"],
        "entry": {"trigger": f"{SIGNAL_ID}: 3-min flow z-score spike faded (self-simulated 1 s engine)",
                  "entry_path": "TILE_FADE3_SELF_SIMULATED", "fill_path": "TILE_FILL_IOC_CAP_THEN_A5_REST",
                  "execution": "Own paper ledger; every live arm path refused",
                  "chase_detail": f"No chase; remainder rests at the cap for {paper_fill.REST_TTL_S:g} s (A5)"},
        "strategy_detail": [f"E8 FADE ALL W={FLOW_W_MIN}: z3 >= {Z_ENTRY:g}, mom5 >= {MOM_ATR_MULT:g} ATR30, "
                            f"imb5 against >= {IMB_MIN:g}, |BZ| guard {BZ_LIMIT:g}, spread <= {SPREAD_MAX_BP:g} bp",
                            f"First trigger wins: TP +{TP_BP:g} / hard -{HARD_STOP_BP:g} / exchange -"
                            f"{EXCHANGE_STOP_BP:g} bp / {TIME_EXIT_S // 60}m; {COOLDOWN_S:g} s cooldown, one open, "
                            f"${NOTIONAL_USD:g}",
                            "PAPER ONLY: every live arm path refused (live_tile_gate.PAPER_ONLY_TILE_KEYS)"],
        "exit": {"profile": "GS_FADE3_FIRST_TRIGGER_WINS", "take_profit_bp": TP_BP, "hard_stop_bp": HARD_STOP_BP,
                 "exchange_stop_bp": EXCHANGE_STOP_BP, "fixed_time_exit": f"{TIME_EXIT_S // 60}m"},
    }


# ------------------------------------------------------------------ live feature feed (collector tails, read-only)
class _JsonlHistory:
    """Incremental byte-offset reader of one collector jsonl keeping HISTORY_S seconds of parsed rows by "s".

    A shrink / inode change (rotation) resets to the new file's tail; the tile then re-warms (z needs ~4.8 h)."""

    def __init__(self, path: str, parse: Callable[[dict], Any], tail_bytes: int) -> None:
        self.path, self.parse, self.tail_bytes = path, parse, tail_bytes
        self.rows: dict = {}
        self._ino = None
        self._offset = 0
        self._partial = b""
        self.gen = 0  # bumps on any content change (new / overwritten second, eviction, reset)
        self.resets = 0  # bumps when rows were replaced wholesale (rotation)
        self.new_secs: list = []  # seconds written by the last sync(), in file order (may repeat / go backwards)
        self.floor: Optional[int] = None  # eviction floor applied by the last sync()

    def sync(self) -> None:
        try:
            st = os.stat(self.path)
        except OSError:
            return
        self.new_secs = []
        if self._ino != st.st_ino or st.st_size < self._offset:
            self.rows, self._ino, self._partial = {}, st.st_ino, b""
            self.resets += 1
            self.gen += 1
            self._offset = max(0, st.st_size - self.tail_bytes)
            drop_first = self._offset > 0
        else:
            drop_first = False
        if st.st_size == self._offset:
            return
        with open(self.path, "rb") as fh:
            fh.seek(self._offset)
            chunk = fh.read(st.st_size - self._offset)
        self._offset += len(chunk)
        lines = (self._partial + chunk).split(b"\n")
        self._partial = lines.pop()
        for line in lines[1:] if drop_first else lines:
            try:
                r = json.loads(line)
                sec = int(r["s"])
                val = self.parse(r)
            except (ValueError, KeyError, TypeError, IndexError):
                continue
            if val is not None:
                self.rows[sec] = val
                self.new_secs.append(sec)
        if self.new_secs:
            self.gen += 1
        if self.rows:
            floor = max(self.rows) - HISTORY_S
            self.floor = floor
            stale = [k for k in self.rows if k < floor]
            for sec in stale:
                del self.rows[sec]
            if stale:
                self.gen += 1


def _parse_book(r: dict):
    b, a = r.get("b") or [], r.get("a") or []
    if not b or not a:
        return None
    pad = [[np.nan, 0.0]] * 10
    rx = r.get("receive_ts")
    return ((list(b) + pad)[:10], (list(a) + pad)[:10], float(rx) if rx else None)


def _parse_trades(r: dict):
    return [[float(t[0]), float(t[1]), float(t[2]), float(t[3])] for t in r.get("tr") or []]


def _parse_deriv(r: dict):
    d, sp = r.get("deriv"), r.get("spot")
    return (float(d), float(sp)) if d and sp else None


class _GrowBuf:
    """Append-only, front-trimmable column store (amortised O(1) append, no per-tick rebuild / re-sort)."""

    def __init__(self, shapes: Mapping[str, tuple]) -> None:
        self.shapes = dict(shapes)
        self.clear()

    def clear(self) -> None:
        self.cols = {k: np.empty((0,) + shp, dtype=np.int64 if k == "key" else float) for k, shp in self.shapes.items()}
        self.lo, self.hi = 0, 0

    def __len__(self) -> int:
        return self.hi - self.lo

    def view(self, k: str) -> np.ndarray:
        return self.cols[k][self.lo:self.hi]

    def set_all(self, data: Mapping[str, np.ndarray]) -> None:
        self.cols = {k: np.array(data[k], dtype=np.int64 if k == "key" else float).reshape((-1,) + self.shapes[k])
                     for k in self.shapes}
        self.lo, self.hi = 0, len(self.cols["key"])

    def append(self, data: Mapping[str, np.ndarray]) -> None:
        n = len(data["key"])
        if not n:
            return
        cap = len(self.cols["key"])
        if self.hi + n > cap:
            live = self.hi - self.lo
            new_cap = max(64, 2 * (live + n))
            for k, shp in self.shapes.items():
                arr = np.empty((new_cap,) + shp, dtype=self.cols[k].dtype)
                arr[:live] = self.cols[k][self.lo:self.hi]
                self.cols[k] = arr
            self.lo, self.hi = 0, live
        for k in self.shapes:
            self.cols[k][self.hi:self.hi + n] = np.asarray(data[k]).reshape((n,) + self.shapes[k])
        self.hi += n

    def keep(self, mask: np.ndarray) -> None:
        """Drop rows where mask is False (prefix drop is O(1); otherwise one compaction)."""
        if mask.all():
            return
        idx = np.flatnonzero(mask)
        if idx.size and idx[-1] - idx[0] + 1 == idx.size:
            self.lo, self.hi = self.lo + int(idx[0]), self.lo + int(idx[-1]) + 1
            return
        self.set_all({k: self.view(k)[mask] for k in self.shapes})


class LiveFeatureFeed:
    """features_fn for the engine over PRICE_SOURCES in ``root``: the newest complete book second s (s + 1 <= now,
    receive_ts <= now), trade prints and deriv rows known by s + 1, then compute_features / entry_signals_e8 over
    the last HISTORY_S seconds. Returns (s + 1, side or None, snapshot); (None, None, {}) before the first row.

    Incremental (R8): the collector tails are mirrored into running numpy buffers that only append the new seconds
    each sync (a full re-gather / re-sort happens only on rotation, an overwritten or out-of-order second, or an
    out-of-order trade print). Features are recomputed only when the inputs changed (new book second, newly eligible
    second, new prints / deriv rows, eviction); otherwise the cached result is returned. The inputs handed to
    compute_features are identical, element for element, to the former full rebuild (``full_rebuild_inputs``), so
    the features are bit-identical (test_tile_fade3_incremental_feed pins this on a recorded post-cutoff window)."""

    def __init__(self, root: str) -> None:
        self.book = _JsonlHistory(os.path.join(root, PRICE_SOURCES[0]), _parse_book, 48 << 20)
        self.trades = _JsonlHistory(os.path.join(root, PRICE_SOURCES[1]), _parse_trades, 32 << 20)
        self.deriv = _JsonlHistory(os.path.join(root, PRICE_SOURCES[2]), _parse_deriv, 16 << 20)
        self._bbuf = _GrowBuf({"key": (), "B": (10, 2), "A": (10, 2), "rx": ()})
        self._tbuf = _GrowBuf({"key": (), "T": (4,)})  # prints sorted by (ms, id); key = collector second
        self._dbuf = _GrowBuf({"key": (), "D": (2,)})
        self._seen = {"book": None, "trades": None, "deriv": None}  # resets counter mirrored per tail
        self._cache_key = None
        self._cache_val = (None, None, {})
        self.last_features: Optional[dict] = None
        self.recomputes = 0
        self.full_rebuilds = 0
        self._tmax = -1  # newest trade collector second mirrored into _tbuf

    # -- buffer maintenance --------------------------------------------------------------------------------------
    def _needs_rebuild(self, name: str, tail: "_JsonlHistory", buf: _GrowBuf) -> bool:
        if self._seen[name] != tail.resets:
            return True
        if tail.new_secs and len(buf):
            return min(tail.new_secs) <= int(buf.view("key")[-1]) if name != "trades" else \
                min(tail.new_secs) <= self._tmax
        return False

    def _book_cols(self, keys):
        rows = self.book.rows
        return {"key": np.asarray(keys, dtype=np.int64),
                "B": np.asarray([rows[k][0] for k in keys], dtype=float).reshape(-1, 10, 2),
                "A": np.asarray([rows[k][1] for k in keys], dtype=float).reshape(-1, 10, 2),
                "rx": np.asarray([np.nan if rows[k][2] is None else rows[k][2] for k in keys], dtype=float)}

    def _sync_book(self) -> None:
        t, buf = self.book, self._bbuf
        if self._needs_rebuild("book", t, buf):
            self.full_rebuilds += 1
            buf.set_all(self._book_cols(sorted(t.rows)))
            self._seen["book"] = t.resets
        elif t.new_secs:
            buf.append(self._book_cols(sorted(set(t.new_secs))))
        if len(buf) and t.rows and t.floor is not None and int(buf.view("key")[0]) < t.floor:
            buf.keep(buf.view("key") >= t.floor)

    def _trade_rows(self, keys):
        rows = self.trades.rows
        T = [p for k in keys for p in rows[k]]
        K = [k for k in keys for _ in rows[k]]
        order = sorted(range(len(T)), key=lambda i: (T[i][1], T[i][0]))  # stable, == sorted(T, key=(ms, id))
        return ({"key": np.asarray([K[i] for i in order], dtype=np.int64),
                 "T": np.asarray([T[i] for i in order], dtype=float).reshape(-1, 4)})

    def _sync_trades(self) -> None:
        t, buf = self.trades, self._tbuf
        if self._needs_rebuild("trades", t, buf):
            self.full_rebuilds += 1
            buf.set_all(self._trade_rows(list(t.rows)))  # dict order == the former generator order
            self._seen["trades"] = t.resets
            self._tmax = max(t.rows) if t.rows else -1
        elif t.new_secs:
            new = list(dict.fromkeys(t.new_secs))  # first-appearance order == dict insertion order
            add = self._trade_rows(new)
            if len(add["T"]) and len(buf):
                last, first = buf.view("T")[-1], add["T"][0]
                if (first[1], first[0]) <= (last[1], last[0]):  # a print older than the newest buffered: re-sort
                    self.full_rebuilds += 1
                    buf.set_all(self._trade_rows(list(t.rows)))
                    add = None
            if add is not None:
                buf.append(add)
            self._tmax = max(self._tmax, max(new))
        if len(buf) and t.floor is not None:
            buf.keep(buf.view("key") >= t.floor)

    def _sync_deriv(self) -> None:
        t, buf = self.deriv, self._dbuf

        def cols(keys):
            return {"key": np.asarray(keys, dtype=np.int64),
                    "D": np.asarray([t.rows[k] for k in keys], dtype=float).reshape(-1, 2)}
        if self._needs_rebuild("deriv", t, buf):
            self.full_rebuilds += 1
            buf.set_all(cols(sorted(t.rows)))
            self._seen["deriv"] = t.resets
        elif t.new_secs:
            buf.append(cols(sorted(set(t.new_secs))))
        if len(buf) and t.floor is not None and int(buf.view("key")[0]) < t.floor:
            buf.keep(buf.view("key") >= t.floor)

    # -- tick ------------------------------------------------------------------------------------------------------
    def __call__(self, now: float):
        for tail in (self.book, self.trades, self.deriv):
            tail.sync()
        self._sync_book()
        self._sync_trades()
        self._sync_deriv()
        S_all = self._bbuf.view("key")
        if not len(S_all):
            return None, None, {}
        rx = self._bbuf.view("rx")
        with np.errstate(invalid="ignore"):
            elig = (S_all + 1 <= now) & (np.isnan(rx) | (rx <= now))
        idx = np.flatnonzero(elig)
        if not idx.size:
            return None, None, {}
        contiguous = idx[-1] - idx[0] + 1 == idx.size
        s_last = int(S_all[idx[-1]])
        key = (int(S_all[idx[0]]), s_last, int(idx.size), self.book.gen, self.trades.gen, self.deriv.gen)
        if key == self._cache_key:
            return self._cache_val
        sl = slice(int(idx[0]), int(idx[-1]) + 1) if contiguous else idx
        S, B, A = S_all[sl], self._bbuf.view("B")[sl], self._bbuf.view("A")[sl]
        TK, T = self._tbuf.view("key"), self._tbuf.view("T")
        if len(TK) and TK.max() > s_last:
            T = T[TK <= s_last]
        DK = self._dbuf.view("key")
        nd = int(np.searchsorted(DK, s_last, "right"))
        D = np.column_stack([DK[:nd].astype(float), self._dbuf.view("D")[:nd]]) if nd else np.empty((0, 3))
        F = compute_features(S, B, A, T, D)
        L, Sh = entry_signals_e8(F)
        side = "LONG" if L[-1] else "SHORT" if Sh[-1] else None
        snap = {k: (float(F[k][-1]) if np.isfinite(F[k][-1]) else None)
                for k in ("mid", "spread", "imb5", "z3", "mom5", "ATR30", "BZ")}
        self.recomputes += 1
        self.last_features = F
        self._cache_key, self._cache_val = key, (float(s_last + 1), side, snap)
        return self._cache_val


def full_rebuild_inputs(feed: "LiveFeatureFeed", now: float):
    """Reference (pre-R8) full rebuild of compute_features inputs from the feed's synced tails; equivalence tests
    and benchmarks only (never on the tick path). Returns None before the first eligible book second."""
    secs = sorted(k for k, v in feed.book.rows.items() if k + 1 <= now and (v[2] is None or v[2] <= now))
    if not secs:
        return None
    s_last = secs[-1]
    S = np.asarray(secs, dtype=np.int64)
    B = np.asarray([feed.book.rows[k][0] for k in secs], dtype=float)
    A = np.asarray([feed.book.rows[k][1] for k in secs], dtype=float)
    T = sorted((t for k, prints in feed.trades.rows.items() if k + 1 <= s_last + 1 for t in prints),
               key=lambda t: (t[1], t[0]))
    D = [[k, *v] for k, v in sorted(feed.deriv.rows.items()) if k + 1 <= s_last + 1]
    return S, B, A, np.asarray(T, dtype=float).reshape(-1, 4), np.asarray(D, dtype=float).reshape(-1, 3)
