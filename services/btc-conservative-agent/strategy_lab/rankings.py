"""Family-wise corrected verdicts for the analyzer's main rankings.

Each ranking (tiles, top combinations, exit combinations, regime x lane
cells, feature buckets, feature correlations) is one family. Every row gets a
two-sided p-value for "mean per-trade PnL is zero" from an hour-cluster CR1
t-test, then Holm (family-wise error) and Benjamini-Hochberg (false discovery
rate) adjustments across the rows of that family that could be tested.

Feature buckets use expanding quantiles: a trade's quintile threshold comes
only from strictly earlier trades, so a bucket never peeks at the future.
"""
from __future__ import annotations

import math
from typing import Iterable, Optional, Sequence

import numpy as np
import pandas as pd

from strategy_lab.stats import benjamini_hochberg, cluster_ci, expanding_quantile, holm, t_two_sided_p

SCHEMA = "main_rankings_v1"
REPORT_FILE = "main_rankings_report.json"
MIN_TEST_N = 10
FWER_ALPHA = 0.05
FDR_Q = 0.10
CLUSTER_SEC = 3600
QUINTILES = (0.2, 0.4, 0.6, 0.8)
EXPANDING_MIN_HISTORY = 30
FORCED_EXIT_TOKENS = ("ADMIN", "DEPLOY", "FORCE", "MANUAL", "MAINTENANCE")

VERDICT_TEXT = {
    "POSITIVE_FWER": "positive after Holm (family-wise 5%)",
    "NEGATIVE_FWER": "negative after Holm (family-wise 5%)",
    "POSITIVE_FDR": "positive BH discovery (q<=10%), not Holm-significant",
    "NEGATIVE_FDR": "negative BH discovery (q<=10%), not Holm-significant",
    "NOT_SIGNIFICANT": "not distinguishable from zero after correction",
    "INSUFFICIENT_N": f"fewer than {MIN_TEST_N} trades: not tested",
}


def _epoch_seconds(values) -> np.ndarray:
    s = pd.Series(values)
    if s.empty:
        return np.array([], float)
    num = pd.to_numeric(s, errors="coerce")
    if num.notna().mean() > 0.5:
        out = num.to_numpy(float)
        return np.where(out > 1e11, out / 1000.0, out)
    dt = pd.to_datetime(s, utc=True, errors="coerce")
    return dt.map(lambda x: x.timestamp() if pd.notna(x) else np.nan).to_numpy(float)


def trade_time(frame: pd.DataFrame) -> np.ndarray:
    for col in ("close_ts", "exit_ts", "ts", "entry_ts", "fill_ts", "signal_ts"):
        if col in frame.columns:
            secs = _epoch_seconds(frame[col])
            if np.isfinite(secs).any():
                return secs
    return np.full(len(frame), np.nan)


def trade_pnl(frame: pd.DataFrame) -> np.ndarray:
    col = "net_pnl_usd" if "net_pnl_usd" in frame.columns else "outcome_net_pnl_usd"
    if col not in frame.columns:
        return np.full(len(frame), np.nan)
    return pd.to_numeric(frame[col], errors="coerce").to_numpy(float)


def sample_test(pnl, ts) -> dict:
    v = np.asarray(pnl, float)
    t = np.asarray(ts, float)
    ok = np.isfinite(v)
    v, t = v[ok], t[ok]
    if len(v) < MIN_TEST_N:
        return {"n_tested": int(len(v)), "p_value": None, "t_stat": None, "ci_lo": None, "ci_hi": None,
                "clusters": None}
    clusters = np.where(np.isfinite(t), np.floor(np.nan_to_num(t) / CLUSTER_SEC), np.arange(len(t)) + 1e9)
    ci = cluster_ci(v, clusters.astype(np.int64))
    return {"n_tested": int(len(v)), "p_value": ci.get("p"), "t_stat": ci.get("t"), "ci_lo": ci.get("lo"),
            "ci_hi": ci.get("hi"), "clusters": ci.get("clusters")}


def corrected_verdict(mean: Optional[float], n_tested: int, p_holm: Optional[float], q_bh: Optional[float]) -> str:
    if n_tested < MIN_TEST_N or p_holm is None:
        return "INSUFFICIENT_N"
    sign = "POSITIVE" if (mean or 0) > 0 else "NEGATIVE"
    if p_holm <= FWER_ALPHA:
        return f"{sign}_FWER"
    if q_bh is not None and q_bh <= FDR_Q:
        return f"{sign}_FDR"
    return "NOT_SIGNIFICANT"


def annotate(rows: list, samples: Sequence, *, family: str) -> dict:
    """Add p_value / p_holm / q_bh / corrected_verdict to ``rows`` in place.

    ``samples[i]`` is ``(pnl, ts)`` for ``rows[i]``. Returns the family summary.
    """
    tests = [sample_test(p, t) for p, t in samples]
    pvals = [x["p_value"] for x in tests]
    p_holm = holm(pvals)
    q_bh = benjamini_hochberg(pvals)
    for row, test, ph, qb, (pnl, _ts) in zip(rows, tests, p_holm, q_bh, samples):
        v = np.asarray(pnl, float)
        v = v[np.isfinite(v)]
        mean = float(v.mean()) if len(v) else None
        verdict = corrected_verdict(mean, test["n_tested"], ph, qb)
        row.update({
            "p_value": _r(test["p_value"]), "p_holm": _r(ph), "q_bh": _r(qb),
            "ci_lo_usd": _r(test["ci_lo"], 6), "ci_hi_usd": _r(test["ci_hi"], 6),
            "ci_clusters": test["clusters"], "n_tested": test["n_tested"],
            "corrected_verdict": verdict, "family": family,
        })
    return family_summary(rows, family)


def family_summary(rows: Iterable[dict], family: str) -> dict:
    rows = list(rows)
    tested = [r for r in rows if r.get("p_value") is not None]
    counts = {}
    for r in rows:
        counts[r.get("corrected_verdict") or "INSUFFICIENT_N"] = counts.get(r.get("corrected_verdict") or "INSUFFICIENT_N", 0) + 1
    def below(row, key, limit):
        value = row.get(key)
        return value is not None and value <= limit

    return {
        "family": family, "rows": len(rows), "tested": len(tested), "verdict_counts": counts,
        "raw_p_below_alpha": sum(1 for r in tested if below(r, "p_value", FWER_ALPHA)),
        "holm_significant": sum(1 for r in tested if below(r, "p_holm", FWER_ALPHA)),
        "bh_discoveries": sum(1 for r in tested if below(r, "q_bh", FDR_Q)),
        "expected_false_raw_hits": round(len(tested) * FWER_ALPHA, 2),
        "alpha": FWER_ALPHA, "fdr_q": FDR_Q, "min_test_n": MIN_TEST_N,
    }


def _r(value, digits: int = 6):
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return round(f, digits) if math.isfinite(f) else None


def is_forced_exit(reason) -> bool:
    text = str(reason or "").upper()
    return any(tok in text for tok in FORCED_EXIT_TOKENS)


def tile_family(trades: Optional[pd.DataFrame], lanes, labels: Optional[dict] = None) -> tuple[list, dict]:
    """Current tiles, strategy exits only (forced/admin closes excluded like the EV ranking)."""
    rows, samples = [], []
    t = trades if trades is not None else pd.DataFrame()
    if not t.empty and "trade_id" in t.columns:
        t = t.drop_duplicates(subset=["trade_id"], keep="last")
    lane_col = (t["research_lane"].fillna("").astype(str).str.upper() if "research_lane" in t.columns
                else pd.Series("", index=t.index, dtype=str))
    forced = (t["exit_reason"].map(is_forced_exit) if "exit_reason" in t.columns
              else pd.Series(False, index=t.index))
    for lane in lanes:
        sub = t[(lane_col == str(lane).upper()) & ~forced] if len(t) else t
        pnl, ts = trade_pnl(sub), trade_time(sub)
        ok = np.isfinite(pnl)
        rows.append({"key": lane, "label": (labels or {}).get(lane) or lane, "n": int(ok.sum()),
                     "mean_usd": _r(pnl[ok].mean()) if ok.any() else None,
                     "win_rate": _r((pnl[ok] > 0).mean(), 4) if ok.any() else None,
                     "total_usd": _r(pnl[ok].sum()) if ok.any() else None})
        samples.append((pnl, ts))
    return rows, annotate(rows, samples, family="tiles")


def expanding_bucket_labels(values, ts, *, qs=QUINTILES, min_history: int = EXPANDING_MIN_HISTORY) -> np.ndarray:
    """Q1..Q5 from thresholds built only from strictly earlier rows; WARMUP before enough history."""
    v = np.asarray(values, float)
    thresholds = np.vstack([expanding_quantile(v, ts, q, min_history=min_history) for q in qs])
    labels = np.empty(len(v), dtype=object)
    for i in range(len(v)):
        if not np.isfinite(v[i]):
            labels[i] = None
        elif not np.isfinite(thresholds[:, i]).all():
            labels[i] = "WARMUP"
        else:
            labels[i] = f"Q{int((v[i] > thresholds[:, i]).sum()) + 1}"
    return labels


def feature_impact(trades: Optional[pd.DataFrame], features: Sequence[str]) -> tuple[list, dict]:
    """Per feature x expanding quintile: PnL stats with family-wise correction across all buckets."""
    rows, samples = [], []
    if trades is None or trades.empty:
        return rows, family_summary(rows, "feature_impact")
    t = trades.drop_duplicates(subset=["trade_id"], keep="last") if "trade_id" in trades.columns else trades
    pnl_all, ts_all = trade_pnl(t), trade_time(t)
    for item in features:
        feat, col = (item, item) if isinstance(item, str) else item
        if col not in t.columns:
            continue
        values = pd.to_numeric(t[col], errors="coerce").to_numpy(float)
        if np.isfinite(values).sum() < EXPANDING_MIN_HISTORY + MIN_TEST_N:
            continue
        order_ts = np.where(np.isfinite(ts_all), ts_all, np.inf)
        labels = expanding_bucket_labels(values, order_ts)
        for bucket in ("Q1", "Q2", "Q3", "Q4", "Q5", "WARMUP"):
            m = labels == bucket
            if not m.any():
                continue
            pnl = pnl_all[m]
            ok = np.isfinite(pnl)
            row = {"key": f"{feat}:{bucket}", "feature": feat, "column": col, "bucket": bucket, "n": int(ok.sum()),
                   "mean_usd": _r(pnl[ok].mean()) if ok.any() else None,
                   "win_rate": _r((pnl[ok] > 0).mean(), 4) if ok.any() else None,
                   "feature_min": _r(np.nanmin(values[m])), "feature_max": _r(np.nanmax(values[m]))}
            rows.append(row)
            # Warm-up rows are reported but not tested: their bucket is undefined.
            samples.append((pnl if bucket != "WARMUP" else np.array([]), ts_all[m]))
    return rows, annotate(rows, samples, family="feature_impact")


def correlation_pvalue(r: Optional[float], n: Optional[int]) -> Optional[float]:
    if r is None or n is None or n < MIN_TEST_N or not math.isfinite(r) or abs(r) >= 1:
        return None
    t = r * math.sqrt((n - 2) / max(1e-12, 1 - r * r))
    return t_two_sided_p(t, n - 2)


def annotate_correlations(rows: list) -> dict:
    """Pearson correlations (feature_importance_report): p from the t-transform, Holm/BH across features.

    Labels that alias the same column are one test, so they do not inflate the family size.
    """
    first: dict = {}
    for i, row in enumerate(rows):
        first.setdefault(row.get("column") or row.get("feature") or i, i)
    owners = sorted(first.values())
    unique_p = [correlation_pvalue(rows[i].get("correlation_with_pnl"), rows[i].get("n")) for i in owners]
    adj = dict(zip(owners, zip(unique_p, holm(unique_p), benjamini_hochberg(unique_p))))
    triples = [adj[first[row.get("column") or row.get("feature") or i]] for i, row in enumerate(rows)]
    for row, (p, ph, qb) in zip(rows, triples):
        n = int(row.get("n") or 0)
        row.update({"p_value": _r(p), "p_holm": _r(ph), "q_bh": _r(qb), "n_tested": n,
                    "corrected_verdict": corrected_verdict(row.get("correlation_with_pnl"), n if p is not None else 0, ph, qb),
                    "family": "feature_importance"})
    return family_summary(rows, "feature_importance")


FEATURE_IMPACT_CANDIDATES = (
    ("delta", ("features_delta", "feature_delta", "delta")),
    ("delta_change", ("features_delta_change", "delta_change")),
    ("imbalance", ("features_imbalance", "feature_imbalance", "imbalance")),
    ("velocity", ("features_velocity", "feature_velocity", "velocity")),
    ("volume", ("features_volume", "volume")),
    ("volume_ratio", ("features_volume_ratio", "feature_volume_ratio", "volume_ratio")),
    ("ema_slope", ("context_ema_slope", "ema_slope")),
    ("edge_score", ("edge_score_at_entry", "edge_score", "decision_edge_score")),
)


def resolve_features(frame: Optional[pd.DataFrame], candidates=FEATURE_IMPACT_CANDIDATES) -> list:
    out = []
    if frame is None or frame.empty:
        return out
    for label, cols in candidates:
        for col in cols:
            if col in frame.columns and pd.to_numeric(frame[col], errors="coerce").notna().sum() >= MIN_TEST_N:
                out.append((label, col))
                break
    return out


def samples_of(frame: pd.DataFrame) -> tuple:
    return trade_pnl(frame), trade_time(frame)


def _family_block(rows: list, summary: dict, *, source: str, limit: Optional[int] = None) -> dict:
    ordered = sorted(rows, key=lambda r: (r.get("p_holm") is None, r.get("p_holm") if r.get("p_holm") is not None
                                          else 1.0))
    return {"source": source, "summary": summary, "rows": ordered[:limit] if limit else ordered}


def _retired_lanes() -> frozenset:
    try:
        from combo_pathway_config import RETIRED_TILE_LANES
        return frozenset(str(l).upper() for l in RETIRED_TILE_LANES)
    except Exception:
        return frozenset()


def pooled_tile_family(pool: dict, labels: Optional[dict] = None) -> tuple[list, dict]:
    """Registry tiles on raw closes + archived rollup cells (``analysis_archive.registry_tile_pool``).

    Sufficient statistics carry no per-trade timestamps, so the test is an iid
    t on the pooled mean (no hour clustering); Holm/BH across the tiles.
    """
    rows, pvals = [], []
    for lane, t in (pool.get("tiles") or {}).items():
        n = int(t.get("n") or 0)
        tstat = t.get("t_stat")
        p = t_two_sided_p(float(tstat), n - 1) if tstat is not None and n >= MIN_TEST_N else None
        pvals.append(p)
        rows.append({"key": lane, "label": (labels or {}).get(lane) or lane, "n": n, "n_tested": n if p is not None else 0,
                     "n_raw": t.get("n_raw"), "n_archive": t.get("n_archive"),
                     "days_raw": len(t.get("days_raw") or []), "days_archive": len(t.get("days_archive") or []),
                     "mean_usd": _r(t.get("mean_usd")), "win_rate": _r(t.get("win_rate"), 4),
                     "total_usd": _r(t.get("net_pnl_usd")), "ci_lo_usd": _r(t.get("ci95_lo_usd")),
                     "ci_hi_usd": _r(t.get("ci95_hi_usd")), "p_value": _r(p)})
    for row, ph, qb in zip(rows, holm(pvals), benjamini_hochberg(pvals)):
        row.update({"p_holm": _r(ph), "q_bh": _r(qb), "family": "tiles_pooled",
                    "corrected_verdict": corrected_verdict(row["mean_usd"], row["n_tested"], ph, qb)})
    return rows, family_summary(rows, "tiles_pooled")


def registry_pool(trades, lanes, *, epoch_id: Optional[str] = None, archive_root: Optional[str] = None,
                  use_archive: Optional[bool] = None) -> Optional[dict]:
    """Pooled registry-tile stats, or ``None`` when the archive is not in use (tests / Fly)."""
    if use_archive is None:
        from strategy_lab.tape import laptop_defaults_enabled
        use_archive = archive_root is not None or laptop_defaults_enabled()
    if not use_archive:
        return None
    import analysis_archive

    return analysis_archive.registry_tile_pool(trades, lanes, epoch_id=epoch_id, root=archive_root,
                                               retired=_retired_lanes())


def build_main_rankings(trades: Optional[pd.DataFrame], lanes, *, labels: Optional[dict] = None,
                        reports: Optional[dict] = None, generated_at: Optional[str] = None,
                        epoch_id: Optional[str] = None, archive_root: Optional[str] = None,
                        use_archive: Optional[bool] = None) -> tuple:
    """One payload with every corrected ranking family; ``reports`` holds already-annotated report payloads.

    ``tiles_pooled`` adds archived daily rollups of the *same registry tiles and
    current epoch* to the raw closes (retired / non-registry lanes never enter).
    """
    reports = reports or {}
    families = {}
    tile_rows, tile_sum = tile_family(trades, lanes, labels)
    families["tiles"] = _family_block(tile_rows, tile_sum, source="trades (strategy exits, current tiles)")
    pool, pool_error = None, None
    try:
        pool = registry_pool(trades, lanes, epoch_id=epoch_id, archive_root=archive_root, use_archive=use_archive)
    except Exception as exc:  # the archive is optional evidence; never sink the rankings
        pool_error = f"{type(exc).__name__}: {exc}"
    if pool is not None:
        p_rows, p_sum = pooled_tile_family(pool, labels)
        families["tiles_pooled"] = _family_block(
            p_rows, p_sum, source="raw closes + analysis-archive daily rollups (registry tiles, current epoch; "
                                  "all exits incl. forced, iid t)")
    fi_rows, fi_sum = feature_impact(trades, resolve_features(trades))
    families["feature_impact"] = _family_block(fi_rows, fi_sum, source="trades (expanding quintiles)")
    for fam, report, keys in (("top_combinations", "top_combinations", ("top", "bottom")),
                              ("regime_lane_cells", "regime_leaderboard", ("cells",)),
                              ("feature_importance", "feature_importance", ("features",))):
        payload = reports.get(report) or {}
        rows, seen = [], set()
        for key in keys:
            for row in payload.get(key) or []:
                ident = row.get("combo") or row.get("feature") or (row.get("regime"), row.get("lane"))
                if ident not in seen:
                    seen.add(ident)
                    rows.append(row)
        summary = payload.get("multiple_testing") or family_summary(rows, fam)
        families[fam] = _family_block(rows, summary, source=f"{report}_report", limit=200)
    tile_verdicts = {r["key"]: {"label": r.get("label"), "n_tested": r.get("n_tested"),
                                "mean_usd": r.get("mean_usd"), "p_value": r.get("p_value"),
                                "p_holm": r.get("p_holm"), "q_bh": r.get("q_bh"),
                                "corrected_verdict": r.get("corrected_verdict"),
                                "verdict_text": VERDICT_TEXT.get(r.get("corrected_verdict"))}
                     for r in tile_rows}
    payload = {
        "schema": SCHEMA, "status": "OK", "generated_at": generated_at,
        "method": {
            "test": "two-sided t on mean per-trade net PnL, hour-cluster CR1 standard errors",
            "fwer": f"Holm across the tested rows of each family (alpha {FWER_ALPHA})",
            "fdr": f"Benjamini-Hochberg q across the same rows (q <= {FDR_Q} = discovery)",
            "min_test_n": MIN_TEST_N,
            "correlations": "feature_importance p from the Pearson t-transform (trades treated as independent); "
                            "aliased labels on one column are a single test",
            "buckets": f"expanding quintiles from strictly earlier trades, warm-up {EXPANDING_MIN_HISTORY} "
                       "trades (WARMUP rows are reported, never tested)",
            "verdicts": VERDICT_TEXT,
        },
        "family_summaries": {k: v["summary"] for k, v in families.items()},
        "tile_verdicts": tile_verdicts,
        "families": families,
        "tile_pool": ({k: pool.get(k) for k in ("schema", "epoch_id", "lanes", "excluded_archive_lanes",
                                                  "archive_root", "method")}
                      | {"status": "OK", "rows": families["tiles_pooled"]["rows"]}) if pool is not None else
        {"status": "ERROR" if pool_error else "NOT_USED", "error": pool_error},
    }
    payload["method"]["tiles_pooled"] = ("raw closes plus analysis-archive daily sufficient statistics for the same "
                                         "registry tiles and current epoch only; retired / NON_REGISTRY_LANE rows "
                                         "stay quarantined; a (day, tile) cell is never double counted; iid t "
                                         "(archived cells have no per-trade timestamps)")
    return payload, {"main_rankings": flat_rows(families)}


def flat_rows(families: dict) -> pd.DataFrame:
    out = []
    for fam, block in families.items():
        for row in block.get("rows") or []:
            out.append({
                "family": fam, "key": row.get("key") or row.get("combo") or row.get("feature")
                or (f"{row.get('regime')}|{row.get('lane')}" if row.get("regime") else None),
                "lane": row.get("lane") or (row.get("key") if fam in ("tiles", "tiles_pooled") else None),
                "n": row.get("n") if row.get("n") is not None else row.get("trades"),
                "mean_usd": row.get("mean_usd") if row.get("mean_usd") is not None else row.get("ev_usd"),
                "win_rate": row.get("win_rate") if row.get("win_rate") is not None else
                (row.get("wr_pct") / 100.0 if isinstance(row.get("wr_pct"), (int, float)) else None),
                "statistic": row.get("correlation_with_pnl"),
                "p_value": row.get("p_value"), "p_holm": row.get("p_holm"), "q_bh": row.get("q_bh"),
                "ci_lo_usd": row.get("ci_lo_usd"), "ci_hi_usd": row.get("ci_hi_usd"),
                "corrected_verdict": row.get("corrected_verdict"),
            })
    return pd.DataFrame(out, columns=["family", "key", "lane", "n", "mean_usd", "win_rate", "statistic", "p_value",
                                      "p_holm", "q_bh", "ci_lo_usd", "ci_hi_usd", "corrected_verdict"])
