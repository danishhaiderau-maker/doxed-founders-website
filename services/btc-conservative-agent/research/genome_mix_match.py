"""Decision-time regime axes, mix-and-match search, regime map and regime-switching meta-policy.

Research only (SIMULATED_COUNTERFACTUAL). Works on the genome grid's REALISTIC_V1 outcome matrix: one row per
unique AI decision (or per cross-venue trigger when called for that cohort), one column per entry x exit x
direction policy. Every context axis is read from data available at the decision: the v3 opportunity feature
snapshot (captured PRE_AI_DECISION), decision scores, the 1 s Bitfinex BBO of the second before the signal, and
cross-venue mids strictly before the signal second. Nothing looks ahead.

Selection (policy, filters, regime -> policy mapping) is always redone inside each walk-forward fold using only
prior UTC days; the pooled nested out-of-sample number is the only one that counts. Picking the best value of
each axis from the same data overfits - in-sample numbers are shown only as labelled descriptions.
"""
from __future__ import annotations

import bisect
import hashlib
import json
import math
import time
from collections import Counter, defaultdict
from pathlib import Path
from statistics import NormalDist
from typing import Any, Callable, Iterable, Mapping

import numpy as np

SCHEMA = "genome_mix_match_v1"
FILTER_DEFS_VERSION = "FILTER_DEFS_V1"
NOTIONAL_USD = 25.0
DAY_SEC = 86400
CLUSTER_SEC = 3600
BOOTSTRAP_RESAMPLES = 2000
BOOTSTRAP_SEED = 20261003
MIN_TRAIN_FILLS = 30
MIN_EFF_TRADES = 30          # distinct 1 h clusters among fills: the independent-trade floor for any combo
MIN_CELL_FILLS = 20          # regime cells are smaller; the meta-policy stands aside below this
MIN_CELL_EFF = 10
MIN_FILTER_GAIN_USD = 0.0001  # a filter must add >= 0.4 bp/fill on TRAIN to be kept
MAX_FILTERS = 3
TOP_CHECK = 400
XV_LEAD_BP = 1.0
XV_PREMIUM_DEV_BP = 2.0
XV_PREMIUM_WINDOW_SEC = 3600
XV_MIN_PREMIUM_SAMPLES = 600
PRIOR_SNAPSHOT_MAX_AGE_SEC = 360
HALF_DAY_SEC = 43200
COMMITTED_CLASSES = ("AI_COMMITTED", "AI_COMMITTED_SCORE_CONFLICT")
SESSION_ENDS = ((8, "ASIA"), (16, "EU"), (24, "US"))  # bot._utc_session_label

# Family / ingredient groups shown in the per-family table; every group must appear (content contract).
FAMILY_GROUPS: dict[str, str] = {
    "CHANDELIER": "family CHANDELIER (ATR chandelier trail)",
    "MFE_GIVEBACK": "family MFE_GIVEBACK (give back a share/amount of peak profit)",
    "ATR_TRAIL": "family ATR_TRAIL (armed ATR trailing stop)",
    "ATR_TARGET": "family FIXED_TARGET (ATR take-profit; incl. ladder/BE/time/thesis variants)",
    "HYBRID_RUNNER": "family HYBRID_RUNNER (partials + trailed runner)",
    "TIME_EXIT_HARD_STOP": "registry time exit with catastrophic hard stop",
    "PROFIT_LADDER": "any exit with the Scenario-C profit ladder floor",
    "THESIS_CUT": "any exit with a thesis fast cut",
    "BREAK_EVEN_ARM": "any exit with a break-even arm/lock",
    "TIME_STOP": "any exit with a time stop",
    "PARTIALS": "any exit with partial take-profits",
    "ATR_STOP": "any exit with an ATR initial stop",
    "HARD_STOP_ONLY": "loss bounded only by the physical hard stop",
}
_FAMILY_OF = {"CHANDELIER": "CHANDELIER", "MFE_GIVEBACK": "MFE_GIVEBACK", "ATR_TRAIL": "ATR_TRAIL",
              "FIXED_TARGET": "ATR_TARGET", "HYBRID_RUNNER": "HYBRID_RUNNER", "REGISTRY_TIME_EXIT": "TIME_EXIT_HARD_STOP"}

INGREDIENTS: dict[str, str] = {
    "FADE": "fade the signalled side (vs follow)",
    "MAKER": "passive limit entry (vs taker at signal)",
    "NO_CHASE": "limit never chases",
    "CHASE_STEP_50": "chase 50% of the remaining gap per reprice",
    "CHASE_STEP_25": "chase 25% of the remaining gap per reprice",
    "CHASE_STEP_10": "chase 10% of the remaining gap per reprice",
    "TTL_900": "entry TTL 15 min", "TTL_1800": "entry TTL 30 min", "TTL_3600": "entry TTL 60 min",
    "OFFSET_LE_0.15": "limit offset <= 0.15%", "OFFSET_GE_0.30": "limit offset >= 0.30%",
    "PROFIT_LADDER": "Scenario-C profit ladder", "THESIS_CUT": "thesis fast cut", "BREAK_EVEN_ARM": "break-even arm",
    "TIME_STOP": "time stop", "PARTIALS": "partial take-profits", "ATR_STOP": "ATR initial stop",
    "HARD_STOP_ONLY": "hard stop is the only loss bound", "ATR_TARGET": "ATR take-profit target",
}

_ND = NormalDist()


# ------------------------------------------------------------------ helpers

def _num(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _read_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    try:
        fh = open(path, encoding="utf-8")
    except OSError:
        return
    with fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                yield row


def bp(usd: float | None) -> float | None:
    return None if usd is None or usd != usd else round(float(usd) / NOTIONAL_USD * 1e4, 2)


def session_of(ts: float) -> str:
    h = time.gmtime(ts).tm_hour
    return next(label for end, label in SESSION_ENDS if h < end)


def cluster_ci(pnl: np.ndarray, ts: np.ndarray, *, alpha: float = 0.05, cluster_sec: int = CLUSTER_SEC) -> dict[str, Any]:
    """1 h block bootstrap EV CI (same method/seed as the genome) and effective independent n."""
    p, t = np.asarray(pnl, dtype=np.float64), np.asarray(ts, dtype=np.float64)
    n = len(p)
    if n < 2:
        return {"ci_bp": [None, None], "n_eff": float(n), "clusters": n, "alpha": alpha}
    _, inv = np.unique(np.floor(t / cluster_sec).astype(np.int64), return_inverse=True)
    sums, counts = np.bincount(inv, weights=p), np.bincount(inv).astype(np.float64)
    c = len(sums)
    draw = np.random.default_rng(BOOTSTRAP_SEED).integers(0, c, size=(BOOTSTRAP_RESAMPLES, c))
    means = sums[draw].sum(axis=1) / counts[draw].sum(axis=1)
    lo, hi = np.percentile(means, [100 * alpha / 2, 100 * (1 - alpha / 2)])
    var_boot, var_iid = float(means.var(ddof=1)), float(p.var(ddof=1)) / n
    n_eff = float(n) if var_boot <= 0 else min(float(n), max(1.0, n * var_iid / var_boot))
    return {"ci_bp": [bp(lo), bp(hi)], "n_eff": round(n_eff, 1), "clusters": c, "alpha": alpha, "cluster_sec": cluster_sec,
            "lower_one_sided_bp": bp(float(np.percentile(means, 100 * alpha)))}


def summarize(pnl: Iterable[float], ts: Iterable[float], span_days: float | None = None,
              cluster_sec: int = CLUSTER_SEC) -> dict[str, Any]:
    p, t = np.asarray(list(pnl), dtype=np.float64), np.asarray(list(ts), dtype=np.float64)
    n = len(p)
    out: dict[str, Any] = {"fills": n, "span_days": round(span_days, 3) if span_days else None}
    if n == 0:
        return out | {"wins": 0, "losses": 0, "win_rate_pct": None, "net_pnl_usd": 0.0, "ev_per_fill_usd": None,
                      "ev_bp": None, "ci_bp": [None, None], "n_eff": 0.0, "max_drawdown_usd": 0.0,
                      "trades_per_day": 0.0 if span_days else None}
    order = np.argsort(t, kind="stable")
    eq = np.cumsum(p[order])
    ci = cluster_ci(p, t, cluster_sec=cluster_sec)
    sd = float(p.std(ddof=1)) if n > 1 else 0.0
    return out | {
        "wins": int((p > 0).sum()), "losses": int((p < 0).sum()), "win_rate_pct": round(100 * float((p > 0).mean()), 2),
        "net_pnl_usd": round(float(p.sum()), 6), "ev_per_fill_usd": round(float(p.mean()), 6), "ev_bp": bp(float(p.mean())),
        "ci_bp": ci["ci_bp"], "n_eff": ci["n_eff"], "clusters": ci["clusters"], "cluster_sec": cluster_sec,
        "max_drawdown_usd": round(float(np.min(eq - np.maximum.accumulate(np.maximum(eq, 0.0)))), 6),
        "sharpe_per_trade": round(float(p.mean()) / sd, 4) if sd > 0 else None,
        "trades_per_day": round(n / span_days, 2) if span_days else None,
    }


def deflated_sharpe(pnl: np.ndarray, n_trials: int, sr_var: float, t_eff: float) -> dict[str, Any]:
    """Bailey & Lopez de Prado deflated Sharpe on per-trade returns with T = effective independent trades."""
    p = np.asarray(pnl, dtype=np.float64)
    if len(p) < 3 or p.std(ddof=1) <= 0:
        return {"status": "INSUFFICIENT"}
    sr = float(p.mean() / p.std(ddof=1))
    z = (p - p.mean()) / p.std(ddof=0)
    skew, kurt = float((z ** 3).mean()), float((z ** 4).mean())
    g = 0.5772156649
    n = max(2, int(n_trials))
    sr0 = math.sqrt(max(sr_var, 0.0)) * ((1 - g) * _ND.inv_cdf(1 - 1 / n) + g * _ND.inv_cdf(1 - 1 / (n * math.e)))
    den = math.sqrt(max(1e-12, 1 - skew * sr + (kurt - 1) / 4 * sr * sr))
    t = max(2.0, float(t_eff))
    return {"sharpe_per_trade": round(sr, 4), "trials": n, "sharpe_var_across_trials": round(sr_var, 6),
            "expected_max_sharpe_under_null": round(sr0, 4), "t_effective": round(t, 1),
            "skew": round(skew, 3), "kurtosis": round(kurt, 3),
            "probabilistic_sharpe_vs_zero": round(_ND.cdf(sr * math.sqrt(t - 1) / den), 4),
            "deflated_sharpe_prob": round(_ND.cdf((sr - sr0) * math.sqrt(t - 1) / den), 4)}


# ------------------------------------------------------------------ context axes

def load_cross_mids(mirror: Path) -> dict[str, Any] | None:
    """Per-second Bitfinex mid and the median of Binance/Bybit/OKX mids from cross_venue_tape_1m."""
    try:
        import cross_venue_tape as cvt  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        return None
    bfx: dict[int, float] = {}
    ref: dict[int, float] = {}
    paths = sorted(Path(mirror).glob("cross_venue_tape_1m.jsonl*"))
    for path in [p for p in paths if not p.name.endswith(".json")]:
        for row in _read_jsonl(path):
            if row.get("schema") != "cross_venue_tape_1m_v1":
                continue
            try:
                dec = cvt.decode_minute(row)
            except Exception:  # noqa: BLE001
                continue
            for ts, v in (dec.get("bfx") or {}).items():
                if _num(v):
                    bfx[int(ts)] = float(v)
            per: dict[int, list[float]] = defaultdict(list)
            for venue in ("binance", "bybit", "okx"):
                for ts, cell in (dec.get(venue) or {}).items():
                    if cell and cell.get("up") and _num(cell.get("mid")):
                        per[int(ts)].append(float(cell["mid"]))
            for ts, mids in per.items():
                ref[ts] = float(np.median(mids))
    if not bfx or not ref:
        return None
    x0, x1 = min(min(bfx), min(ref)), max(max(bfx), max(ref)) + 1
    b, r = np.full(x1 - x0, np.nan), np.full(x1 - x0, np.nan)
    for ts, v in bfx.items():
        b[ts - x0] = v
    for ts, v in ref.items():
        r[ts - x0] = v
    prem = (b / r - 1.0) * 1e4
    return {"x0": x0, "bfx": b, "ref": r, "premium_bp": prem, "first_ts": x0, "last_ts": x1 - 1}


def cross_venue_state(xv: Mapping[str, Any] | None, ts: float, side: str) -> tuple[float | None, float | None]:
    """(lead vs side, premium deviation vs side) in bp from seconds strictly before the signal."""
    if not xv or side not in ("LONG", "SHORT"):
        return None, None
    s = int(math.floor(ts)) - 1 - xv["x0"]
    if s - 60 < 0 or s >= len(xv["bfx"]):
        return None, None
    sign = 1.0 if side == "LONG" else -1.0
    b0, b1, r0, r1 = xv["bfx"][s - 60], xv["bfx"][s], xv["ref"][s - 60], xv["ref"][s]
    lead = None
    if not any(math.isnan(x) for x in (b0, b1, r0, r1)):
        lead = round(((r1 / r0 - 1) - (b1 / b0 - 1)) * 1e4 * sign, 3)
    window = xv["premium_bp"][max(0, s - XV_PREMIUM_WINDOW_SEC): s]
    dev = None
    if np.count_nonzero(~np.isnan(window)) >= XV_MIN_PREMIUM_SAMPLES and not math.isnan(xv["premium_bp"][s]):
        dev = round((float(xv["premium_bp"][s]) - float(np.nanmean(window))) * sign, 3)
    return lead, dev


def episode_context(mirror: Path, episodes: list[Mapping[str, Any]], tape: Any = None,
                    xv: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    """Decision-time context per episode (same order). Missing evidence stays None and never matches a filter."""
    led = Path(mirror) / "v3" / "ledgers"
    want = {str(e["episode_id"]) for e in episodes}
    feats: dict[str, dict[str, Any]] = {}
    prior: list[tuple[float, dict[str, Any]]] = []
    for row in _read_jsonl(led / "opportunity.jsonl"):
        ep = str(row.get("episode_id") or "")
        snap = row.get("feature_snapshot_at_signal") or {}
        if ep in want and ep not in feats:
            feats[ep] = snap
        obs = _num(snap.get("observed_ts"))
        if obs is not None and snap.get("causal_snapshot_phase") == "PRE_AI_DECISION":
            prior.append((obs, snap))
    prior.sort(key=lambda x: x[0])
    prior_ts = [p[0] for p in prior]
    scores: dict[str, tuple[float, float]] = {}
    for row in _read_jsonl(led / "decision.jsonl"):
        ep = str(row.get("episode_id") or "")
        if ep in want:
            ls, ss = _num(row.get("long_score")), _num(row.get("short_score"))
            if ls is not None and ss is not None:
                scores.setdefault(ep, (ls, ss))
    if xv is None:
        xv = load_cross_mids(mirror)
    out = []
    for e in episodes:
        ts = float(e["signal_ts"])
        f = feats.get(str(e["episode_id"])) or {}
        causal = f.get("causal_snapshot_phase") == "PRE_AI_DECISION"
        source = "OWN_PRE_DECISION_SNAPSHOT" if causal else "NONE"
        if not causal and prior_ts:
            # Cross-venue triggers carry no market snapshot: use the latest AI pre-decision snapshot observed
            # strictly before the trigger (3 m bar metrics), if it is at most PRIOR_SNAPSHOT_MAX_AGE_SEC old.
            j = bisect.bisect_left(prior_ts, ts) - 1
            if j >= 0 and ts - prior_ts[j] <= PRIOR_SNAPSHOT_MAX_AGE_SEC:
                f, causal, source = prior[j][1], True, "PRIOR_AI_SNAPSHOT"
        adx = _num(f.get("adx")) if causal else None
        volp = _num(f.get("volatility_percentile")) if causal else None
        sc = scores.get(str(e["episode_id"]))
        spread = None
        if tape is not None:
            i = int(math.floor(ts)) - 1 - int(tape.t0)
            if 0 <= i < len(tape.bid):
                b, a = float(tape.bid[i]), float(tape.ask[i])
                if b == b and a == a and a >= b > 0:
                    spread = round((a - b) / ((a + b) / 2) * 1e4, 3)
        lead, dev = cross_venue_state(xv, ts, str(e.get("direction") or ""))
        out.append({
            "episode_id": e["episode_id"], "signal_ts": ts, "episode_class": e.get("episode_class"),
            "ai_side": e.get("direction"), "causal_snapshot": causal, "context_source": source,
            "adx": adx, "vol_pct": volp,
            "score_gap": abs(sc[0] - sc[1]) if sc else None, "ai_confidence": max(sc) if sc else None,
            "spread_bp": spread, "session": session_of(ts), "xv_lead_bp": lead, "xv_premium_dev_bp": dev,
            "regime_label": e.get("regime"),
        })
    return out


def _rng(lo: float | None, hi: float | None) -> Callable[[Any], bool]:
    return lambda v: v is not None and (lo is None or v >= lo) and (hi is None or v < hi)


def _eq(*vals: Any) -> Callable[[Any], bool]:
    return lambda v: v in vals


# (axis, label) -> (context field, predicate). Labels are part of frozen forward-test rules: never rename.
FILTERS: dict[str, tuple[str, list[tuple[str, Callable[[Any], bool]]]]] = {
    "adx": ("adx", [("<18", _rng(None, 18)), ("18-20", _rng(18, 20)), ("18-30", _rng(18, 30)),
                    ("20-30", _rng(20, 30)), (">=20", _rng(20, None)), (">=30", _rng(30, None))]),
    "score_gap": ("score_gap", [("<10", _rng(None, 10)), ("10-40", _rng(10, 40)), (">=40", _rng(40, None)),
                                (">=55", _rng(55, None))]),
    "ai_confidence": ("ai_confidence", [("<50", _rng(None, 50)), ("50-65", _rng(50, 65)), (">=65", _rng(65, None)),
                                        (">=70", _rng(70, None))]),
    "vol_pct": ("vol_pct", [("<33", _rng(None, 33)), ("33-66", _rng(33, 66)), (">=66", _rng(66, None)),
                            ("<66", _rng(None, 66)), (">=33", _rng(33, None))]),
    "spread_bp": ("spread_bp", [("<=1.0", _rng(None, 1.0 + 1e-9)), ("1.0-2.0", _rng(1.0 + 1e-9, 2.0)),
                                (">=2.0", _rng(2.0, None)), ("<2.0", _rng(None, 2.0))]),
    "session": ("session", [("ASIA", _eq("ASIA")), ("EU", _eq("EU")), ("US", _eq("US"))]),
    "xv_lead": ("xv_lead_bp", [("AGREES", _rng(XV_LEAD_BP, None)), ("NEUTRAL", _rng(-XV_LEAD_BP, XV_LEAD_BP)),
                               ("OPPOSES", lambda v: v is not None and v < -XV_LEAD_BP)]),
    "xv_premium": ("xv_premium_dev_bp", [("PAYS_PREMIUM", _rng(XV_PREMIUM_DEV_BP, None)),
                                         ("NEUTRAL", _rng(-XV_PREMIUM_DEV_BP, XV_PREMIUM_DEV_BP)),
                                         ("GETS_DISCOUNT", lambda v: v is not None and v < -XV_PREMIUM_DEV_BP)]),
    "ai_class": ("episode_class", [("COMMITTED", _eq(*COMMITTED_CLASSES)),
                                   ("NO_TRADE_SCORE_LED", _eq("AI_NO_TRADE_SCORE_LED")),
                                   ("XVENUE_LEAD", _eq("XVENUE_LEAD")), ("XVENUE_PREMIUM", _eq("XVENUE_PREMIUM"))]),
    "ai_side": ("ai_side", [("LONG", _eq("LONG")), ("SHORT", _eq("SHORT"))]),
}
FILTER_DEFS_SHA = hashlib.sha256(json.dumps({a: [f, [lbl for lbl, _ in vals]] for a, (f, vals) in FILTERS.items()},
                                            sort_keys=True).encode()).hexdigest()[:16]


def _trend(c: Mapping[str, Any]) -> str | None:
    a = c.get("adx")
    return None if a is None else "SIDEWAYS_ADX<20" if a < 20 else "WEAK_TREND_ADX20-30" if a < 30 else "TREND_ADX>=30"


def _vol(c: Mapping[str, Any]) -> str | None:
    v = c.get("vol_pct")
    return None if v is None else "LOW_VOL<33" if v < 33 else "MID_VOL33-66" if v < 66 else "HIGH_VOL>=66"


def _spread(c: Mapping[str, Any]) -> str | None:
    s = c.get("spread_bp")
    return None if s is None else "TIGHT<=1.5bp" if s <= 1.5 else "WIDE>1.5bp"


def _xv(c: Mapping[str, Any]) -> str | None:
    v = c.get("xv_lead_bp")
    return None if v is None else "XV_AGREES" if v >= XV_LEAD_BP else "XV_OPPOSES" if v < -XV_LEAD_BP else "XV_NEUTRAL"


REGIMES: dict[str, Callable[[Mapping[str, Any]], str | None]] = {
    "trend_x_vol": lambda c: f"{_trend(c)}|{_vol(c)}" if _trend(c) and _vol(c) else None,
    "trend": _trend, "vol": _vol, "session": lambda c: c.get("session"), "spread": _spread, "xvenue": _xv,
}
META_REGIMES = ("trend_x_vol", "trend", "vol", "session")


def filter_masks(ctx: list[Mapping[str, Any]]) -> dict[tuple[str, str], np.ndarray]:
    out = {}
    for axis, (field, vals) in FILTERS.items():
        col = [c.get(field) for c in ctx]
        for label, pred in vals:
            m = np.array([bool(pred(v)) for v in col], dtype=bool)
            if m.any():
                out[(axis, label)] = m
    return out


def rule_mask(ctx: list[Mapping[str, Any]], filters: Iterable[Iterable[str]], regime: Iterable[str] | None = None) -> np.ndarray | None:
    """Episode mask of a frozen rule's conditions; None when a referenced filter no longer exists."""
    m = np.ones(len(ctx), dtype=bool)
    for axis, label in filters:
        spec = FILTERS.get(axis)
        pred = dict(spec[1]).get(label) if spec else None
        if pred is None:
            return None
        m &= np.array([bool(pred(c.get(spec[0]))) for c in ctx], dtype=bool)
    if regime:
        name, cell = list(regime)
        fn = REGIMES.get(name)
        if fn is None:
            return None
        m &= np.array([fn(c) == cell for c in ctx], dtype=bool)
    return m


# ------------------------------------------------------------------ policies

def policy_meta(entry: Mapping[str, Any], prot: Mapping[str, Any], rule: str, chases: Mapping[str, Any]) -> dict[str, Any]:
    lp, pp = prot["loss_protection"], prot["profit_protection"]
    tags = {rule}
    off = float(entry["offset_pct"])
    if off == 0:
        tags.add("TAKER")
    else:
        tags.add("MAKER")
        tags.add(f"TTL_{int(entry['ttl_sec'])}")
        if off <= 0.15:
            tags.add("OFFSET_LE_0.15")
        if off >= 0.30:
            tags.add("OFFSET_GE_0.30")
        windows, step, _ = chases[entry["chase_id"]]
        tags.add("NO_CHASE" if not windows or step <= 0 else f"CHASE_STEP_{int(round(step * 100))}")
    groups = {_FAMILY_OF.get(str(prot.get("policy_family")), str(prot.get("policy_family")))}
    if pp.get("ladder"):
        groups.add("PROFIT_LADDER")
    if lp.get("thesis_cut_margin_pct") is not None:
        groups.add("THESIS_CUT")
    if pp.get("break_even_arm_mfe_pct") is not None or pp.get("break_even_arm_atr_k") is not None:
        groups.add("BREAK_EVEN_ARM")
    if lp.get("time_stop_min") is not None:
        groups.add("TIME_STOP")
    if pp.get("partial_take_profits"):
        groups.add("PARTIALS")
    if lp.get("atr_stop_k") is not None:
        groups.add("ATR_STOP")
    if lp.get("atr_stop_k") is None and lp.get("thesis_cut_margin_pct") is None:
        groups.add("HARD_STOP_ONLY")
    if pp.get("atr_tp_k") is not None:
        tags.add("ATR_TARGET")
    tags |= groups
    return {"policy_id": f"{rule}|{entry['entry_id']}|{prot['protection_id']}", "direction_rule": rule,
            "policy_family": prot.get("policy_family"), "entry_id": entry["entry_id"], "offset_pct": off,
            "chase_id": entry["chase_id"], "ttl_sec": entry["ttl_sec"], "protection_id": prot["protection_id"],
            "hard_stop_margin_pct": lp.get("hard_stop_margin_pct"), "time_stop_min": lp.get("time_stop_min"),
            "groups": sorted(groups), "tags": sorted(tags)}


class Grid:
    """REALISTIC_V1 per-episode pnl (NaN = no fill) for every headline policy, plus decision-time context."""

    def __init__(self, pnl: np.ndarray, ts: np.ndarray, meta: list[dict[str, Any]], ctx: list[dict[str, Any]],
                 cohort: str, *, cluster_sec: int = CLUSTER_SEC, blocks: tuple[int, int] = (DAY_SEC, HALF_DAY_SEC)):
        self.cluster_sec, self.blocks = int(cluster_sec), (int(blocks[0]), int(blocks[1]))
        self.P = np.asarray(pnl, dtype=np.float64)
        self.F = ~np.isnan(self.P)
        self.P0 = np.where(self.F, self.P, 0.0)
        self.Ff = self.F.astype(np.float64)
        self.ts = np.asarray(ts, dtype=np.float64)
        self.hours = np.floor(self.ts / self.cluster_sec).astype(np.int64)
        self.days = np.floor(self.ts / DAY_SEC).astype(np.int64)
        self.meta, self.ctx, self.cohort = meta, ctx, cohort
        self.index = {m["policy_id"]: k for k, m in enumerate(meta)}
        self.filters = filter_masks(ctx)
        self.all = np.ones(len(self.ts), dtype=bool)

    @classmethod
    def from_matrix(cls, matrix: Mapping[str, Any], keys: list[tuple], entries: list[dict], protections: Mapping[str, Any],
                    chases: Mapping[str, Any], world: str, ctx: list[dict[str, Any]], cohort: str,
                    **kw: Any) -> "Grid":
        cols = [k for k, key in enumerate(keys) if key[3] == world]
        vals = matrix["values"][:, cols, 0]
        codes = matrix["codes"][:, cols]
        meta = [policy_meta(entries[keys[k][0]], protections[keys[k][1]], keys[k][2], chases) for k in cols]
        return cls(np.where(codes >= 0, vals, np.nan), matrix["ts"], meta, ctx, cohort, **kw)

    def pool(self, pred: Callable[[Mapping[str, Any]], bool] | None = None) -> np.ndarray:
        return np.array([k for k, m in enumerate(self.meta) if pred is None or pred(m)], dtype=np.int64)

    def span_days(self, mask: np.ndarray) -> float:
        t = self.ts[mask]
        if not len(t):
            return 0.0
        return float(sum(min(1.0, (t[self.days[mask] == d].max() - d * DAY_SEC) / DAY_SEC + 1e-9) if d == self.days[mask].max()
                         else 1.0 for d in np.unique(self.days[mask])))

    def scan(self, mask: np.ndarray, pool: np.ndarray, *, min_fills: int = MIN_TRAIN_FILLS,
             min_eff: int = MIN_EFF_TRADES) -> dict[str, Any] | None:
        """Best train-EV policy in ``pool`` over episodes in ``mask`` with >= min_fills and >= min_eff 1 h clusters."""
        if not mask.any() or not len(pool):
            return None
        m = mask.astype(np.float64)
        s, f = m @ self.P0[:, pool], m @ self.Ff[:, pool]
        ev = np.where(f >= min_fills, s / np.maximum(f, 1.0), -np.inf)
        for j in np.argsort(-ev, kind="stable")[:TOP_CHECK]:
            if not np.isfinite(ev[j]):
                break
            k = int(pool[j])
            eff = len(np.unique(self.hours[mask & self.F[:, k]]))
            if eff >= min_eff:
                return {"k": k, "ev_usd": float(ev[j]), "fills": int(f[j]), "clusters": eff}
        return None

    def apply(self, mask: np.ndarray, k: int) -> tuple[np.ndarray, np.ndarray]:
        hit = mask & self.F[:, k]
        return self.P[hit, k], self.ts[hit]


def select(g: Grid, train: np.ndarray, pool: np.ndarray, *, max_filters: int = 0, base: np.ndarray | None = None,
           axes: Iterable[str] | None = None, force_first: bool = False, min_fills: int = MIN_TRAIN_FILLS,
           min_eff: int = MIN_EFF_TRADES) -> dict[str, Any] | None:
    """Greedy forward selection on TRAIN only: best policy, then add the filter that raises train EV most."""
    mask = train & (g.all if base is None else base)
    best = g.scan(mask, pool, min_fills=min_fills, min_eff=min_eff)
    tried = len(pool)
    allowed = None if axes is None else set(axes)
    chosen: list[tuple[str, str]] = []
    for depth in range(max_filters):
        cand = None
        for key, fmask in g.filters.items():
            if key[0] in {c[0] for c in chosen} or (allowed is not None and key[0] not in allowed):
                continue
            r = g.scan(mask & fmask, pool, min_fills=min_fills, min_eff=min_eff)
            tried += len(pool)
            if r and (cand is None or r["ev_usd"] > cand[1]["ev_usd"]):
                cand = (key, r)
        if cand is None:
            break
        forced = force_first and depth == 0
        if not forced and best is not None and cand[1]["ev_usd"] <= best["ev_usd"] + MIN_FILTER_GAIN_USD:
            break
        chosen.append(cand[0])
        mask = mask & g.filters[cand[0]]
        best = cand[1]
    if best is None:
        return None
    return best | {"filters": chosen, "configs_tried": tried, "policy_id": g.meta[best["k"]]["policy_id"]}


def nested_walk_forward(g: Grid, pool: np.ndarray, *, max_filters: int = 0, base: np.ndarray | None = None,
                        axes: Iterable[str] | None = None, force_first: bool = False, fixed: Mapping[str, Any] | None = None,
                        min_fills: int = MIN_TRAIN_FILLS, min_eff: int = MIN_EFF_TRADES,
                        block_sec: int | None = None) -> dict[str, Any]:
    """Nested walk-forward by UTC block (day by default): selection redone on all prior blocks, the next scored once."""
    block_sec = int(block_sec or g.blocks[0])
    b = g.all if base is None else base
    blocks = np.floor(g.ts / block_sec).astype(np.int64)
    last_ts = float(g.ts.max()) if len(g.ts) else 0.0
    folds, pnl, tts = [], [], []
    span = 0.0
    tried = 0
    for d in sorted(set(blocks[b].tolist()))[1:]:
        tr, te = blocks < d, (blocks == d) & b
        sel = dict(fixed) if fixed else select(g, tr, pool, max_filters=max_filters, base=base, axes=axes,
                                               force_first=force_first, min_fills=min_fills, min_eff=min_eff)
        day = time.strftime("%Y-%m-%dT%HZ" if block_sec < DAY_SEC else "%Y-%m-%d", time.gmtime(d * block_sec))
        if not sel:
            folds.append({"test_day_utc": day, "status": "NO_ELIGIBLE_POLICY"})
            continue
        tried += int(sel.get("configs_tried") or 0)
        m = te.copy()
        for key in sel.get("filters") or []:
            m &= g.filters.get(tuple(key), np.zeros(len(m), dtype=bool))
        p, t = g.apply(m, int(sel["k"]))
        pnl.extend(p.tolist())
        tts.extend(t.tolist())
        span += min(block_sec, max(0.0, last_ts - d * block_sec)) / DAY_SEC
        folds.append({"test_day_utc": day, "status": "SCORED", "policy_id": g.meta[int(sel["k"])]["policy_id"],
                      "filters": [list(k) for k in sel.get("filters") or []],
                      "train_ev_bp": bp(sel.get("ev_usd")), "train_fills": sel.get("fills"),
                      "test_fills": len(p), "test_net_usd": round(float(np.sum(p)), 6) if len(p) else 0.0,
                      "test_ev_bp": bp(float(np.mean(p))) if len(p) else None})
    return {"block_sec": block_sec, "folds": folds, "oos": summarize(pnl, tts, span, g.cluster_sec), "configs_tried_all_folds": tried,
            "_pnl": np.asarray(pnl), "_ts": np.asarray(tts)}


def _public(res: Mapping[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in res.items() if not k.startswith("_")}


def sharpe_variance(g: Grid, pool: np.ndarray, masks: list[np.ndarray]) -> tuple[float, int]:
    """Variance of per-trade Sharpe across every (policy x mask) configuration with >= MIN_TRAIN_FILLS fills."""
    srs = []
    for m in masks:
        mf = m.astype(np.float64)
        f = mf @ g.Ff[:, pool]
        s = mf @ g.P0[:, pool]
        ss = mf @ (g.P0[:, pool] ** 2)
        ok = f >= MIN_TRAIN_FILLS
        mean = s[ok] / f[ok]
        var = (ss[ok] - f[ok] * mean ** 2) / np.maximum(f[ok] - 1, 1)
        good = var > 0
        srs.extend((mean[good] / np.sqrt(var[good])).tolist())
    return (float(np.var(srs, ddof=1)) if len(srs) > 1 else 0.0), len(srs)


# ------------------------------------------------------------------ studies

def _structure_specs(g: Grid) -> list[dict[str, Any]]:
    committed = g.filters.get(("ai_class", "COMMITTED"))
    notrade = g.filters.get(("ai_class", "NO_TRADE_SCORE_LED"))
    allp = g.pool()
    specs = [
        {"id": "S0_ANY_POLICY", "rule": "best train-EV policy, no filter", "pool": allp},
        {"id": "S1_ANY_POLICY_PLUS_1_FILTER", "rule": "best policy + at most 1 context filter", "pool": allp, "max_filters": 1},
        {"id": "S3_ANY_POLICY_PLUS_UP_TO_3_FILTERS", "rule": "greedy: best policy + up to 3 context filters",
         "pool": allp, "max_filters": 3},
        {"id": "D_FADE_ONLY", "rule": "best FADE policy, no filter", "pool": g.pool(lambda m: m["direction_rule"] == "FADE")},
        {"id": "D_FOLLOW_ONLY", "rule": "best FOLLOW policy, no filter", "pool": g.pool(lambda m: m["direction_rule"] == "FOLLOW")},
    ]
    if committed is not None:
        specs += [{"id": "C0_COMMITTED_ONLY", "rule": "committed AI calls only, best policy", "pool": allp, "base": committed},
                  {"id": "C2_COMMITTED_PLUS_UP_TO_2_FILTERS", "rule": "committed only + up to 2 filters", "pool": allp,
                   "base": committed, "max_filters": 2},
                  {"id": "CF_COMMITTED_FADE", "rule": "committed only, best FADE policy", "base": committed,
                   "pool": g.pool(lambda m: m["direction_rule"] == "FADE")}]
    if notrade is not None:
        specs.append({"id": "N0_NO_TRADE_SCORE_LED_ONLY", "rule": "no-trade score-led calls only, best policy",
                      "pool": allp, "base": notrade})
    return specs


def ingredient_verdicts(g: Grid, block_sec: int | None = None) -> list[dict[str, Any]]:
    """Nested OOS of the best-policy search restricted to policies WITH an ingredient vs WITHOUT it, and axes.

    Verdicts need >= 2 compared folds agreeing in majority; 12 h blocks are used because ~4 UTC days give at most
    two scored day folds once the first train window holds 30 independent hours.
    """
    block_sec = int(block_sec or g.blocks[1])
    out = []
    allp = g.pool()
    baseline = nested_walk_forward(g, allp, block_sec=block_sec)
    for tag, desc in INGREDIENTS.items():
        with_p = g.pool(lambda m, t=tag: t in m["tags"])
        without = g.pool(lambda m, t=tag: t not in m["tags"])
        if not len(with_p) or not len(without):
            out.append({"ingredient": tag, "kind": "EXIT_OR_ENTRY", "description": desc, "verdict": "NOT_IN_GRID"})
            continue
        w, wo = nested_walk_forward(g, with_p, block_sec=block_sec), nested_walk_forward(g, without, block_sec=block_sec)
        out.append(_verdict_row(tag, "EXIT_OR_ENTRY", desc, w, wo, len(with_p), len(without), g, with_p, without))
    for axis in FILTERS:
        if not any(k[0] == axis for k in g.filters):
            out.append({"ingredient": f"FILTER:{axis}", "kind": "REGIME_FILTER", "verdict": "NO_DATA"})
            continue
        w = nested_walk_forward(g, allp, max_filters=1, axes=[axis], force_first=True, block_sec=block_sec)
        out.append(_verdict_row(f"FILTER:{axis}", "REGIME_FILTER",
                                f"best policy + the train-best value of {axis} (forced) vs no filter", w, baseline,
                                len(allp), len(allp), g, None, None))
    return out


def _verdict_row(tag, kind, desc, w, wo, n_with, n_without, g, with_p, without) -> dict[str, Any]:
    fw = {f["test_day_utc"]: f for f in w["folds"] if f["status"] == "SCORED"}
    fo = {f["test_day_utc"]: f for f in wo["folds"] if f["status"] == "SCORED"}
    deltas = [(fw[d]["test_ev_bp"] or 0) - (fo[d]["test_ev_bp"] or 0) for d in fw if d in fo
              and fw[d]["test_ev_bp"] is not None and fo[d]["test_ev_bp"] is not None]
    ew, eo = w["oos"]["ev_bp"], wo["oos"]["ev_bp"]
    delta = None if ew is None or eo is None else round(ew - eo, 2)
    pos, neg = sum(d > 0 for d in deltas), sum(d < 0 for d in deltas)
    if delta is None or not deltas:
        verdict = "INSUFFICIENT"
    elif len(deltas) < 2:
        verdict = "INSUFFICIENT_FOLDS"
    elif delta > 0 and pos > len(deltas) / 2:
        verdict = "HELPS_OOS"
    elif delta < 0 and neg > len(deltas) / 2:
        verdict = "HURTS_OOS"
    else:
        verdict = "NO_CONSISTENT_EFFECT"
    insample = None
    if with_p is not None and without is not None:
        ev = np.where(g.Ff.sum(0) > 0, g.P0.sum(0) / np.maximum(g.Ff.sum(0), 1), np.nan)
        insample = round((float(np.nanmean(ev[with_p])) - float(np.nanmean(ev[without]))) / NOTIONAL_USD * 1e4, 2)
    return {"ingredient": tag, "kind": kind, "description": desc, "policies_with": n_with, "policies_without": n_without,
            "nested_oos_ev_bp_with": ew, "nested_oos_ev_bp_without": eo, "delta_bp": delta,
            "oos_fills_with": w["oos"]["fills"], "oos_fills_without": wo["oos"]["fills"],
            "ci_bp_with": w["oos"]["ci_bp"], "n_eff_with": w["oos"]["n_eff"],
            "block_sec": w.get("block_sec"), "folds_compared": len(deltas), "folds_better": pos, "folds_worse": neg,
            "insample_mean_policy_ev_delta_bp": insample, "verdict": verdict}


def marginal_effects(g: Grid) -> list[dict[str, Any]]:
    """Descriptive (all data) effect of each axis value: per-policy EV change vs unfiltered, median across policies."""
    allp = g.pool()
    f_all, s_all = g.Ff.sum(0), g.P0.sum(0)
    ev_all = np.where(f_all >= MIN_TRAIN_FILLS, s_all / np.maximum(f_all, 1), np.nan)
    refs = {}
    for rule in ("FADE", "FOLLOW"):
        k = g.index.get(f"{rule}|TAKER_AT_SIGNAL|REGISTRY_TIME_3600_HARD40BP")
        if k is not None:
            refs[rule] = k
    rows = []
    for (axis, label), m in g.filters.items():
        mf = m.astype(np.float64)
        f, s = mf @ g.Ff, mf @ g.P0
        ev = np.where(f >= MIN_TRAIN_FILLS, s / np.maximum(f, 1), np.nan)
        d = (ev - ev_all)[allp]
        d = d[~np.isnan(d)]
        row = {"axis": axis, "value": label, "episodes": int(m.sum()), "label": "IN_SAMPLE_DESCRIPTIVE",
               "policies_compared": int(len(d)),
               "median_policy_ev_delta_bp": round(float(np.median(d)) / NOTIONAL_USD * 1e4, 2) if len(d) else None,
               "share_policies_improved": round(float((d > 0).mean()), 3) if len(d) else None}
        for rule, k in refs.items():
            p, t = g.apply(m, k)
            sm = summarize(p, t, None, g.cluster_sec)
            row[f"ref_{rule.lower()}_taker_time60"] = {"fills": sm["fills"], "ev_bp": sm["ev_bp"], "ci_bp": sm["ci_bp"],
                                                       "n_eff": sm["n_eff"]}
        rows.append(row)
    return rows


def regime_map(g: Grid, block_sec: int | None = None) -> list[dict[str, Any]]:
    block_sec = int(block_sec or g.blocks[1])
    """Regime cell x family group: nested OOS EV of the train-best policy within that cell and group."""
    rows = []
    groups = {gname: g.pool(lambda m, n=gname: n in m["groups"]) for gname in FAMILY_GROUPS}
    groups["ALL_POLICIES"] = g.pool()
    for name, fn in REGIMES.items():
        labels = np.array([fn(c) or "" for c in g.ctx], dtype=object)
        for cell in sorted({x for x in labels.tolist() if x}):
            base = labels == cell
            for gname, pool in groups.items():
                if not len(pool):
                    continue
                res = nested_walk_forward(g, pool, base=base, min_fills=MIN_CELL_FILLS, min_eff=MIN_CELL_EFF,
                                          block_sec=block_sec)
                ins = g.scan(base, pool, min_fills=MIN_CELL_FILLS, min_eff=MIN_CELL_EFF)
                o = res["oos"]
                rows.append({"regime": name, "cell": cell, "group": gname, "episodes": int(base.sum()), "block_sec": block_sec,
                             "oos_fills": o["fills"], "oos_ev_bp": o["ev_bp"], "oos_ci_bp": o["ci_bp"],
                             "oos_n_eff": o["n_eff"], "oos_net_usd": o["net_pnl_usd"],
                             "folds_scored": sum(f["status"] == "SCORED" for f in res["folds"]),
                             "insample_best_policy": g.meta[ins["k"]]["policy_id"] if ins else None,
                             "insample_ev_bp": bp(ins["ev_usd"]) if ins else None,
                             "insample_fills": ins["fills"] if ins else None})
    return rows


def learn_meta(g: Grid, regime: str, train: np.ndarray, pool: np.ndarray) -> dict[str, Any]:
    fn = REGIMES[regime]
    labels = np.array([fn(c) or "" for c in g.ctx], dtype=object)
    mapping: dict[str, Any] = {}
    for cell in sorted({x for x in labels[train].tolist() if x}):
        r = g.scan(train & (labels == cell), pool, min_fills=MIN_CELL_FILLS, min_eff=MIN_CELL_EFF)
        mapping[cell] = ({"policy_id": g.meta[r["k"]]["policy_id"], "train_ev_bp": bp(r["ev_usd"]),
                          "train_fills": r["fills"]} if r and r["ev_usd"] > 0 else None)
    return {"regime": regime, "mapping": mapping, "labels": labels}


def apply_meta(g: Grid, mapping: Mapping[str, Any], labels: np.ndarray, mask: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    pnl, ts = [], []
    for cell, sel in mapping.items():
        if not sel or sel["policy_id"] not in g.index:
            continue
        p, t = g.apply(mask & (labels == cell), g.index[sel["policy_id"]])
        pnl.extend(p.tolist())
        ts.extend(t.tolist())
    return np.asarray(pnl), np.asarray(ts)


def meta_policies(g: Grid, block_sec: int | None = None) -> list[dict[str, Any]]:
    block_sec = int(block_sec or g.blocks[1])
    """Regime-switching meta-policy: per regime cell the train-best positive policy, else stand aside (nested WF)."""
    allp = g.pool()
    out = []
    blocks = np.floor(g.ts / block_sec).astype(np.int64)
    last_ts = float(g.ts.max()) if len(g.ts) else 0.0
    for regime in META_REGIMES:
        folds, pnl, tts, span = [], [], [], 0.0
        for d in sorted(set(blocks.tolist()))[1:]:
            learned = learn_meta(g, regime, blocks < d, allp)
            te = blocks == d
            p, t = apply_meta(g, learned["mapping"], learned["labels"], te)
            pnl.extend(p.tolist())
            tts.extend(t.tolist())
            span += min(block_sec, max(0.0, last_ts - d * block_sec)) / DAY_SEC
            folds.append({"test_block_utc": time.strftime("%Y-%m-%dT%HZ", time.gmtime(d * block_sec)),
                          "cells_traded": sum(1 for v in learned["mapping"].values() if v),
                          "cells_stand_aside": sum(1 for v in learned["mapping"].values() if not v),
                          "test_fills": len(p), "test_ev_bp": bp(float(np.mean(p))) if len(p) else None})
        full = learn_meta(g, regime, g.all, allp)
        p_in, t_in = apply_meta(g, full["mapping"], full["labels"], g.all)
        out.append({"id": f"META_{regime.upper()}", "regime": regime, "block_sec": block_sec, "folds": folds,
                    "nested_oos": summarize(pnl, tts, span, g.cluster_sec), "full_data_mapping": full["mapping"],
                    "full_data_in_sample": summarize(p_in, t_in, g.span_days(g.all), g.cluster_sec),
                    "_pnl": np.asarray(pnl), "_ts": np.asarray(tts)})
    return out


def family_table(g: Grid, rows: list[Mapping[str, Any]] | None, world: str) -> list[dict[str, Any]]:
    """Per family/ingredient group: best train-selected holdout row (genome 70/30) + nested WF within the group."""
    out = []
    by_id = {r["policy_id"]: r for r in rows or [] if r.get("fill_world") == world}
    for gname, desc in FAMILY_GROUPS.items():
        pool = g.pool(lambda m, n=gname: n in m["groups"])
        row: dict[str, Any] = {"group": gname, "description": desc, "policies": int(len(pool))}
        if not len(pool):
            out.append(row | {"status": "NOT_IN_GRID"})
            continue
        members = [by_id[g.meta[k]["policy_id"]] for k in pool if g.meta[k]["policy_id"] in by_id]
        ranked = [r for r in members if r.get("holdout_verdict") != "INSUFFICIENT"]
        best = max(ranked, key=lambda r: r["train"]["ev_per_fill_usd"] or -1e9) if ranked else None
        best_oos = max(members, key=lambda r: r["oos"]["net_pnl_usd"]) if members else None
        wf = nested_walk_forward(g, pool)
        row |= {"status": "EVALUATED", "confirmed_policies": sum(r.get("holdout_verdict") == "CONFIRMED" for r in members),
                "best_train_policy": best["policy_id"] if best else None,
                "best_train_ev_bp": bp(best["train"]["ev_per_fill_usd"]) if best else None,
                "best_train_holdout_ev_bp": bp(best["oos"]["ev_per_fill_usd"]) if best else None,
                "best_train_holdout_fills": best["oos"]["fills"] if best else None,
                "best_train_holdout_verdict": best["holdout_verdict"] if best else None,
                "best_oos_net_policy": best_oos["policy_id"] if best_oos else None,
                "best_oos_net_usd": best_oos["oos"]["net_pnl_usd"] if best_oos else None,
                "nested_wf_ev_bp": wf["oos"]["ev_bp"], "nested_wf_ci_bp": wf["oos"]["ci_bp"],
                "nested_wf_fills": wf["oos"]["fills"], "nested_wf_n_eff": wf["oos"]["n_eff"],
                "nested_wf_folds": [f.get("policy_id") for f in wf["folds"]]}
        out.append(row)
    return out


def in_sample_top(g: Grid, limit: int = 20) -> list[dict[str, Any]]:
    """Best in-sample combos (policy alone and policy + 1 filter) with >= MIN fills and clusters. LABELLED IN-SAMPLE."""
    cands = []
    allp = g.pool()
    masks = [((), g.all)] + [((k,), m) for k, m in g.filters.items()]
    for fkeys, m in masks:
        mf = m.astype(np.float64)
        f, s = mf @ g.Ff[:, allp], mf @ g.P0[:, allp]
        ev = np.where(f >= MIN_TRAIN_FILLS, s / np.maximum(f, 1), -np.inf)
        for j in np.argsort(-ev)[:15]:
            if not np.isfinite(ev[j]) or ev[j] <= 0:
                break
            k = int(allp[j])
            if len(np.unique(g.hours[m & g.F[:, k]])) < MIN_EFF_TRADES:
                continue
            cands.append((float(ev[j]), k, fkeys, int(f[j])))
    cands.sort(key=lambda c: -c[0])
    seen, out = set(), []
    for ev, k, fkeys, fills in cands:
        key = (k, fkeys)
        if key in seen:
            continue
        seen.add(key)
        m = g.all.copy()
        for fk in fkeys:
            m &= g.filters[fk]
        p, t = g.apply(m, k)
        out.append({"label": "IN_SAMPLE_TOP", "policy_id": g.meta[k]["policy_id"], "filters": [list(x) for x in fkeys],
                    "in_sample": summarize(p, t, g.span_days(g.all), g.cluster_sec)})
        if len(out) >= limit:
            break
    return out


def regime_winners(g: Grid, regime_rows: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for r in regime_rows:
        if r["group"] == "ALL_POLICIES" and r.get("insample_best_policy") and (r.get("insample_ev_bp") or 0) > 0:
            out.append({"label": "REGIME_WINNER", "policy_id": r["insample_best_policy"], "filters": [],
                        "regime": [r["regime"], r["cell"]], "insample_ev_bp": r["insample_ev_bp"],
                        "insample_fills": r["insample_fills"]})
    return out


def mix_match(g: Grid, rows: list[Mapping[str, Any]] | None = None, world: str = "REALISTIC_V1",
              *, full: bool = True) -> dict[str, Any]:
    """Every study over one cohort grid. ``full=False`` skips the regime map / ingredients (cheap cohorts)."""
    started = time.time()
    structures = []
    total_configs = 0
    for spec in _structure_specs(g):
        wf = nested_walk_forward(g, spec["pool"], max_filters=spec.get("max_filters", 0), base=spec.get("base"))
        wff = nested_walk_forward(g, spec["pool"], max_filters=spec.get("max_filters", 0), base=spec.get("base"),
                                   block_sec=g.blocks[1])
        fd = select(g, g.all, spec["pool"], max_filters=spec.get("max_filters", 0), base=spec.get("base"))
        total_configs += int((fd or {}).get("configs_tried") or 0)
        rule, in_s = None, None
        if fd:
            m = (g.all if spec.get("base") is None else spec["base"]).copy()
            for fk in fd["filters"]:
                m &= g.filters[fk]
            p, t = g.apply(m, fd["k"])
            in_s = summarize(p, t, g.span_days(g.all), g.cluster_sec)
            rule = {"policy_id": fd["policy_id"], "policy": g.meta[fd["k"]],
                    "base": spec["id"].split("_")[0] in ("C0", "C2", "CF") and "COMMITTED_ONLY"
                    or spec["id"].startswith("N0") and "NO_TRADE_SCORE_LED_ONLY" or "ALL_EPISODES",
                    "filters": [list(x) for x in fd["filters"]]}
        structures.append({"id": spec["id"], "procedure": spec["rule"], "pool_policies": int(len(spec["pool"])),
                           "max_filters": spec.get("max_filters", 0), "nested_oos": wf["oos"], "folds": wf["folds"],
                           "nested_oos_fine": wff["oos"], "folds_fine": wff["folds"],
                           "configs_tried_nested": wf["configs_tried_all_folds"] + wff["configs_tried_all_folds"],
                           "full_data_rule": rule, "base_mask_id": spec["id"],
                           "full_data_in_sample": in_s, "_pnl": wf["_pnl"], "_ts": wf["_ts"],
                           "_pnlf": wff["_pnl"], "_tsf": wff["_ts"]})
    allp = g.pool()
    sr_var, n_sr = sharpe_variance(g, allp, [g.all] + list(g.filters.values()))
    n_trials = max(total_configs, n_sr)
    for s in structures:
        s["deflation"] = deflated_sharpe(s["_pnl"], n_trials, sr_var, s["nested_oos"]["n_eff"] or 0) if len(s["_pnl"]) else {"status": "NO_OOS"}
        s["deflation_fine"] = (deflated_sharpe(s["_pnlf"], n_trials, sr_var, s["nested_oos_fine"]["n_eff"] or 0)
                              if len(s["_pnlf"]) else {"status": "NO_OOS"})
    out: dict[str, Any] = {
        "schema": SCHEMA, "cohort": g.cohort, "fill_world": world, "evidence_label": "SIMULATED_COUNTERFACTUAL",
        "filter_defs": {"version": FILTER_DEFS_VERSION, "sha": FILTER_DEFS_SHA,
                        "axes": {a: {"field": f, "values": [lbl for lbl, _ in v]} for a, (f, v) in FILTERS.items()}},
        "cluster_sec": g.cluster_sec, "fold_blocks_sec": {"primary": g.blocks[0], "fine": g.blocks[1]},
        "episodes": int(len(g.ts)), "policies": int(len(g.meta)), "utc_days": sorted({time.strftime("%Y-%m-%d", time.gmtime(d * DAY_SEC)) for d in set(g.days.tolist())}),
        "context_coverage": {f: int(sum(c.get(f) is not None for c in g.ctx)) for f in
                             ("adx", "vol_pct", "score_gap", "ai_confidence", "spread_bp", "xv_lead_bp", "xv_premium_dev_bp")},
        "multiple_testing": {"configurations_searched_full_data": total_configs, "sharpe_trials": n_sr,
                             "trials_used_for_deflation": n_trials, "sharpe_var_across_trials": round(sr_var, 6)},
        "warning": ("Cherry-picking the best value of each axis from the same data overfits. Only nested_oos "
                    "(selection redone on prior UTC days, next day scored once) counts; in-sample rows are labelled."),
        "structures": [_public(s) for s in structures],
    }
    if full:
        out["marginal_effects"] = marginal_effects(g)
        out["ingredient_verdicts"] = ingredient_verdicts(g)
        rmap = regime_map(g)
        out["regime_map"] = rmap
        metas = meta_policies(g)
        for m in metas:
            m["deflation"] = deflated_sharpe(m["_pnl"], n_trials + len(META_REGIMES), sr_var, m["nested_oos"]["n_eff"] or 0) if len(m["_pnl"]) else {"status": "NO_OOS"}
        out["meta_policies"] = [_public(m) for m in metas]
        out["family_table"] = family_table(g, rows, world)
        out["in_sample_top"] = in_sample_top(g)
        out["regime_winners"] = regime_winners(g, rmap)
    cands = [{"id": s["id"], "kind": "STRUCTURE", "nested_oos": s["nested_oos"], "nested_oos_fine": s["nested_oos_fine"],
              "full_data_rule": s["full_data_rule"], "deflation": s.get("deflation_fine")} for s in out["structures"]]
    cands += [{"id": m["id"], "kind": "META_POLICY", "nested_oos": None, "nested_oos_fine": m["nested_oos"],
               "full_data_rule": {"regime": m["regime"], "mapping": m["full_data_mapping"]}, "deflation": m.get("deflation")}
              for m in out.get("meta_policies") or []]
    ranked = sorted((c for c in cands if (c["nested_oos_fine"].get("n_eff") or 0) >= MIN_EFF_TRADES
                     and c["nested_oos_fine"].get("ci_bp", [None])[0] is not None),
                    key=lambda c: -(c["nested_oos_fine"]["ci_bp"][0]))
    out["most_probable"] = ranked[:3]
    out["most_probable_note"] = ("ranked by fine-block (12 h AI / 6 h cross-venue) nested-OOS lower 95% CI among structures/meta-policies with "
                                 f"n_eff >= {MIN_EFF_TRADES}; choosing among them is itself a selection step")
    out["runtime_sec"] = round(time.time() - started, 1)
    return out

# ------------------------------------------------------------------ totals (dashboard / API)

TOTALS_SORT_KEY = "net_oos_usd"


def policy_totals(rows: list[Mapping[str, Any]], world: str, span_days: float) -> list[dict[str, Any]]:
    """Per-policy totals for every headline-world row, sorted by out-of-sample net $ (desc); train rank kept."""
    head = [r for r in rows if r.get("fill_world") == world]
    ranked = sorted((r for r in head if r.get("holdout_verdict") != "INSUFFICIENT"),
                    key=lambda r: -(r["train"].get("ev_per_fill_usd") if r["train"].get("ev_per_fill_usd") is not None else -1e9))
    rank = {r["policy_id"]: i + 1 for i, r in enumerate(ranked)}
    out = []
    for r in head:
        a, t, o = r.get("all") or {}, r.get("train") or {}, r.get("oos") or {}
        exit_ = r.get("exit") or {}
        out.append({
            "policy_id": r["policy_id"], "policy_family": r.get("policy_family"), "direction_rule": r.get("direction_rule"),
            "entry_id": (r.get("entry") or {}).get("entry_id"), "protection_id": exit_.get("protection_id"),
            "fill_world": world, "fills": int(a.get("fills") or 0), "wins": int(a.get("wins") or 0),
            "losses": int(a.get("losses") or 0), "win_rate_pct": a.get("win_rate_pct"),
            "net_pnl_usd": round(float(a.get("net_pnl_usd") or 0.0), 6),
            "net_in_sample_usd": round(float(t.get("net_pnl_usd") or 0.0), 6),
            "net_oos_usd": round(float(o.get("net_pnl_usd") or 0.0), 6),
            "in_sample_fills": int(t.get("fills") or 0), "oos_fills": int(o.get("fills") or 0),
            "avg_pnl_usd": a.get("ev_per_fill_usd"), "avg_pnl_bp": bp(a.get("ev_per_fill_usd")),
            "train_ev_bp": bp(t.get("ev_per_fill_usd")), "oos_ev_bp": bp(o.get("ev_per_fill_usd")),
            "max_drawdown_usd": a.get("max_drawdown_usd"),
            "trades_per_day": round(int(a.get("fills") or 0) / span_days, 2) if span_days else None,
            "holdout_verdict": r.get("holdout_verdict"), "train_rank": rank.get(r["policy_id"]),
        })
    out.sort(key=lambda x: (-x["net_oos_usd"], -x["net_pnl_usd"], x["policy_id"]))
    for i, x in enumerate(out):
        x["oos_net_rank"] = i + 1
    return out


def family_totals(totals: list[Mapping[str, Any]], g: "Grid") -> list[dict[str, Any]]:
    """Per family/ingredient group: policy counts and the group's top policy by out-of-sample net $ (same columns)."""
    groups_of = {m["policy_id"]: set(m["groups"]) for m in g.meta}
    out = []
    for gname, desc in FAMILY_GROUPS.items():
        members = [t for t in totals if gname in groups_of.get(t["policy_id"], ())]
        row: dict[str, Any] = {"group": gname, "description": desc, "policies": len(members),
                               "positive_oos_policies": sum(1 for t in members if t["net_oos_usd"] > 0)}
        if members:
            top = members[0]  # totals are sorted by net_oos_usd desc
            row |= {k: top[k] for k in ("policy_id", "fills", "wins", "losses", "win_rate_pct", "net_pnl_usd",
                                        "net_in_sample_usd", "net_oos_usd", "avg_pnl_usd", "avg_pnl_bp", "oos_ev_bp",
                                        "max_drawdown_usd", "trades_per_day", "holdout_verdict", "train_rank")}
        else:
            row |= {"policy_id": None, "net_oos_usd": None, "status": "NOT_IN_GRID"}
        out.append(row)
    out.sort(key=lambda r: (r.get("net_oos_usd") is None, -(r.get("net_oos_usd") or 0)))
    return out


def lane_totals(lanes: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return sorted((dict(x) for x in lanes), key=lambda x: (-(x.get("net_pnl_usd") or 0), str(x.get("lane"))))


def matrix_reconciliation(g: "Grid", totals: list[Mapping[str, Any]], limit: int = 100) -> dict[str, Any]:
    """Totals vs the per-trade outcome matrix: fills and net $ of the top rows must match the per-episode pnl."""
    worst_usd, worst_fills, checked, missing = 0.0, 0, 0, 0
    for t in totals[:limit]:
        k = g.index.get(t["policy_id"])
        if k is None:
            missing += 1
            continue
        p = g.P[g.F[:, k], k]
        worst_usd = max(worst_usd, abs(float(p.sum()) - float(t["net_pnl_usd"])))
        worst_fills = max(worst_fills, abs(int(len(p)) - int(t["fills"])))
        checked += 1
    return {"rows_checked": checked, "rows_missing_in_matrix": missing, "max_abs_net_diff_usd": round(worst_usd, 9),
            "max_abs_fill_diff": worst_fills, "status": "PASS" if checked and worst_usd < 1e-5 and worst_fills == 0 and not missing else "FAIL"}


# ------------------------------------------------------------------ cross-venue cohort

XV_DECISION_LATENCY_SEC = 8.93  # measured live median signal -> fill of Tiles 3/4 (XVENUE-INVERT-STUDY)
XV_TIME_EXIT_MIN = (1, 2, 5, 15, 60)
XV_TP_ATR = (0.5, 1.0)


def xvenue_episodes(gg: Any, mirror: Path, tape: Any) -> list[dict[str, Any]]:
    seen, out = set(), []
    for row in _read_jsonl(Path(mirror) / "v3" / "ledgers" / "opportunity.jsonl"):
        call = str(row.get("shared_ai_call_id") or "")
        did = gg.decision_identity(call)[0] if call else ""
        klass = gg.xvenue_class(did) if did else None
        ts, px = _num(row.get("signal_ts")), _num(row.get("signal_price"))
        if not klass or did in seen or ts is None or not px or row.get("raw_direction") not in ("LONG", "SHORT"):
            continue
        seen.add(did)
        feat = row.get("feature_snapshot_at_signal") or {}
        atr = _num(feat.get("atr14_pct_3m")) or gg.tape_atr14_pct(tape, ts) or 0.05
        out.append({"episode_id": str(row.get("episode_id")), "decision_id": did, "episode_class": klass,
                    "signal_ts": ts, "signal_price": px, "direction": row["raw_direction"], "atr14_pct": atr,
                    "regime": "UNKNOWN"})
    out.sort(key=lambda e: e["signal_ts"])
    return out


def xvenue_grid(gg: Any, mirror: Path, tape: Any, xv: Mapping[str, Any] | None) -> "Grid | None":
    """Tiles 3/4 triggers replayed REALISTIC_V1 (taker after measured xvenue latency) with short time/TP exits."""
    eps = xvenue_episodes(gg, mirror, tape)
    if not eps:
        return None
    entry = [{"entry_id": "TAKER_AT_SIGNAL", "offset_pct": 0.0, "chase_id": "no_chase", "ttl_sec": 0,
              "stages": ["PROTECTION_SWEEP"]}]
    prot: dict[str, dict[str, Any]] = {}
    for mins in XV_TIME_EXIT_MIN:
        pid = f"XV_TIME_{mins * 60}_HARD40BP"
        prot[pid] = {"protection_id": pid, "policy_family": "REGISTRY_TIME_EXIT",
                     "loss_protection": {"hard_stop_margin_pct": 40.0, "time_stop_min": float(mins)},
                     "profit_protection": {"mode": "ATR_TARGET", "atr_tp_k": None}}
    for tp in XV_TP_ATR:
        pid = f"XV_TP_{tp}ATR_TIME_300_HARD40BP"
        prot[pid] = {"protection_id": pid, "policy_family": "FIXED_TARGET",
                     "loss_protection": {"hard_stop_margin_pct": 40.0, "time_stop_min": 5.0},
                     "profit_protection": {"mode": "ATR_TARGET", "atr_tp_k": tp}}
    gg._init_worker(tape, entry, prot, XV_DECISION_LATENCY_SEC)
    res = [gg.evaluate_episode((i, e)) for i, e in enumerate(eps)]
    keys = gg.row_keys(entry, prot)
    good = [i for i, r in enumerate(res) if r["status"] == "EVALUATED"]
    if not good:
        return None
    heads = [k for k, key in enumerate(keys) if key[3] == gg.HEADLINE_WORLD]
    pnl = np.stack([np.where(res[i]["code"] >= 0, res[i]["values"][:, 0], np.nan) for i in good])[:, heads]
    meta = [policy_meta(entry[keys[k][0]], prot[keys[k][1]], keys[k][2], gg.CHASES) for k in heads]
    evs = [eps[i] for i in good]
    ctx = episode_context(mirror, evs, tape, xv)
    return Grid(pnl, np.array([e["signal_ts"] for e in evs]), meta, ctx, "XVENUE_EVALUATOR",
                cluster_sec=600, blocks=(HALF_DAY_SEC, HALF_DAY_SEC // 2))