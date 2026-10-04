"""Studies over the four collector streams the main analyzer did not use.

* ``post_exit_replay``             exit-timing regret: what holding longer would have done
* ``taker_signal_counterfactuals`` taker EV: cross the spread at signal (+latency), exit at touch
* ``fill_markouts``                post-fill markout curves per tile and liquidity
* ``research_events_v22``          outcome / observation / replay-eligibility mix per tile

Rotated files are immutable, so each one is summarised once and cached by a
content signature (size + head/tail hash, independent of the rotation index).
The live ``research_events_v22.jsonl`` (>1 GB, ~375 KB per row) is indexed
incrementally from the last parsed byte offset.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import math
import os
import re
import time
from typing import Iterable, Optional

import numpy as np
import pandas as pd

from analyzer_epoch_guard import guarded_read, process_guard, stream_name
from strategy_lab.stats import cluster_ci

SCHEMA = "stream_studies_v1"
REPORT_FILE = "stream_studies_report.json"
CACHE_SCHEMA = "stream_studies_cache_v2_epoch_guarded"
POST_EXIT_HORIZONS = (300, 900, 1800, 3600)
MARKOUT_HORIZONS = ("1s", "10s", "60s", "300s")
CLUSTER_SEC = 3600
MIN_CI_N = 3
STREAMS = ("post_exit_replay.jsonl", "taker_signal_counterfactuals.jsonl", "fill_markouts.jsonl",
           "research_events_v22.jsonl")
_ROT = re.compile(r"^(?P<base>.+?\.jsonl)(?:\.(?P<idx>\d+))?(?P<gz>\.gz)?$")


# ------------------------------------------------------------------ files & cache

def rotation_files(data_dir: str, stem: str) -> list:
    """Rotations oldest-first (higher index = older on this collector) then the live file."""
    try:
        names = os.listdir(data_dir)
    except OSError:
        return []
    rot, live = [], None
    for name in names:
        m = _ROT.match(name)
        if not m or m.group("base") != stem:
            continue
        path = os.path.join(data_dir, name)
        if m.group("idx") is None and not m.group("gz"):
            live = path
        else:
            rot.append((int(m.group("idx") or 0), path))
    files = [p for _, p in sorted(rot, key=lambda x: -x[0])]
    return files + ([live] if live else [])


def _open(path: str):
    return gzip.open(path, "rb") if path.endswith(".gz") else open(path, "rb")


def content_signature(path: str) -> Optional[str]:
    try:
        st = os.stat(path)
        with guarded_read(stream_name(path), "inventory"):
            fh = open(path, "rb")
        with fh:
            head = fh.read(65536)
            if st.st_size > 131072:
                fh.seek(st.st_size - 65536)
            tail = fh.read(65536)
    except OSError:
        return None
    return f"{st.st_size}:{hashlib.sha1(head + tail).hexdigest()}"


class Cache:
    def __init__(self, cache_dir: Optional[str]):
        self.path = os.path.join(cache_dir, "stream_studies_cache.json") if cache_dir else None
        self.data = {"schema": CACHE_SCHEMA, "files": {}, "live": {}}
        if self.path and os.path.isfile(self.path):
            try:
                with open(self.path, encoding="utf-8") as fh:
                    loaded = json.load(fh)
                if loaded.get("schema") == CACHE_SCHEMA:
                    self.data = loaded
            except (OSError, ValueError):
                pass
        self.used = set()
        self.hits = 0
        self.misses = 0

    def get(self, study: str, sig: Optional[str]):
        key = f"{study}|{sig}"
        if sig and key in self.data["files"]:
            self.used.add(key)
            self.hits += 1
            return self.data["files"][key]
        self.misses += 1
        return None

    def put(self, study: str, sig: Optional[str], value) -> None:
        if sig:
            key = f"{study}|{sig}"
            self.data["files"][key] = value
            self.used.add(key)

    def save(self) -> None:
        if not self.path:
            return
        # Drop entries for files that no longer exist so the cache stays bounded.
        self.data["files"] = {k: v for k, v in self.data["files"].items() if k in self.used}
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.data, fh)
        os.replace(tmp, self.path)


def _guarded_open(path: str):
    with guarded_read(stream_name(path)):
        return _open(path)


def _iter_json(path: str, start: int = 0):
    rel, guard = stream_name(path), process_guard()
    with _guarded_open(path) as fh:
        if start:
            fh.seek(start)
        for raw in fh:
            if not raw.strip() or not guard.admit_line(rel, raw):
                continue
            try:
                yield json.loads(raw)
            except ValueError:
                continue


def _num(v) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _ci(values, ts) -> dict:
    v = np.asarray(values, float)
    t = np.asarray(ts, float)
    ok = np.isfinite(v)
    v, t = v[ok], t[ok]
    if len(v) == 0:
        return {"n": 0, "mean": None, "lo": None, "hi": None, "p": None}
    if len(v) < MIN_CI_N:
        return {"n": int(len(v)), "mean": float(v.mean()), "lo": None, "hi": None, "p": None}
    c = cluster_ci(v, np.floor(np.nan_to_num(t) / CLUSTER_SEC).astype(np.int64))
    return {"n": int(len(v)), "mean": c["mean"], "lo": c["lo"], "hi": c["hi"], "p": c["p"]}


def _iso(ts) -> Optional[str]:
    f = _num(ts)
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(f)) if f else None


# ------------------------------------------------------------------ post-exit regret

def _post_exit_partial(path: str, epoch_start: float = 0.0) -> dict:
    """trade_id -> header + 15 s post-exit buckets (unreal % of margin), current epoch only."""
    out: dict = {}
    for r in _iter_json(path):
        if (_num(r.get("ts")) or 0) < epoch_start:
            continue
        tid = r.get("trade_id")
        if not tid:
            continue
        slot = out.setdefault(tid, {"hdr": None, "h": {}})
        if r.get("kind") == "post_exit_header":
            cfg = r.get("exit_config") or {}
            slot["hdr"] = {"exit_t_rel": _num(r.get("exit_t_rel")), "start_ts": _num(r.get("start_ts")),
                           "post_start_ts": _num(r.get("post_exit_started_ts")), "margin": _num(r.get("margin_usdt")),
                           "direction": r.get("direction"), "family": cfg.get("family")}
            continue
        if r.get("phase") != "post_exit":
            continue
        u, ts = _num(r.get("unreal_pct")), _num(r.get("ts"))
        if u is None or ts is None:
            continue
        slot.setdefault("ticks", []).append((ts, u))
    # Keep files small in the cache: collapse ticks to per-horizon extremes relative to
    # the earliest tick seen in this file (the exit anchor is resolved at merge time).
    for tid, slot in out.items():
        ticks = slot.pop("ticks", [])
        slot["ticks"] = _downsample(ticks)
    return out


def _downsample(ticks: list, step: float = 15.0) -> list:
    """One (ts, max, min, last) per 15 s bucket: exact enough for 5-60 min horizons."""
    if not ticks:
        return []
    ticks.sort()
    buckets: dict = {}
    for ts, u in ticks:
        b = int(ts // step)
        cur = buckets.get(b)
        if cur is None:
            buckets[b] = [ts, u, u, u]
        else:
            cur[1] = max(cur[1], u)
            cur[2] = min(cur[2], u)
            cur[3] = u
            cur[0] = ts
    return [buckets[k] for k in sorted(buckets)]


def _is_live(path: str, frozen) -> bool:
    """The mirror's active file is re-read every cycle; rotations and frozen archive files are cached."""
    return path.endswith(".jsonl") and path not in frozen


def post_exit_regret(files: list, cache: Cache, trades: pd.DataFrame, lane_of: dict, epoch_start: float,
                     frozen=frozenset()) -> tuple:
    merged: dict = {}
    study = f"post_exit@{int(epoch_start)}"
    for path in files:
        live = _is_live(path, frozen)
        sig = None if live else content_signature(path)
        part = cache.get(study, sig) if sig else None
        if part is None:
            part = _post_exit_partial(path, epoch_start)
            if sig:
                cache.put(study, sig, part)
        for tid, slot in part.items():
            m = merged.setdefault(tid, {"hdr": None, "ticks": []})
            if slot.get("hdr"):
                m["hdr"] = slot["hdr"]
            m["ticks"].extend(slot.get("ticks") or [])
    for m in merged.values():
        # the same rotation can exist in a frozen archive and the mirror
        m["ticks"] = [list(t) for t in sorted({tuple(t) for t in m["ticks"]})]
    exit_info = {}
    if trades is not None and not trades.empty and "trade_id" in trades.columns:
        t = trades.drop_duplicates(subset=["trade_id"], keep="last")
        for _, row in t.iterrows():
            exit_info[str(row["trade_id"])] = (_num(row.get("net_pnl_usd")), str(row.get("research_lane") or "").upper(),
                                               str(row.get("exit_reason") or ""))
    rows = []
    for tid, m in merged.items():
        hdr = m.get("hdr")
        if not hdr or tid not in exit_info or not m["ticks"]:
            continue
        pnl, lane, reason = exit_info[tid]
        margin = hdr.get("margin") or 0.25
        anchor = hdr.get("post_start_ts") or (
            (hdr.get("start_ts") or 0) + (hdr.get("exit_t_rel") or 0) if hdr.get("start_ts") else None)
        if pnl is None or anchor is None or anchor < epoch_start:
            continue
        exit_u = pnl / margin * 100.0
        ticks = sorted(m["ticks"])
        row = {"trade_id": tid, "research_lane": lane or lane_of.get(tid.split("-")[0], ""), "exit_reason": reason,
               "exit_family": hdr.get("family"), "exit_ts": anchor, "exit_unreal_pct": round(exit_u, 4),
               "margin_usd": margin, "post_exit_ticks_15s": len(ticks),
               "coverage_sec": round(ticks[-1][0] - anchor, 1)}
        for h in POST_EXIT_HORIZONS:
            win = [b for b in ticks if 0 <= b[0] - anchor <= h]
            if not win or row["coverage_sec"] < h * 0.9:
                row[f"drift_{h}s_pct"] = row[f"missed_mfe_{h}s_pct"] = row[f"avoided_mae_{h}s_pct"] = None
                continue
            row[f"drift_{h}s_pct"] = round(win[-1][3] - exit_u, 4)
            row[f"missed_mfe_{h}s_pct"] = round(max(b[1] for b in win) - exit_u, 4)
            row[f"avoided_mae_{h}s_pct"] = round(min(b[2] for b in win) - exit_u, 4)
        rows.append(row)
    per_trade = pd.DataFrame(rows)
    summary = []
    if not per_trade.empty:
        for lane, sub in per_trade.groupby("research_lane"):
            for h in POST_EXIT_HORIZONS:
                col = f"drift_{h}s_pct"
                d = pd.to_numeric(sub[col], errors="coerce")
                ok = d.notna()
                if not ok.any():
                    continue
                c = _ci(d[ok].to_numpy(float) * sub.loc[ok, "margin_usd"].to_numpy(float) / 100.0,
                        sub.loc[ok, "exit_ts"].to_numpy(float))
                mfe = pd.to_numeric(sub.loc[ok, f"missed_mfe_{h}s_pct"], errors="coerce")
                summary.append({
                    "research_lane": lane, "horizon_sec": h, "n": c["n"],
                    "hold_longer_mean_usd": _r(c["mean"]), "ci_lo_usd": _r(c["lo"]), "ci_hi_usd": _r(c["hi"]),
                    "p_cluster": _r(c["p"]),
                    "share_better_if_held": _r(float((d[ok] > 0).mean()), 4),
                    "mean_drift_pct_margin": _r(float(d[ok].mean()), 4),
                    "mean_missed_mfe_pct_margin": _r(float(mfe.mean()), 4) if mfe.notna().any() else None,
                    "verdict": _regret_verdict(c),
                })
    return per_trade, pd.DataFrame(summary)


def _regret_verdict(c: dict) -> str:
    if c["n"] < 10 or c["lo"] is None:
        return "INSUFFICIENT_N"
    if c["lo"] > 0:
        return "EXITS_TOO_EARLY"
    if c["hi"] < 0:
        return "EXITS_WELL_TIMED"
    return "NO_TIMING_EDGE"


def _r(v, d: int = 6):
    f = _num(v)
    return round(f, d) if f is not None else None


# ------------------------------------------------------------------ markouts

def _markout_rows(files: list, cache: Cache, study: str, epoch_start: float, keep, frozen=frozenset(),
                  extra_rows: Iterable = (), key=None) -> list:
    """Kept rows from every file (+ ``extra_rows`` such as Tier A), de-duplicated by ``key``."""
    rows = []
    for path in files:
        live = _is_live(path, frozen)
        sig = None if live else content_signature(path)
        part = cache.get(study, sig) if sig else None
        if part is None:
            part = [keep(r) for r in _iter_json(path)]
            part = [p for p in part if p]
            if sig:
                cache.put(study, sig, part)
        rows.extend(p for p in part if (p.get("ts") or 0) >= epoch_start)
    for r in extra_rows:
        p = keep(r)
        if p and (p.get("ts") or 0) >= epoch_start:
            rows.append(p)
    if key is None:
        return rows
    seen, out = set(), []
    for p in rows:
        k = key(p)
        if k in seen:
            continue
        seen.add(k)
        out.append(p)
    return out


def _tier_a_rows(dataset: str, root: Optional[str], since: float) -> list:
    """Raw Tier A rows as dicts; empty when Tier A has no such dataset yet."""
    if not root:
        return []
    try:
        from strategy_lab.client import load_tier_a

        frame = load_tier_a(dataset, since=since or None, root=root, dedupe="row")
    except Exception:
        return []
    out = []
    for text in frame["row"] if len(frame) else ():
        if isinstance(text, str) and text.startswith("{"):
            try:
                row = json.loads(text)
            except ValueError:
                continue
            if isinstance(row, dict):
                out.append(row)
    return out


def _flat_markouts(r: dict, field: str) -> dict:
    out = {}
    for h in MARKOUT_HORIZONS:
        m = (r.get("markouts") or {}).get(h) or {}
        out[h] = _num(m.get(field)) if m.get("on_time", True) else None
    return out


def taker_counterfactuals(files: list, cache: Cache, trades: pd.DataFrame, epoch_start: float,
                          frozen=frozenset(), extra_rows: Iterable = ()) -> tuple:
    def keep(r):
        if r.get("schema") != "taker_signal_counterfactual_v1":
            return None
        return {"ts": _num(r.get("signal_ts")), "call": r.get("shared_ai_call_id"), "dir": r.get("direction"),
                "lat": _num(r.get("latency_sec")), "ai": r.get("raw_ai_decision"),
                "spread_bps": _spread_bps(r), "exit_touch": _flat_markouts(r, "markout_exit_touch_bps"),
                "mid": _flat_markouts(r, "markout_mid_bps")}

    rows = _markout_rows(files, cache, "taker_cf", epoch_start, keep, frozen, extra_rows,
                         key=lambda p: (p["ts"], p["call"], p["dir"], p["lat"]))
    tile_by_call = {}
    if trades is not None and not trades.empty and "shared_ai_call_id" in trades.columns:
        t = trades.drop_duplicates(subset=["trade_id"], keep="last")
        dcol = "direction" if "direction" in t.columns else ("final_direction" if "final_direction" in t.columns else None)
        for _, row in t.iterrows():
            key = (str(row.get("shared_ai_call_id") or ""), str(row.get(dcol) or "").upper() if dcol else "")
            tile_by_call.setdefault(key, set()).add(str(row.get("research_lane") or "").upper())
    out = []
    groups: dict = {}
    for r in rows:
        tiles = tile_by_call.get((str(r["call"] or ""), str(r["dir"] or "").upper()), set())
        for g in [("ALL", "ALL"), ("AI_DECISION", str(r["ai"] or "UNKNOWN"))] + [("TILE", t) for t in sorted(tiles)]:
            groups.setdefault(g + (r["lat"],), []).append(r)
    for (kind, value, lat), sub in sorted(groups.items(), key=lambda kv: (kv[0][0], kv[0][1], kv[0][2] or 0)):
        for h in MARKOUT_HORIZONS:
            vals = [s["exit_touch"].get(h) for s in sub]
            ts = [s["ts"] for s in sub]
            c = _ci([v if v is not None else np.nan for v in vals], ts)
            mids = [s["mid"].get(h) for s in sub if s["mid"].get(h) is not None]
            out.append({"group": kind, "value": value, "latency_sec": lat, "horizon": h, "n": c["n"],
                        "ev_exit_touch_bps": _r(c["mean"], 4), "ci_lo_bps": _r(c["lo"], 4), "ci_hi_bps": _r(c["hi"], 4),
                        "p_cluster": _r(c["p"]), "mean_mid_markout_bps": _r(np.mean(mids), 4) if mids else None,
                        "mean_spread_bps": _r(np.nanmean([s["spread_bps"] for s in sub if s["spread_bps"] is not None]), 4)
                        if any(s["spread_bps"] is not None for s in sub) else None})
    meta = {"rows": len(rows), "signals": len({(r["call"], r["dir"]) for r in rows}),
            "ai_decisions": _counts(r["ai"] for r in rows),
            "last_signal_at": _iso(max((r["ts"] or 0) for r in rows)) if rows else None,
            "last_reject_signal_at": _iso(max(((r["ts"] or 0) for r in rows if str(r["ai"]).upper() == "REJECT"),
                                              default=0)) if rows else None}
    return pd.DataFrame(out), meta


def _spread_bps(r: dict) -> Optional[float]:
    bid, ask = _num(r.get("bid")), _num(r.get("ask"))
    if bid and ask and ask >= bid:
        return (ask - bid) / ((ask + bid) / 2) * 1e4
    return None


def _counts(values: Iterable) -> dict:
    out: dict = {}
    for v in values:
        out[str(v)] = out.get(str(v), 0) + 1
    return out


def fill_markouts(files: list, cache: Cache, lanes, epoch_start: float, frozen=frozenset(),
                  extra_rows: Iterable = ()) -> tuple:
    def keep(r):
        if r.get("schema") != "fill_markout_v1":
            return None
        return {"ts": _num(r.get("fill_ts")), "trade_id": r.get("trade_id"),
                "lane": str(r.get("research_lane") or "").upper(), "liq": r.get("liquidity"),
                "mid": _flat_markouts(r, "markout_mid_bps"), "touch": _flat_markouts(r, "markout_exit_touch_bps")}

    rows = _markout_rows(files, cache, "fill_mk", epoch_start, keep, frozen, extra_rows,
                         key=lambda p: (p["trade_id"], p["ts"], p["liq"]))
    current = {str(l).upper() for l in lanes}
    out = []
    groups: dict = {}
    for r in rows:
        if current and r["lane"] not in current:
            continue
        for liq in ("ALL", str(r["liq"] or "UNKNOWN")):
            groups.setdefault((r["lane"], liq), []).append(r)
    for (lane, liq), sub in sorted(groups.items()):
        for h in MARKOUT_HORIZONS:
            c = _ci([s["mid"].get(h) if s["mid"].get(h) is not None else np.nan for s in sub], [s["ts"] for s in sub])
            ct = _ci([s["touch"].get(h) if s["touch"].get(h) is not None else np.nan for s in sub], [s["ts"] for s in sub])
            out.append({"research_lane": lane, "liquidity": liq, "horizon": h, "n": c["n"],
                        "markout_mid_bps": _r(c["mean"], 4), "ci_lo_bps": _r(c["lo"], 4), "ci_hi_bps": _r(c["hi"], 4),
                        "p_cluster": _r(c["p"]), "markout_exit_touch_bps": _r(ct["mean"], 4)})
    meta = {"rows": len(rows), "trades": len({r["trade_id"] for r in rows}),
            "last_fill_at": _iso(max((r["ts"] or 0) for r in rows)) if rows else None}
    return pd.DataFrame(out), meta


# ------------------------------------------------------------------ research events

def _event_compact(r: dict, nbytes: int) -> dict:
    env = r.get("envelope") or {}
    rep = r.get("replay_eligibility") or {}
    tid = str(r.get("trade_id") or r.get("event_id") or "")
    return {"id": tid, "prefix": tid.split("-")[0] if "-" in tid else tid[:4], "epoch": r.get("epoch_id"),
            "ts": _num(env.get("signal_ts")), "outcome": r.get("primary_outcome"),
            "obs": r.get("observation_status"), "lifecycle": r.get("lifecycle"),
            "fill_model": r.get("fill_model"), "replay": bool(rep.get("eligible")) if rep else None,
            "would_block": r.get("would_block"), "negative": r.get("negative_evidence"),
            "policy": r.get("base_policy_id"), "bytes": nbytes}


def _index_events(path: str, start: int) -> tuple:
    rows, end = [], start
    rel, guard = stream_name(path), process_guard()
    with _guarded_open(path) as fh:
        if start:
            fh.seek(start)
        while True:
            raw = fh.readline()
            if not raw:
                break
            if not raw.endswith(b"\n"):
                break  # torn tail: re-read next cycle
            end += len(raw)
            if not guard.admit_line(rel, raw):
                continue
            try:
                rows.append(_event_compact(json.loads(raw), len(raw)))
            except ValueError:
                continue
    return rows, end


def research_events(files: list, cache: Cache, epoch_id: Optional[str], lane_of: dict, now: float,
                    frozen=frozenset()) -> tuple:
    rows = []
    for path in files:
        if _is_live(path, frozen):
            head = content_signature_head(path)
            live = cache.data["live"].get(os.path.basename(path)) or {}
            start = live.get("offset", 0) if live.get("head") == head and live.get("offset", 0) <= os.path.getsize(path) else 0
            prior = live.get("rows", []) if start else []
            new, end = _index_events(path, start)
            cache.data["live"][os.path.basename(path)] = {"head": head, "offset": end, "rows": prior + new}
            rows.extend(prior + new)
        else:
            sig = content_signature(path)
            part = cache.get("events", sig)
            if part is None:
                part, _ = _index_events(path, 0)
                cache.put("events", sig, part)
            rows.extend(part)
    if frozen:
        seen, unique = set(), []
        for r in rows:
            k = (r.get("id"), r.get("ts"), r.get("outcome"), r.get("obs"), r.get("lifecycle"), r.get("bytes"))
            if k not in seen:
                seen.add(k)
                unique.append(r)
        rows = unique
    epoch_rows = [r for r in rows if not epoch_id or r.get("epoch") == epoch_id]
    table = []
    agg: dict = {}
    for r in epoch_rows:
        lane = lane_of.get(r["prefix"], "SCAN_OPPORTUNITY" if r["prefix"] == "scan" else f"OTHER:{r['prefix']}")
        key = (lane, r.get("outcome"), r.get("obs"))
        a = agg.setdefault(key, {"n": 0, "replay": 0, "bytes": 0, "last": 0.0, "would_block": 0, "negative": 0})
        a["n"] += 1
        a["replay"] += 1 if r.get("replay") else 0
        a["would_block"] += 1 if r.get("would_block") else 0
        a["negative"] += 1 if r.get("negative") else 0
        a["bytes"] += r.get("bytes") or 0
        a["last"] = max(a["last"], r.get("ts") or 0)
    for (lane, outcome, obs), a in sorted(agg.items(), key=lambda kv: (str(kv[0][0]), str(kv[0][1]), str(kv[0][2]))):
        table.append({"lane": lane, "primary_outcome": outcome, "observation_status": obs, "n": a["n"],
                      "replay_eligible_share": _r(a["replay"] / a["n"], 4), "would_block": a["would_block"],
                      "negative_evidence": a["negative"], "mean_row_kb": _r(a["bytes"] / a["n"] / 1024, 1),
                      "last_signal_at": _iso(a["last"])})
    by_outcome_last = {}
    for r in epoch_rows:
        k = str(r.get("outcome"))
        by_outcome_last[k] = max(by_outcome_last.get(k, 0), r.get("ts") or 0)
    complete = sum(1 for r in epoch_rows if r.get("obs") in ("COMPLETE", "FUNNEL_COMPLETE"))
    meta = {"rows_indexed": len(rows), "epoch_rows": len(epoch_rows),
            "complete_share": _r(complete / len(epoch_rows), 4) if epoch_rows else None,
            "insufficient_path": sum(1 for r in epoch_rows if r.get("obs") == "INSUFFICIENT_PATH"),
            "last_signal_by_outcome": {k: _iso(v) for k, v in by_outcome_last.items()},
            "last_signal_at": _iso(max((r.get("ts") or 0) for r in epoch_rows)) if epoch_rows else None}
    if epoch_rows:
        meta["signal_lag_sec"] = round(now - max((r.get("ts") or 0) for r in epoch_rows), 1)
    return pd.DataFrame(table), meta


def content_signature_head(path: str) -> Optional[str]:
    try:
        with open(path, "rb") as fh:
            return hashlib.sha1(fh.read(65536)).hexdigest()
    except OSError:
        return None


# ------------------------------------------------------------------ driver

TIER_A_DATASETS = {"fill_markouts.jsonl": "fill_markouts"}


def run_stream_studies(data_dir: str, *, trades: Optional[pd.DataFrame], registry: Optional[dict], lanes,
                       epoch_id: Optional[str], epoch_start: float, cache_dir: Optional[str],
                       now: Optional[float] = None, history=None) -> tuple:
    """``history`` (``strategy_lab.tape.HistorySources``) adds frozen archive files and
    Tier A rows to the live mirror, epoch-bounded and de-duplicated; ``None`` = laptop defaults."""
    from strategy_lab.tape import HistorySources, default_history_sources

    now = float(now if now is not None else time.time())
    t0 = time.time()
    if not epoch_start:
        history = HistorySources()          # history is only epoch-pure with a known epoch start
    elif history is None:
        history = default_history_sources()
    cache = Cache(cache_dir)
    lane_of = {str((spec or {}).get("id_prefix")): lane for lane, spec in (registry or {}).items()
               if (spec or {}).get("id_prefix")}
    timing, health, tables, errors = {}, [], {}, {}

    def stage(name, fn):
        t = time.time()
        try:
            return fn()
        except Exception as exc:  # one stream must never sink the others
            errors[name] = f"{type(exc).__name__}: {exc}"
            return None
        finally:
            timing[name] = round(time.time() - t, 2)

    archive_files = {s: [p for folder in history.archive_dirs for p in rotation_files(folder, s)] for s in STREAMS}
    frozen = frozenset(p for fl in archive_files.values() for p in fl)
    files = {s: archive_files[s] + rotation_files(data_dir, s) for s in STREAMS}
    tier_a = {s: _tier_a_rows(TIER_A_DATASETS[s], history.tier_a_root, epoch_start) if s in TIER_A_DATASETS else []
              for s in STREAMS}
    pe = stage("post_exit_replay", lambda: post_exit_regret(files["post_exit_replay.jsonl"], cache, trades, lane_of,
                                                            epoch_start, frozen))
    tk = stage("taker_signal_counterfactuals", lambda: taker_counterfactuals(
        files["taker_signal_counterfactuals.jsonl"], cache, trades, epoch_start, frozen,
        tier_a["taker_signal_counterfactuals.jsonl"]))
    fm = stage("fill_markouts", lambda: fill_markouts(files["fill_markouts.jsonl"], cache, lanes, epoch_start, frozen,
                                                      tier_a["fill_markouts.jsonl"]))
    ev = stage("research_events_v22", lambda: research_events(files["research_events_v22.jsonl"], cache, epoch_id,
                                                              lane_of, now, frozen))
    tables["exit_regret_trades"], tables["exit_regret"] = pe if pe else (pd.DataFrame(), pd.DataFrame())
    tables["taker_counterfactual"], tk_meta = tk if tk else (pd.DataFrame(), {})
    tables["fill_markouts"], fm_meta = fm if fm else (pd.DataFrame(), {})
    tables["research_events"], ev_meta = ev if ev else (pd.DataFrame(), {})
    used = {"post_exit_replay.jsonl": len(tables["exit_regret_trades"]),
            "taker_signal_counterfactuals.jsonl": tk_meta.get("rows", 0),
            "fill_markouts.jsonl": fm_meta.get("rows", 0), "research_events_v22.jsonl": ev_meta.get("epoch_rows", 0)}
    last = {"post_exit_replay.jsonl": _iso(tables["exit_regret_trades"]["exit_ts"].max())
            if len(tables["exit_regret_trades"]) else None,
            "taker_signal_counterfactuals.jsonl": tk_meta.get("last_signal_at"),
            "fill_markouts.jsonl": fm_meta.get("last_fill_at"), "research_events_v22.jsonl": ev_meta.get("last_signal_at")}
    for s in STREAMS:
        fl = files[s]
        size = sum(os.path.getsize(p) for p in fl if os.path.exists(p))
        mtime = max((os.path.getmtime(p) for p in fl if os.path.exists(p)), default=None)
        name = s.replace(".jsonl", "")
        status = "ERROR" if name in errors else ("MISSING" if not fl else ("NO_EPOCH_ROWS" if not used[s] else "ANALYSED"))
        mirror_files = [p for p in fl if p not in frozen]
        mtime = max((os.path.getmtime(p) for p in mirror_files if os.path.exists(p)), default=mtime)
        health.append({"stream": s, "status": status, "files": len(fl), "bytes": size, "rows_used": used[s],
                       "file_age_sec": round(now - mtime, 1) if mtime else None, "content_last_at": last[s],
                       "parse_sec": timing.get(name), "error": errors.get(name),
                       "archive_files": len(archive_files[s]), "mirror_files": len(mirror_files),
                       "tier_a_rows": len(tier_a[s])})
    tables["stream_study_health"] = pd.DataFrame(health)
    cache.save()
    payload = {
        "schema": SCHEMA, "status": "ERROR" if len(errors) == len(STREAMS) else ("PARTIAL" if errors else "OK"),
        "generated_at": _iso(now), "epoch_id": epoch_id, "epoch_start": _iso(epoch_start),
        "post_exit_horizons_sec": list(POST_EXIT_HORIZONS), "markout_horizons": list(MARKOUT_HORIZONS),
        "exit_regret": {"trades": len(tables["exit_regret_trades"]),
                        "by_tile": tables["exit_regret"].to_dict("records")},
        "taker_counterfactual": {**tk_meta, "summary": tables["taker_counterfactual"][
            tables["taker_counterfactual"]["group"].isin(["ALL", "TILE"])].to_dict("records")
            if len(tables["taker_counterfactual"]) else []},
        "fill_markouts": {**fm_meta, "curves": tables["fill_markouts"].to_dict("records")},
        "research_events": {**ev_meta, "mix": tables["research_events"].to_dict("records")},
        "stream_health": health, "errors": errors,
        "history_sources": history.describe() if history.enabled else {"archive_dirs": [], "tier_a_root": None},
        "cache": {"hits": cache.hits, "misses": cache.misses, "path": cache.path},
        "timing": {**timing, "total_sec": round(time.time() - t0, 2)},
        "method": {
            "exit_regret": "post-exit 1s BBO marks bucketed to 15 s; drift = mark at exit+H minus realised exit "
                           "PnL (% of margin); hold_longer_mean_usd > 0 with CI above zero = exits too early. "
                           "Hour-cluster CI. Trades without >= 90% post-exit coverage at H are excluded at H.",
            "taker_counterfactual": "cross the spread at signal + latency at depth VWAP, exit at the opposite touch "
                                    "after H (markout_exit_touch_bps, BITFINEX_ZERO fees); on-time samples only",
            "fill_markouts": "mid markout after each paper fill, signed in trade direction, per tile x liquidity",
            "research_events": "current-epoch research_event_v2.2 rows by tile id prefix x outcome x observation",
        },
    }
    return payload, tables
