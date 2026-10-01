"""CONSERVATIVE_BBO entry/exit simulator on the 1 s Bitfinex tape.

Port of ``diagnostics/tile2_20261001/sim2.py`` (entries, exits, funding) with
capacity-1 sequential execution from ``analyze3.py``. World rules:

* TAKER fills at the executable quote (ask for LONG, bid for SHORT) ``latency``
  seconds after the decision, optionally only while the quote is within a
  protection cap of the decision mid (the runtime's taker limit).
* MAKER / REST limits fill only when the *opposite* quote reaches the limit
  (never on a last-trade print), within their TTL; unfilled orders score 0 and
  occupy the tile for their TTL.
* Stops, trails, ladders and time exits fill at the executable quote of the
  breach second; take-profit limits fill at their level.
* Costs: Bitfinex fees from ``bitfinex_cost_profile`` (the only venue
  profile), the spread through executable quotes, optional extra slippage per
  taker leg, and funding when a hold crosses 00/08/16 UTC.
* Paths crossing a tape hole longer than 60 s are censored, never filled with
  stale quotes.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

import bitfinex_cost_profile as cost_profile
from strategy_lab.tape import Tape

FUNDING_PERIOD_SEC = 8 * 3600
MAX_HOLD_SEC = 7200


@dataclass(frozen=True)
class EntrySpec:
    kind: str = "TAKER"            # TAKER | MAKER | REST
    latency_sec: int = 1
    ttl_sec: int = 0               # MAKER/REST TTL; TAKER protection window
    offset_pct: float = 0.0        # REST distance from mid
    protection_bp: Optional[float] = None
    max_spread_bp: Optional[float] = None

    def label(self) -> str:
        if self.kind == "TAKER":
            return f"TAKER_L{self.latency_sec}" + (f"_SPR{self.max_spread_bp:g}" if self.max_spread_bp else "")
        if self.kind == "MAKER":
            return f"MAKER1T_{self.ttl_sec}"
        return f"REST_{self.offset_pct:g}_{self.ttl_sec}"


@dataclass(frozen=True)
class ExitSpec:
    tcap_sec: int = 3600
    hard_bp: Optional[float] = None
    sl_bp: Optional[float] = None
    sl_atr: Optional[float] = None
    arm_atr: Optional[float] = None
    trail_atr: Optional[float] = None
    tp_bp: Optional[float] = None
    tp_atr: Optional[float] = None
    giveback: Optional[float] = None
    ladder: tuple = field(default_factory=tuple)      # ((trigger_bp, lock_bp), ...)
    breakeven: Optional[tuple] = None                 # (trigger_bp, lock_bp)

    @property
    def needs_atr(self) -> bool:
        return any(v is not None for v in (self.sl_atr, self.trail_atr, self.tp_atr))

    def label(self) -> str:
        parts = [f"T{self.tcap_sec}"]
        for name in ("hard_bp", "sl_bp", "sl_atr", "arm_atr", "trail_atr", "tp_bp", "tp_atr", "giveback"):
            v = getattr(self, name)
            if v is not None:
                parts.append(f"{name}{v:g}")
        if self.ladder:
            parts.append(f"ladder{len(self.ladder)}")
        if self.breakeven:
            parts.append("be{0:g}-{1:g}".format(*self.breakeven))
        return "_".join(parts)


@dataclass(frozen=True)
class CostModel:
    maker_fee_rate: float = cost_profile.MAKER_FEE_RATE
    taker_fee_rate: float = cost_profile.TAKER_FEE_RATE
    taker_slippage_bp: float = 0.0
    funding: bool = True

    @property
    def profile_id(self) -> str:
        return cost_profile.FEE_PROFILE_ID


def _tick(px: float) -> float:
    return 10.0 ** (math.floor(math.log10(px)) - 4)


def _entry_fill(tape: Tape, i_dec: int, sign: int, spec: EntrySpec) -> tuple:
    """Return (fill index or -1, fill price, occupied-until index)."""
    n = tape.n
    i0 = i_dec + max(int(spec.latency_sec), 0)
    if i0 >= n - 1 or i_dec < 0:
        return -1, math.nan, i0
    if spec.max_spread_bp is not None:
        spr = tape.spread_bp[i_dec]
        if not (np.isfinite(spr) and spr <= spec.max_spread_bp):
            return -1, math.nan, i_dec
    if spec.kind == "TAKER":
        hi = min(i0 + max(int(spec.ttl_sec), 0), n - 1)
        cap = None
        if spec.protection_bp is not None:
            ref = tape.mid[i_dec]
            cap = ref * (1 + sign * spec.protection_bp / 1e4)
        for j in range(i0, hi + 1):
            if not tape.present[j]:
                continue
            px = tape.ask[j] if sign > 0 else tape.bid[j]
            if cap is None or (px <= cap if sign > 0 else px >= cap):
                return j, float(px), j
        return -1, math.nan, hi
    if spec.kind == "MAKER":
        tk = _tick(tape.mid[i0])
        if sign > 0:
            limit = tape.bid[i0] + tk if tape.bid[i0] + tk < tape.ask[i0] else tape.bid[i0]
        else:
            limit = tape.ask[i0] - tk if tape.ask[i0] - tk > tape.bid[i0] else tape.ask[i0]
    else:
        limit = tape.mid[i0] * (1 - sign * spec.offset_pct / 100.0)
    hi = min(i0 + max(int(spec.ttl_sec), 1), n - 1)
    seg = slice(i0 + 1, hi + 1)
    hit = ((tape.ask[seg] <= limit) if sign > 0 else (tape.bid[seg] >= limit)) & tape.present[seg]
    if hit.any():
        j = i0 + 1 + int(np.argmax(hit))
        return j, float(limit), j
    return -1, math.nan, hi


def _ladder_floor(peak: np.ndarray, ladder: tuple) -> np.ndarray:
    trig = np.array([r[0] for r in ladder], float)
    lock = np.array([r[1] for r in ladder], float)
    k = np.searchsorted(trig, peak, side="right") - 1
    return np.where(k >= 0, lock[np.maximum(k, 0)], -np.inf)


def _exit_path(tape: Tape, j: int, fill_px: float, sign: int, spec: ExitSpec, atr_bp: float) -> tuple:
    """Return (exit offset s, exit return bp vs fill, reason, censored)."""
    tcap = min(int(spec.tcap_sec), MAX_HOLD_SEC)
    end = min(j + tcap, tape.n - 1)
    censor_at = int(tape.next_bad[j]) if j < tape.n else tape.n
    q = tape.bid[j:end + 1] if sign > 0 else tape.ask[j:end + 1]
    u = sign * (q - fill_px) / fill_px * 1e4
    valid_len = min(censor_at - j, len(u))
    if valid_len <= 1:
        return 0, math.nan, "CENSORED", True
    u = u[:valid_len]
    peak = np.maximum.accumulate(u)
    stop = np.full(u.shape, -np.inf)
    if spec.sl_bp is not None:
        stop = np.maximum(stop, -spec.sl_bp)
    if spec.sl_atr is not None and np.isfinite(atr_bp):
        stop = np.maximum(stop, -spec.sl_atr * atr_bp)
    if spec.trail_atr is not None and np.isfinite(atr_bp):
        armed = peak >= (spec.arm_atr or 0.0) * atr_bp
        stop = np.where(armed, np.maximum(stop, peak - spec.trail_atr * atr_bp), stop)
    if spec.giveback is not None:
        stop = np.where(peak > 0, np.maximum(stop, peak * (1 - spec.giveback)), stop)
    if spec.ladder:
        stop = np.maximum(stop, _ladder_floor(peak, spec.ladder))
    if spec.breakeven:
        stop = np.where(peak >= spec.breakeven[0], np.maximum(stop, spec.breakeven[1]), stop)
    hit_stop = u <= stop
    hit_hard = (u <= -spec.hard_bp) if spec.hard_bp is not None else np.zeros(u.shape, bool)
    tp = None
    if spec.tp_bp is not None:
        tp = spec.tp_bp
    elif spec.tp_atr is not None and np.isfinite(atr_bp):
        tp = spec.tp_atr * atr_bp
    hit_tp = (u >= tp) if tp is not None else np.zeros(u.shape, bool)
    anyhit = (hit_stop | hit_hard | hit_tp)
    anyhit[0] = False                                   # second 0 is the fill second
    if anyhit.any():
        k = int(np.argmax(anyhit))
        if hit_hard[k]:
            return k, float(u[k]), "HARD_STOP", False
        if hit_stop[k]:
            return k, float(u[k]), "STOP", False
        return k, float(tp), "TAKE_PROFIT", False
    if len(u) < tcap + 1:                               # tape hole or tape end before the time exit
        return len(u) - 1, math.nan, "CENSORED", True
    k = len(u) - 1
    return k, float(u[k]), "TIME", False


def simulate(tape: Tape, signals: pd.DataFrame, entry: EntrySpec, exit_: ExitSpec,
             costs: CostModel = CostModel(), capacity_one: bool = True,
             keep_skipped: bool = True) -> pd.DataFrame:
    """Simulate ``signals`` (columns ``ts`` decision epoch-seconds, ``side`` +1/-1).

    Optional ``funding_bp_8h`` column: funding rate (bp per 8 h) charged to the
    paying side when the hold crosses a funding boundary. Every signal yields one
    row (``skipped_busy`` while the tile holds a position) unless
    ``keep_skipped`` is False, which drops busy rows for dense per-second
    triggers.
    """
    cols = ["signal_ts", "side", "filled", "skipped_busy", "fill_ts", "fill_px", "exit_ts", "exit_px",
            "hold_sec", "reason", "censored", "quote_bp", "mid_bp", "spread_cost_bp", "fee_bp",
            "slippage_bp", "funding_bp", "net_bp"]
    if signals is None or len(signals) == 0:
        return pd.DataFrame(columns=cols)
    sig = signals.sort_values("ts", kind="stable").reset_index(drop=True)
    i_dec_all = tape.index(sig["ts"].to_numpy(float))
    fund = sig["funding_bp_8h"].to_numpy(float) if "funding_bp_8h" in sig.columns else np.zeros(len(sig))
    atr_all = tape.atr_abs(sig["ts"].to_numpy(float)) if exit_.needs_atr else np.full(len(sig), np.nan)
    maker_entry = entry.kind != "TAKER"
    sides = sig["side"].to_numpy(int)
    sig_ts = sig["ts"].to_numpy(float)
    busy_until = -1
    out, kept = [], []
    for k in range(len(sig)):
        side = int(sides[k])
        i_dec = int(i_dec_all[k])
        busy = capacity_one and i_dec < busy_until
        if busy and not keep_skipped:
            continue
        kept.append(k)
        row = dict.fromkeys(cols)
        row.update(signal_ts=float(sig_ts[k]), side=side, filled=False, skipped_busy=False,
                   censored=False, net_bp=0.0, reason="NO_FILL")
        if side == 0 or i_dec < 0 or i_dec >= tape.n - 2:
            row["reason"] = "OUT_OF_TAPE" if side else "NO_SIDE"
            out.append(row)
            continue
        if busy:
            row.update(skipped_busy=True, reason="BUSY")
            out.append(row)
            continue
        j, fill_px, occupied = _entry_fill(tape, i_dec, side, entry)
        if j < 0:
            busy_until = max(busy_until, occupied) if maker_entry else busy_until
            out.append(row)
            continue
        atr_bp = float(atr_all[k] / fill_px * 1e4) if np.isfinite(atr_all[k]) else math.nan
        off, u, reason, censored = _exit_path(tape, j, fill_px, side, exit_, atr_bp)
        x = j + off
        busy_until = x + 1
        row.update(filled=True, fill_ts=float(tape.t0 + j), fill_px=fill_px, reason=reason, censored=censored)
        if censored or not np.isfinite(u):
            row.update(censored=True, net_bp=math.nan, hold_sec=float(off))
            out.append(row)
            continue
        exit_px = fill_px * (1 + side * u / 1e4)
        mid_bp = side * (tape.mid[x] - tape.mid[j]) / tape.mid[j] * 1e4
        tp_exit = reason == "TAKE_PROFIT"
        fee_bp = ((costs.maker_fee_rate if maker_entry else costs.taker_fee_rate)
                  + (costs.maker_fee_rate if tp_exit else costs.taker_fee_rate)) * 1e4
        slip = costs.taker_slippage_bp * ((0 if maker_entry else 1) + (0 if tp_exit else 1))
        fbp = 0.0
        if costs.funding and np.isfinite(fund[k]) and fund[k]:
            t_in, t_out = tape.t0 + j, tape.t0 + x
            if (t_out // FUNDING_PERIOD_SEC) > (t_in // FUNDING_PERIOD_SEC):
                fbp = -side * float(fund[k])
        row.update(exit_ts=float(tape.t0 + x), exit_px=float(exit_px), hold_sec=float(off), quote_bp=u,
                   mid_bp=float(mid_bp), spread_cost_bp=float(mid_bp - u), fee_bp=fee_bp, slippage_bp=slip,
                   funding_bp=fbp, net_bp=float(u - fee_bp - slip + fbp))
        out.append(row)
    df = pd.DataFrame(out, columns=cols)
    extra = [c for c in sig.columns if c not in ("ts", "side")]
    for c in extra:
        df[c] = sig[c].to_numpy()[kept]
    return df


def exit_spec_from_registry(spec: dict, leverage: float = 100.0) -> tuple:
    """Map a registry ``exit_policy`` to an ``ExitSpec`` (or ``(None, reason)``)."""
    ep = dict((spec or {}).get("exit_policy") or {})
    family = str(ep.get("family") or "").upper()
    lev = float(leverage or 100.0)
    hard_bp = ep.get("hard_stop_bps")
    if hard_bp is None and ep.get("hard_stop_margin_pct") is not None:
        hard_bp = float(ep["hard_stop_margin_pct"]) * 100.0 / lev
    tcap = int(ep.get("max_duration_sec") or spec.get("path_end_sec") or MAX_HOLD_SEC)

    def ladder_bp(raw):
        rows = []
        for item in raw or ():
            if isinstance(item, dict):
                trig, lock = item.get("trigger_margin_pct"), item.get("lock_margin_pct")
            else:
                trig, lock = (list(item) + [None, None])[:2]
            if trig is None or lock is None:
                continue
            rows.append((float(trig) * 100.0 / lev, float(lock) * 100.0 / lev))
        return tuple(sorted(rows))

    raw_ladder = ep.get("ladder") or ep.get("trail_ladder")
    if not raw_ladder and family == "ATR_TRAIL_PROFIT_LOCK":
        raw_ladder = spec.get("ladder")             # Scenario-C rungs live on the spec, in margin %
    ladder = ladder_bp(raw_ladder)
    be = None
    if ep.get("breakeven_trigger_margin_pct") is not None:
        be = (float(ep["breakeven_trigger_margin_pct"]) * 100.0 / lev,
              float(ep.get("breakeven_lock_margin_pct") or 0.0) * 100.0 / lev)
    if family == "TIME_EXIT_WITH_CATASTROPHIC_STOP":
        return ExitSpec(tcap_sec=tcap, hard_bp=hard_bp, ladder=ladder, breakeven=be), None
    if family in ("ATR_TRAIL", "ATR_TRAIL_PROFIT_LOCK"):
        return ExitSpec(tcap_sec=tcap, hard_bp=hard_bp, sl_atr=ep.get("initial_stop_atr_k"),
                        arm_atr=ep.get("trail_activation_atr_k"), trail_atr=ep.get("trail_atr_k"),
                        ladder=ladder, breakeven=be), None
    return None, f"EXIT_FAMILY_UNSUPPORTED:{family or 'MISSING'}"


def spec_dict(obj) -> dict:
    d = asdict(obj)
    return {k: (list(map(list, v)) if isinstance(v, tuple) and v and isinstance(v[0], tuple) else v)
            for k, v in d.items()}


PARITY_TOLERANCE_BP = 2.0
_FORCED = {"ADMIN_MANUAL_CLOSE", "ADMIN_FORCE_FLAT", "CIRCUIT_BREAKER_ADMIN_MANUAL"}


def live_fill_parity(tape: Tape, trades: pd.DataFrame, registry: dict, lanes, leverage: float = 100.0) -> dict:
    """Replay each closed current-cohort tile trade from its real fill and compare.

    The live fill second and price are taken from the ledger (entry fill
    realism is reported separately as ``entry_gap_bp`` against the tape quote);
    the exit is re-simulated with the lane's registry exit spec. Admin/deploy
    closes are excluded because no exit policy produced them.
    """
    rows, skipped = [], {}
    if trades is None or trades.empty or tape is None:
        return {"status": "NO_DATA", "rows": [], "lanes": {}, "tolerance_bp": PARITY_TOLERANCE_BP}
    t = trades.copy()
    lane_col = t.get("research_lane", pd.Series("", index=t.index)).fillna("").astype(str).str.upper()
    for lane in lanes:
        spec = (registry or {}).get(lane) or {}
        exit_spec, why = exit_spec_from_registry(spec, leverage)
        sub = t[lane_col == lane]
        if exit_spec is None:
            skipped[lane] = why
            continue
        for _, r in sub.iterrows():
            reason = str(r.get("exit_reason") or "").upper()
            if reason in _FORCED:
                continue
            try:
                close_ts = pd.Timestamp(r.get("close_ts") or r.get("ts")).timestamp()
                dur = float(r.get("dur_min") or r.get("duration_min")) * 60.0
                entry_px, exit_px = float(r.get("entry")), float(r.get("exit"))
            except (TypeError, ValueError):
                continue
            side = 1 if str(r.get("dir") or r.get("final_direction") or "").upper() == "LONG" else -1
            fill_ts = close_ts - dur
            j = int(math.floor(fill_ts)) - tape.t0
            if not (0 <= j < tape.n - 2) or not all(np.isfinite([entry_px, exit_px])):
                continue
            atr_bp = float(tape.atr_abs([fill_ts])[0] / entry_px * 1e4)
            off, u, sim_reason, censored = _exit_path(tape, j, entry_px, side, exit_spec, atr_bp)
            live_bp = side * (exit_px - entry_px) / entry_px * 1e4
            quote = tape.ask[j] if side > 0 else tape.bid[j]
            rows.append({
                "research_lane": lane, "trade_id": str(r.get("trade_id") or ""), "side": side,
                "fill_ts": fill_ts, "live_exit_reason": reason, "sim_exit_reason": sim_reason,
                "live_bp": live_bp, "sim_bp": u if not censored else math.nan,
                "diff_bp": (u - live_bp) if not censored and np.isfinite(u) else math.nan,
                "live_hold_sec": dur, "sim_hold_sec": float(off), "censored": bool(censored),
                "entry_gap_bp": side * (entry_px - quote) / quote * 1e4 if tape.present[j] else math.nan,
            })
    df = pd.DataFrame(rows)
    lanes_out = {}
    if not df.empty:
        for lane, g in df.groupby("research_lane"):
            d = g["diff_bp"].dropna()
            exit_spec, _ = exit_spec_from_registry((registry or {}).get(lane) or {}, leverage)
            lanes_out[lane] = {
                "n": int(len(g)), "compared": int(len(d)),
                "atr_dependent": bool(exit_spec is not None and exit_spec.needs_atr),
                "mae_bp": round(float(d.abs().mean()), 3) if len(d) else None,
                "within_tolerance_share": round(float((d.abs() <= PARITY_TOLERANCE_BP).mean()), 3) if len(d) else None,
                "mean_entry_gap_bp": round(float(g["entry_gap_bp"].dropna().mean()), 3)
                if g["entry_gap_bp"].notna().any() else None,
                "verdict": ("PASS" if len(d) and float(d.abs().mean()) <= PARITY_TOLERANCE_BP else
                            "FAIL" if len(d) else "INSUFFICIENT"),
            }
    overall = df["diff_bp"].dropna() if not df.empty else pd.Series(dtype=float)
    return {
        "status": "OK" if len(overall) else "INSUFFICIENT",
        "tolerance_bp": PARITY_TOLERANCE_BP,
        "compared": int(len(overall)),
        "mae_bp": round(float(overall.abs().mean()), 3) if len(overall) else None,
        "within_tolerance_share": round(float((overall.abs() <= PARITY_TOLERANCE_BP).mean()), 3)
        if len(overall) else None,
        "verdict": ("PASS" if len(overall) and float(overall.abs().mean()) <= PARITY_TOLERANCE_BP else
                    "FAIL" if len(overall) else "INSUFFICIENT"),
        "lanes": lanes_out,
        "skipped_lanes": skipped,
        "rows": df.to_dict("records"),
        "basis": "real fill second+price from the ledger; exit re-simulated from the registry exit spec on "
                 "the 1 s CONSERVATIVE_BBO tape; admin/deploy closes excluded",
        "atr_note": "ATR-dependent lanes use a 3 m mid-quote Wilder ATR14 rebuilt from the tape; the runtime's "
                    "fill-time ATR (exchange candles) is not in the mirror, so their residual includes ATR basis",
    }
