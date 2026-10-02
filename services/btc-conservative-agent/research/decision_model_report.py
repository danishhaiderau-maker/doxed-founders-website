"""Decision-model research: snapshot stream health, regime shadow prompt (H8), logistic challenger (H9).

All three read ``decision_feature_snapshots.jsonl`` (SNAPSHOT + LABEL rows) and
``ai_shadow_regime_prompt.jsonl``; none of them can create an order.

H9 logistic challenger (laptop-side shadow, pre-registered 2026-10-02):
* target: sign of the 30-minute forward Bitfinex mid return (``fwd_ret_bp > 0``);
* features: ``LOGISTIC_FEATURES`` from the snapshot, standardised per fold with
  training-fold mean imputation;
* fit: L2 logistic (``LOGISTIC_L2``), Newton/IRLS, refit once per UTC day on
  every snapshot whose 30m label matured before that day (no look-ahead);
* scored out of sample only, from the 4th UTC day onward;
* gate after >= ``H9_MIN_OOS_DAYS`` OOS days and >= ``H9_MIN_OOS_PREDICTIONS``:
  PASS needs the 2h-cluster bootstrap 95% upper bound of Brier(model) -
  Brier(climatology) below zero AND >= ``H9_MIN_TRADES`` sign trades
  (|p - 0.5| >= ``H9_TRADE_EDGE``) with the cluster-bootstrap 95% lower bound
  of the mean 30m return net of one full quoted spread above zero;
* KILL when ``H9_MAX_OOS_DAYS`` OOS days pass without PASS. PASS only
  qualifies the challenger for review; it never trades and never changes a tile.
"""
from __future__ import annotations

import json
import math
import os
import random
from collections import Counter, defaultdict
from typing import Any, Iterable, Mapping, Optional

import numpy as np

import ai_regime_shadow as regime
import decision_feature_snapshots as dfs

SCHEMA = "decision_model_report_v1"
DAY_SEC = 86400
PRIMARY_HORIZON_MIN = 30
MIN_TRAIN_DAYS = 3
LOGISTIC_L2 = 1.0
H9_ID = "H9_LOGISTIC_CHALLENGER_30M_20261002"
H9_MIN_OOS_DAYS = 10
H9_MAX_OOS_DAYS = 14
H9_MIN_OOS_PREDICTIONS = 1500
H9_MIN_TRADES = 300
H9_TRADE_EDGE = 0.03
CLUSTER_SEC = 7200
RESAMPLES = 1000

LOGISTIC_FEATURES = (
    ("tape", "ret_1m_bp"), ("tape", "ret_5m_bp"), ("tape", "ret_15m_bp"), ("tape", "ret_60m_bp"),
    ("tape", "flow_1m"), ("tape", "flow_5m"), ("tape", "l1_imbalance"), ("tape", "spread_bp"),
    ("tape", "rv15_bp_10s"),
    ("compact_facts", "trend_score"), ("compact_facts", "adx15m"), ("compact_facts", "donchian_loc_3m"),
    ("compact_facts", "dist_high_atr"), ("compact_facts", "dist_low_atr"),
    ("compact_facts", "funding_bp_8h"), ("compact_facts", "oi_change_1h_pct"), ("compact_facts", "basis_bp"),
    ("premium", "premium_dev_bp"), ("premium", "lead_60s_bp"), ("premium", "lead_300s_bp"),
    ("leader", "leader_ret_bp"),
)


def _finite(value: Any) -> Optional[float]:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def load_rows(data_dir: str, filename: str) -> list:
    rows = []
    for name in sorted(os.listdir(data_dir)) if os.path.isdir(data_dir) else []:
        if not (name == filename or (name.startswith(filename + ".") and not name.endswith(".lock"))):
            continue
        try:
            with open(os.path.join(data_dir, name), "r", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(row, dict):
                        rows.append(row)
        except OSError:
            continue
    return rows


def split_stream(rows: Iterable[Mapping[str, Any]], epoch_id: Optional[str] = None) -> tuple:
    snaps, labels = {}, defaultdict(dict)
    for row in rows:
        if epoch_id and row.get("epoch_id") and str(row["epoch_id"]) != str(epoch_id) and row.get("row_kind") == "SNAPSHOT":
            continue
        call_id = str(row.get("shared_ai_call_id") or "")
        if not call_id:
            continue
        if row.get("row_kind") == "SNAPSHOT":
            snaps[call_id] = row
        elif row.get("row_kind") == "LABEL":
            labels[call_id][int(_finite(row.get("horizon_min")) or 0)] = row
    return snaps, labels


def stream_health(snaps: Mapping[str, Mapping], labels: Mapping[str, Mapping]) -> dict:
    matured = {}
    for minutes in dfs.LABEL_HORIZONS_MIN:
        rows = [labels.get(cid, {}).get(minutes) for cid in snaps]
        done = [r for r in rows if r]
        matured[str(minutes)] = {"labelled": len(done),
                                 "tape_ok": sum(1 for r in done if r.get("tape_ok"))}
    return {
        "snapshots": len(snaps),
        "feature_set_versions": dict(Counter(str(s.get("feature_set_version")) for s in snaps.values())),
        "prompt_input_revisions": dict(Counter(str((s.get("ai") or {}).get("prompt_input_revision"))
                                               for s in snaps.values())),
        "book_depth_collected": sorted({bool(s.get("book_depth_collected")) for s in snaps.values()}),
        "labels": matured,
    }


def _cluster_bound(values: list, upper: bool, seed: str) -> Optional[float]:
    clusters = defaultdict(list)
    for ts, v in values:
        clusters[int(ts // CLUSTER_SEC)].append(v)
    keys = sorted(clusters)
    if len(keys) < 2:
        return None
    rng = random.Random(seed)
    means = []
    for _ in range(RESAMPLES):
        sample = [v for k in (rng.choice(keys) for _ in keys) for v in clusters[k]]
        means.append(sum(sample) / len(sample))
    means.sort()
    q = 0.95 if upper else 0.05
    return round(means[int(q * (len(means) - 1))], 6)


def feature_vector(snap: Mapping[str, Any]) -> list:
    out = []
    for group, key in LOGISTIC_FEATURES:
        block = snap.get(group) if isinstance(snap.get(group), Mapping) else {}
        out.append(_finite(block.get(key)))
    return out


def fit_logistic(x: np.ndarray, y: np.ndarray, l2: float = LOGISTIC_L2, iters: int = 25) -> np.ndarray:
    n, k = x.shape
    xb = np.hstack([np.ones((n, 1)), x])
    w = np.zeros(k + 1)
    penalty = np.eye(k + 1) * l2
    penalty[0, 0] = 0.0
    for _ in range(iters):
        p = 1.0 / (1.0 + np.exp(-np.clip(xb @ w, -30, 30)))
        grad = xb.T @ (p - y) + penalty @ w
        hess = (xb * (p * (1 - p))[:, None]).T @ xb + penalty
        step = np.linalg.solve(hess + np.eye(k + 1) * 1e-9, grad)
        w -= step
        if np.max(np.abs(step)) < 1e-6:
            break
    return w


def _prepare(train_x: list, test_x: list) -> tuple:
    tr = np.array([[np.nan if v is None else v for v in row] for row in train_x], dtype=float)
    te = np.array([[np.nan if v is None else v for v in row] for row in test_x], dtype=float)
    mean = np.nanmean(np.where(np.isnan(tr).all(axis=0), 0.0, tr), axis=0) if len(tr) else np.zeros(tr.shape[1])
    mean = np.where(np.isnan(mean), 0.0, mean)
    tr = np.where(np.isnan(tr), mean, tr)
    te = np.where(np.isnan(te), mean, te)
    std = tr.std(axis=0)
    std = np.where(std > 1e-12, std, 1.0)
    return (tr - mean) / std, (te - mean) / std


def logistic_challenger(snaps: Mapping[str, Mapping], labels: Mapping[str, Mapping],
                        horizon_min: int = PRIMARY_HORIZON_MIN) -> dict:
    samples = []
    for call_id, snap in snaps.items():
        ts = _finite(snap.get("decision_ts"))
        label = labels.get(call_id, {}).get(horizon_min)
        if ts is None or not label or not label.get("tape_ok") or _finite(label.get("fwd_ret_bp")) is None:
            continue
        spread = _finite((snap.get("tape") or {}).get("spread_bp")) or 0.0
        samples.append((ts, feature_vector(snap), float(label["fwd_ret_bp"]), spread))
    samples.sort(key=lambda s: s[0])
    out = {"hypothesis_id": H9_ID, "horizon_min": horizon_min, "features": [f"{g}.{k}" for g, k in LOGISTIC_FEATURES],
           "l2": LOGISTIC_L2, "labelled_samples": len(samples), "oos_days": 0, "oos_predictions": 0}
    if not samples:
        out["verdict"] = {"status": "COLLECTING", "reason": "NO_LABELLED_SNAPSHOTS"}
        return out
    first_day = int(samples[0][0] // DAY_SEC)
    days = sorted({int(s[0] // DAY_SEC) for s in samples})
    brier_diff, trades, oos, coef = [], [], 0, None
    oos_days = 0
    for day in days:
        if day < first_day + MIN_TRAIN_DAYS:
            continue
        cutoff = day * DAY_SEC - horizon_min * 60
        train = [s for s in samples if s[0] < cutoff]
        test = [s for s in samples if int(s[0] // DAY_SEC) == day]
        ys = np.array([1.0 if s[2] > 0 else 0.0 for s in train])
        if len(train) < 200 or ys.min() == ys.max():
            continue
        xtr, xte = _prepare([s[1] for s in train], [s[1] for s in test])
        coef = fit_logistic(xtr, ys)
        probs = 1.0 / (1.0 + np.exp(-np.clip(np.hstack([np.ones((len(xte), 1)), xte]) @ coef, -30, 30)))
        clim = float(ys.mean())
        oos_days += 1
        for (ts, _, ret, spread), p in zip(test, probs):
            y = 1.0 if ret > 0 else 0.0
            brier_diff.append((ts, (p - y) ** 2 - (clim - y) ** 2))
            oos += 1
            if abs(p - 0.5) >= H9_TRADE_EDGE:
                trades.append((ts, (1.0 if p > 0.5 else -1.0) * ret - spread))
    out.update({
        "oos_days": oos_days,
        "oos_predictions": oos,
        "mean_brier_minus_climatology": None if not brier_diff else round(sum(d for _, d in brier_diff) / len(brier_diff), 6),
        "ub95_brier_minus_climatology": _cluster_bound(brier_diff, True, "h9-brier"),
        "sign_trades": len(trades),
        "sign_trade_mean_net_bp": None if not trades else round(sum(v for _, v in trades) / len(trades), 4),
        "sign_trade_lb95_net_bp": _cluster_bound(trades, False, "h9-trades"),
        "last_coefficients": None if coef is None else [round(float(c), 5) for c in coef],
    })
    out["verdict"] = h9_verdict(out)
    return out


def h9_verdict(stats: Mapping[str, Any]) -> dict:
    days, n = int(stats.get("oos_days") or 0), int(stats.get("oos_predictions") or 0)
    ub, lb = stats.get("ub95_brier_minus_climatology"), stats.get("sign_trade_lb95_net_bp")
    passed = (ub is not None and ub < 0 and int(stats.get("sign_trades") or 0) >= H9_MIN_TRADES
              and lb is not None and lb > 0)
    if days < H9_MIN_OOS_DAYS or n < H9_MIN_OOS_PREDICTIONS:
        status = "COLLECTING"
    elif passed:
        status = "PASS_FOR_REVIEW"
    elif days >= H9_MAX_OOS_DAYS:
        status = "KILL"
    else:
        status = "NOT_YET"
    return {"status": status, "oos_days": days, "oos_predictions": n,
            "needs": {"oos_days": H9_MIN_OOS_DAYS, "oos_predictions": H9_MIN_OOS_PREDICTIONS,
                      "sign_trades": H9_MIN_TRADES, "kill_after_oos_days": H9_MAX_OOS_DAYS},
            "never_trades": True}


def regime_section(regime_rows: Iterable[Mapping[str, Any]], snaps: Mapping[str, Mapping],
                   labels: Mapping[str, Mapping]) -> dict:
    states = Counter()
    scored = []
    for row in regime_rows:
        if row.get("row_kind") != "REGIME_PROMPT":
            continue
        states[str(row.get("call_state"))] += 1
        if row.get("call_state") != "CALLED":
            continue
        call_id = str(row.get("shared_ai_call_id") or "")
        snap = snaps.get(call_id) or {}
        label = labels.get(call_id, {}).get(60) or {}
        prior = (snap.get("tape") or {}).get("ret_60m_bp")
        fwd = label.get("fwd_ret_bp") if label.get("tape_ok") else None
        scored.append({
            "decision_ts": row.get("decision_ts"),
            "facts": row.get("facts") or {},
            "parsed": row.get("parsed") or {},
            "labels": regime.forward_labels(row.get("facts") or {}, prior, fwd),
        })
    report = regime.score_calls(scored)
    report["call_states"] = dict(states)
    return report


def build_report(snapshot_rows: Iterable[Mapping[str, Any]], regime_rows: Iterable[Mapping[str, Any]],
                 *, epoch_id: Optional[str] = None) -> dict:
    snaps, labels = split_stream(snapshot_rows, epoch_id)
    regime_rows = list(regime_rows)
    return {
        "schema": SCHEMA,
        "status": "OK" if snaps else "NO_DATA",
        "mode": "SHADOW_ONLY_NO_ORDERS",
        "epoch_id": epoch_id,
        "feature_set_version": dfs.FEATURE_SET_VERSION,
        "stream_health": stream_health(snaps, labels),
        "regime_h8": regime_section(regime_rows, snaps, labels),
        "logistic_h9": logistic_challenger(snaps, labels),
    }


def build_from_data_dir(data_dir: str, epoch_id: Optional[str] = None) -> dict:
    return build_report(load_rows(data_dir, dfs.SNAPSHOT_FILE),
                        load_rows(data_dir, regime.REGIME_PROMPT_FILE), epoch_id=epoch_id)
