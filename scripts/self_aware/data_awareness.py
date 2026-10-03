"""Data awareness: what is collected, whether every slot is filled, how fast it grows, and what it can answer.

Four laptop-only views over the Fly mirror (zero Fly calls):

* catalog       - every collected stream (curated specs + auto-discovered files): what it is, schema and
                  schema_version, venue, cadence, expected rows per interval, Fly and laptop location,
                  size, estimated rows, retention tier, first/last timestamp and freshness vs the mirror head;
* completeness  - per stream and hour: expected vs actual rows, gaps (attributed to Fly interruptions when
                  they overlap), plus field liveness (null %, distinct, constant, all-zero -> DEAD);
* capacity      - laptop bot data vs the 50 GB cap and Fly volume vs its size, growth rates, days to full;
* sufficiency   - for each research question, are the fields present and alive and are there enough
                  independent samples; what is missing; when it will be ready. READY questions release
                  their pre-registered gated screens in the edge tracker.

Sampling is bounded (tail bytes per file) so a full pass stays a few CPU-seconds at BELOW_NORMAL.
"""
from __future__ import annotations

import csv
import importlib.util
import io
import json
import math
import os
import re
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

from .facts import iso, parse_ts, read_json, watcher_check

FLY_DATA_DIR = "/app/data"
TAIL_BYTES = 1_000_000
MINUTE_TAIL_BYTES = 8_000_000
WHOLE_FILE_BYTES = 2_500_000
MAX_ROWS = 3000
FLATTEN_DEPTH = 3
LAPTOP_CAP_FRACTION = 0.9
GAP_MIN_SEC = {"second": 5, "minute": 180}
STALE_SEC = {"second": 180, "minute": 600, "event": 6 * 3600}
SKIP_DIRS = ("v3/receipts", "v3/market_segments", "v3/lifecycle_bundle_index", "corrupt_evidence_quarantine",
             "research-timing-declarations")
SKIP_SUFFIXES = (".validation.json", ".malformed_rows.jsonl", "-shm", "-wal", ".tmp", ".partial")
_MEASURE = re.compile(r"(^|[._])(ret|delta|bp|bps|pct|prob|score|rank|price|qty|vol|rv|z|adx|atr|rsi|basis|oi|"
                      r"funding|spread|markout|notional|imbalance|ofi|ema|dist)([._]|$|_)", re.I)
_ROT = re.compile(r"^(?P<base>.+?)(\.(?P<n>\d+))?(\.gz)?$")


@dataclass(frozen=True)
class StreamSpec:
    name: str
    path: str
    what: str
    venue: str
    cadence: str  # second | minute | event
    ts_fields: tuple[str, ...]
    expected_per_hour: float | None = None
    watch: tuple[str, ...] = ()
    allow_constant: tuple[str, ...] = ()
    critical: bool = False
    note: str = ""


CATALOG: tuple[StreamSpec, ...] = (
    StreamSpec("bitfinex_tape_1s", "market_microstructure_1s.jsonl", "Bitfinex BTC L1 book + taker flow per second",
               "bitfinex", "second", ("bucket_ts",), 3600, ("bid", "ask", "bid_qty", "ask_qty", "buy_qty", "sell_qty"),
               critical=True),
    StreamSpec("cross_venue_tape_1m", "cross_venue_tape_1m.jsonl", "Binance/Bybit/OKX vs Bitfinex basis and latency per minute",
               "binance,bybit,okx,bitfinex", "minute", ("minute_ts",), 60,
               ("basis_bp_mean.binance", "basis_bp_mean.bybit", "basis_bp_mean.okx", "bfx.m0"), critical=True),
    StreamSpec("market_context_1m", "market_context_1m.jsonl",
               "Spot premiums, derivatives (funding, OI, basis), USDT/USD and regime per minute",
               "coinbase,binance,bybit,okx", "minute", ("minute_ts", "ts"), 60,
               ("premium_bp_mean.coinbase_vs_binance_spot", "derivatives.binance.oi_btc", "regime.label"),
               allow_constant=("derivatives.binance.funding_rate", "derivatives.bybit.funding_rate",
                               "derivatives.okx.funding_rate"), critical=True,
               note="funding rates may legitimately sit at the 0.01% floor for hours"),
    StreamSpec("liquidations", "liquidations.jsonl", "Liquidation prints (lower bound, max 1/s per venue)",
               "binance,bybit,okx", "event", ("ts",), watch=("qty_btc", "price")),
    StreamSpec("ai_calls", "ai_input_log.jsonl", "Every production AI call input (prompt context)", "deepseek", "event",
               ("ts_epoch", "ts"), watch=("context.price", "context.ret_1m", "context.ret_5m", "context.delta_change",
                                          "context.ema_slope"), critical=True),
    StreamSpec("ai_decisions", "ai_tranche_log.csv", "Per AI call outcome rows (tranche log)", "deepseek", "event",
               ("timestamp", "ts")),
    StreamSpec("ai_shadow_challengers", "ai_shadow_challengers.jsonl", "Shadow challenger sides per AI call", "deepseek",
               "event", ("decision_ts", "ts")),
    StreamSpec("ai_shadow_compact_prompt", "ai_shadow_compact_prompt.jsonl", "Shadow compact-prompt calls", "deepseek",
               "event", ("observed_at_utc", "ts")),
    StreamSpec("adaptive_entry_decisions", "adaptive_entry_decisions.jsonl", "Per-signal adaptive entry decision per tile",
               "bitfinex", "event", ("signal_ts", "ts"), watch=("ai_feature.win_prob", "ai_feature.long_score", "spread_bps")),
    StreamSpec("pre_entry_features", "v3/ledgers/pre_entry_features.jsonl", "Pre-decision feature snapshot (ADX/ATR/RSI...)",
               "bitfinex", "event", ("features.cycle_3m_universe.captured_ts", "captured_at_ts", "recorded_at"),
               watch=("features.adx", "features.adx_normalized", "features.atr14_pct_3m", "captured_at_ts")),
    StreamSpec("decisions", "decisions_3factor.csv", "Signal decisions (legacy 3-factor ledger)", "bitfinex", "event",
               ("timestamp", "ts")),
    StreamSpec("closed_trades", "trades_3factor.csv", "Closed paper trades (legacy ledger)", "bitfinex", "event",
               ("close_ts", "ts", "timestamp")),
    StreamSpec("expired_orders", "expired_orders_3factor.csv", "Expired paper orders (legacy ledger)", "bitfinex", "event",
               ("expired_ts", "timestamp", "ts")),
    StreamSpec("v3_execution", "v3/ledgers/execution.jsonl", "V3 execution ledger (fills and closes)", "bitfinex", "event",
               ("fill_ts", "close_ts", "recorded_at", "created_at")),
    StreamSpec("v3_lifecycle", "v3/ledgers/lifecycle.jsonl", "V3 order/position lifecycle", "bitfinex", "event",
               ("terminal_ts", "signal_ts", "recorded_at", "created_at")),
    StreamSpec("v3_order_intent", "v3/ledgers/order_intent.jsonl", "V3 paper order intents", "bitfinex", "event",
               ("recorded_at", "created_at", "ts")),
    StreamSpec("v3_decision", "v3/ledgers/decision.jsonl", "V3 decisions", "bitfinex", "event", ("recorded_at", "created_at", "ts")),
    StreamSpec("v3_opportunity", "v3/ledgers/opportunity.jsonl", "V3 opportunities", "bitfinex", "event",
               ("recorded_at", "created_at", "ts")),
    StreamSpec("counterfactual", "counterfactual.jsonl", "Counterfactual outcomes of unexecuted scenarios", "bitfinex", "event",
               ("signal_ts", "ts", "recorded_at"), watch=("epoch_id", "opportunity_id")),
    StreamSpec("taker_signal_counterfactuals", "taker_signal_counterfactuals.jsonl",
               "Taker-at-signal counterfactual fills with 1s/10s/60s/300s markouts", "bitfinex", "event", ("signal_ts",),
               watch=("markouts.60s.markout_mid_bps",)),
    StreamSpec("fill_markouts", "fill_markouts.jsonl", "Paper fill markouts by liquidity (maker/taker)", "bitfinex", "event",
               ("fill_ts",), watch=("markouts.60s.markout_mid_bps",)),
    StreamSpec("chase_offset_touch_grid", "chase_offset_touch_grid.jsonl", "Limit-offset touch grid (would a resting limit fill)",
               "bitfinex", "event", ("signal_ts", "ts", "bucket_ts")),
    StreamSpec("xvl_shadow_signals", "xvl_shadow_signals.jsonl", "Cross-venue lead shadow triggers and outcomes",
               "binance,bybit,okx,bitfinex", "event", ("anchor_bucket_ts",), watch=("lead_bp", "net_bp_after_spread")),
    StreamSpec("signal_replay", "signal_replay.jsonl", "Per-trade replay paths (Tier B, reconstructible)", "bitfinex", "event",
               ("ts", "signal_ts", "recorded_at")),
    StreamSpec("post_exit_replay", "post_exit_replay.jsonl", "Post-exit price paths (Tier B)", "bitfinex", "event",
               ("ts", "exit_ts", "recorded_at")),
    StreamSpec("order_multiverse_entry_grid", "order_multiverse_entry_grid.jsonl", "Entry-offset multiverse grid (Tier B)",
               "bitfinex", "event", ("ts", "signal_ts")),
    StreamSpec("research_events_v22", "research_events_v22.jsonl", "Research event log v22", "bitfinex", "event",
               ("ts", "recorded_at", "created_at")),
)
_BY_PATH = {s.path: s for s in CATALOG}


@dataclass(frozen=True)
class Question:
    id: str
    question: str
    screen_inputs: tuple[tuple[str, str], ...]   # (stream, field) needed by the gated screens
    full_inputs: tuple[tuple[str, str], ...]     # needed to answer the question with real outcomes
    samples: dict[str, Any] = field(default_factory=dict)
    screens: tuple[str, ...] = ()
    note: str = ""


QUESTIONS: tuple[Question, ...] = (
    Question("Q_VOL_GATE", "Skip / take / wait when volatility is high",
             (("bitfinex_tape_1s", "bid"), ("bitfinex_tape_1s", "ask"), ("bitfinex_tape_1s", "buy_qty")),
             (("market_context_1m", "regime.label"), ("adaptive_entry_decisions", "ai_feature.win_prob"),
              ("counterfactual", "epoch_id"), ("counterfactual", "opportunity_id")),
             {"edge_events": {"min_days": 5, "min_clusters": 40, "min_n": 300}},
             ("VOLHI_MOM_R15M", "VOLHI_FADE_R15M", "VOLLO_MOM_R15M", "VOLLO_FADE_R15M"),
             "Screens gate on the 15-minute realized-vol tercile fixed on the training window."),
    Question("Q_REGIME_GATE", "ADX / regime-gated dynamic strategy",
             (("bitfinex_tape_1s", "bid"), ("bitfinex_tape_1s", "ask")),
             (("pre_entry_features", "features.adx"), ("pre_entry_features", "features.adx_normalized"),
              ("market_context_1m", "regime.label")),
             {"edge_events": {"min_days": 5, "min_clusters": 40, "min_n": 300}},
             ("TREND_MOM_R15M", "CHOP_FADE_R15M", "CHOP_FADE_R5M"),
             "Screens use the 60-minute trend z (tz60) as the tape-only regime proxy; ADX gating needs pre_entry_features."),
    Question("Q_LIMIT_VS_TAKER", "Resting limit below price vs taker",
             (), (("taker_signal_counterfactuals", "markouts.60s.markout_mid_bps"),
                  ("fill_markouts", "markouts.60s.markout_mid_bps"), ("fill_markouts", "liquidity"),
                  ("chase_offset_touch_grid", None)),
             {"streams": {"taker_signal_counterfactuals": {"min_rows": 300, "min_days": 5},
                          "fill_markouts": {"min_rows": 100, "min_days": 5}}},
             (), "Needs a queue-position fill model; screen not implemented yet."),
    Question("Q_XVENUE", "Cross-venue triggers",
             (), (("cross_venue_tape_1m", "basis_bp_mean.binance"), ("xvl_shadow_signals", "lead_bp"),
                  ("xvl_shadow_signals", "net_bp_after_spread"), ("liquidations", "qty_btc")),
             {"streams": {"xvl_shadow_signals": {"min_rows": 300, "min_days": 5},
                          "cross_venue_tape_1m": {"min_days": 5}}},
             (), "Evaluated by the bot's own XVL shadow lane; no tape screen here."),
)


# ------------------------------------------------------------------ helpers

def _retention_tier_fn(repo_root: Path):
    p = repo_root / "services" / "btc-conservative-agent" / "data_retention_policy.py"
    try:
        spec = importlib.util.spec_from_file_location("_sa_retention_policy", p)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        return lambda rel: mod.classify(rel) or "UNTIERED"
    except Exception:  # noqa: BLE001 - tier is descriptive; never block the catalog on it
        return lambda rel: "UNKNOWN"


def _flatten(d: dict, prefix: str = "", depth: int = 0, out: dict | None = None) -> dict:
    out = {} if out is None else out
    for k, v in d.items():
        key = f"{prefix}{k}"
        if isinstance(v, dict) and depth < FLATTEN_DEPTH - 1 and v:
            _flatten(v, key + ".", depth + 1, out)
        else:
            out[key] = v
    return out


def _read_tail(path: Path, budget: int) -> tuple[bytes, bool]:
    size = path.stat().st_size
    with open(path, "rb") as fh:
        if size <= max(budget, WHOLE_FILE_BYTES):
            return fh.read(), True
        fh.seek(size - budget)
        return fh.read(), False


def _read_head_line(path: Path) -> bytes:
    with open(path, "rb") as fh:
        return fh.read(65536).split(b"\n", 2)[1 if path.suffix == ".csv" else 0]


def _rows_jsonl(blob: bytes, whole: bool) -> list[dict]:
    lines = blob.split(b"\n")
    if not whole:
        lines = lines[1:]
    out = []
    for line in lines[-MAX_ROWS:]:
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            out.append(row)
    return out


def _rows_csv(path: Path, blob: bytes, whole: bool) -> list[dict]:
    with open(path, "rb") as fh:
        header = fh.readline().decode("utf-8", "replace").strip()
    lines = blob.decode("utf-8", "replace").splitlines()[1:]  # header row, or a partial first line of a tail
    reader = csv.DictReader(io.StringIO(header + "\n" + "\n".join(lines[-MAX_ROWS:])))
    return [r for r in reader]


def _row_ts(row: dict, fields: Iterable[str]) -> float | None:
    for f in fields:
        v = row
        for part in f.split("."):
            v = v.get(part) if isinstance(v, dict) else None
        t = parse_ts(v)
        if t:
            return t
    return None


def _is_null(v: Any) -> bool:
    return v is None or v == "" or v == [] or v == {} or (isinstance(v, str) and v.lower() in ("none", "null", "nan"))


def _num(v: Any) -> float | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v) if math.isfinite(float(v)) else None
    if isinstance(v, str):
        try:
            x = float(v)
            return x if math.isfinite(x) else None
        except ValueError:
            return None
    return None


def profile_fields(rows: list[dict], spec: StreamSpec | None) -> list[dict]:
    flat = [_flatten(r) for r in rows]
    keys: dict[str, None] = {}
    for r in flat:
        for k in r:
            keys.setdefault(k, None)
    watch = set(spec.watch) if spec else set()
    allow = set(spec.allow_constant) if spec else set()
    out = []
    for k in keys:
        vals = [r[k] for r in flat if k in r]
        n = len(vals)
        live = [v for v in vals if not _is_null(v)]
        nums = [x for x in (_num(v) for v in live) if x is not None]
        distinct: set = set()
        for v in live:
            distinct.add(json.dumps(v, sort_keys=True, default=str)[:200])
            if len(distinct) > 50:
                break
        zero_pct = (sum(1 for x in nums if x == 0) / len(nums)) if nums else None
        status = "OK"
        if n >= 20 and not live:
            status = "DEAD_NULL"
        elif n >= 20 and nums and len(nums) == len(live) and zero_pct == 1.0 and (k in watch or _MEASURE.search(k)):
            status = "DEAD_ZERO"
        elif n >= 50 and len(distinct) == 1 and k not in allow:
            status = "CONSTANT"
        out.append({
            "field": k, "n": n, "null_pct": round(100 * (1 - len(live) / n), 1) if n else None,
            "distinct": len(distinct) if len(distinct) <= 50 else ">50", "zero_pct": round(100 * zero_pct, 1) if zero_pct is not None else None,
            "constant_value": (next(iter(distinct))[:80] if len(distinct) == 1 else None),
            "status": status, "watched": k in watch,
            "alarm": k in watch and status in ("DEAD_NULL", "DEAD_ZERO", "CONSTANT"),
        })
    for w in watch - set(keys):
        out.append({"field": w, "n": 0, "null_pct": None, "distinct": 0, "zero_pct": None, "constant_value": None,
                    "status": "MISSING" if rows else "NO_ROWS", "watched": True, "alarm": bool(rows)})
    return out


def _hourly(ts: list[float], now: float, span_hours: int, expected: float | None, cadence: str) -> list[dict]:
    if not ts:
        return []
    end_h = int(now // 3600)
    t_first = min(ts)
    first_h = max(int(t_first // 3600), end_h - span_hours + 1)
    counts: dict[int, int] = {}
    for t in ts:
        h = int(t // 3600)
        if h >= first_h:
            counts[h] = counts.get(h, 0) + 1
    out = []
    for h in range(first_h, end_h + 1):
        lo, hi = max(h * 3600, t_first), min((h + 1) * 3600, now)
        exp = expected * max(0.0, hi - lo) / 3600 if expected else None
        n = counts.get(h, 0)
        out.append({"hour": iso(h * 3600), "actual": n, "expected": round(exp, 1) if exp else None,
                    "fill_pct": round(min(100.0, 100 * n / exp), 2) if exp else None,
                    "partial": h == end_h or lo > h * 3600})
    return out


def _gaps(ts_sorted: list[float], min_gap: float, interruptions: list[tuple[float, float, str]], limit: int = 20) -> list[dict]:
    gaps = []
    for a, b in zip(ts_sorted, ts_sorted[1:]):
        if b - a > min_gap:
            cause = next((c for s, e, c in interruptions if s <= b and e >= a), None)
            gaps.append({"start": iso(a), "end": iso(b), "sec": round(b - a, 1), "explained_by": cause})
    gaps.sort(key=lambda g: -g["sec"])
    return gaps[:limit]


# ------------------------------------------------------------------ discovery + catalog

def discover(mirror: Path) -> dict[str, dict]:
    """Group mirror files by logical stream (base path without rotation suffix)."""
    groups: dict[str, dict] = {}
    day_ago = time.time() - 86400
    for dp, dn, fn in os.walk(mirror):
        rel_dir = os.path.relpath(dp, mirror).replace("\\", "/")
        rel_dir = "" if rel_dir == "." else rel_dir
        skip = next((s for s in SKIP_DIRS if rel_dir == s or rel_dir.startswith(s + "/")), None)
        if skip:
            # Object stores (receipts, segments): one aggregate catalog row, no field profiling.
            g = groups.setdefault(skip + "/", {"files": [], "bytes": 0, "newest_mtime": 0.0, "dir": True,
                                               "n_files": 0, "bytes_24h": 0})
            for f in fn:
                try:
                    st = os.stat(os.path.join(dp, f))
                except OSError:
                    continue
                g["n_files"] += 1
                g["bytes"] += st.st_size
                g["newest_mtime"] = max(g["newest_mtime"], st.st_mtime)
                if st.st_mtime >= day_ago:
                    g["bytes_24h"] += st.st_size
            continue
        for f in fn:
            if f.endswith(SKIP_SUFFIXES):
                continue
            rel = f"{rel_dir}/{f}" if rel_dir else f
            m = _ROT.match(rel)
            base = m.group("base") if m else rel
            if not base.endswith((".jsonl", ".csv", ".db", ".sqlite3", ".json")):
                continue
            p = Path(dp) / f
            try:
                st = p.stat()
            except OSError:
                continue
            g = groups.setdefault(base, {"files": [], "bytes": 0, "newest_mtime": 0.0})
            g["files"].append((p, st.st_mtime, st.st_size))
            g["bytes"] += st.st_size
            g["newest_mtime"] = max(g["newest_mtime"], st.st_mtime)
    for g in groups.values():
        g["files"].sort(key=lambda x: -x[1])
    return groups


def _stream_profile(base: str, g: dict, spec: StreamSpec | None, now: float,
                    interruptions: list[tuple[float, float, str]]) -> tuple[dict, list[dict]]:
    kind = "csv" if base.endswith(".csv") else "jsonl" if base.endswith(".jsonl") else Path(base).suffix.lstrip(".")
    rows: list[dict] = []
    sampled_bytes = 0
    if kind in ("jsonl", "csv"):
        budget = MINUTE_TAIL_BYTES if (spec and spec.cadence == "minute") else TAIL_BYTES
        for p, _mt, size in g["files"]:
            if budget <= 0 or size == 0:
                continue
            try:
                blob, whole = _read_tail(p, budget)
            except OSError:
                continue
            sampled_bytes += len(blob)
            part = _rows_jsonl(blob, whole) if kind == "jsonl" else _rows_csv(p, blob, whole)
            rows = part + rows
            budget -= len(blob)
            if len(rows) >= MAX_ROWS:
                break
    rows = rows[-MAX_ROWS:]
    ts_fields = spec.ts_fields if spec else ("ts", "ts_epoch", "timestamp", "bucket_ts", "minute_ts", "recorded_at",
                                             "created_at", "signal_ts", "fill_ts")
    ts = sorted(t for t in (_row_ts(r, ts_fields) for r in rows) if t)
    first_ts = None
    oldest = g["files"][-1][0] if g["files"] else None
    if oldest is not None and kind in ("jsonl", "csv"):
        try:
            head = _read_head_line(oldest)
            row0 = json.loads(head) if kind == "jsonl" else next(csv.DictReader(io.StringIO(
                open(oldest, "rb").readline().decode("utf-8", "replace") + head.decode("utf-8", "replace"))))
            first_ts = _row_ts(row0, ts_fields)
        except Exception:  # noqa: BLE001 - head of a rotated file may be partial
            first_ts = None
    schemas = sorted({str(r.get("schema")) for r in rows if isinstance(r, dict) and r.get("schema")})[:5]
    avg_row = (sampled_bytes / len(rows)) if rows else None
    span = (ts[-1] - ts[0]) if len(ts) > 1 else None
    rate_h = (len(ts) / (span / 3600)) if span and span > 300 else None
    cadence = spec.cadence if spec else "event"
    fields = profile_fields(rows, spec) if rows else []
    doc = {
        "stream": spec.name if spec else base, "path": base, "catalogued": spec is not None,
        "what": spec.what if spec else "auto-discovered (not yet described)",
        "venue": spec.venue if spec else None, "cadence": cadence, "kind": kind,
        "expected_per_hour": spec.expected_per_hour if spec else None,
        "observed_per_hour": round(rate_h, 2) if rate_h else None,
        "fly_location": f"{FLY_DATA_DIR}/{base}", "laptop_location": None,
        "files": len(g["files"]), "bytes": g["bytes"], "newest_mtime": iso(g["newest_mtime"]),
        "rows_estimated": int(g["bytes"] / avg_row) if avg_row else None,
        "bytes_per_day": int(rate_h * 24 * avg_row) if (rate_h and avg_row) else None,
        "schema": schemas, "fields": len(fields), "sample_rows": len(rows),
        "sample_span_h": round(span / 3600, 2) if span else None,
        "first_ts": iso(first_ts) if first_ts else (iso(ts[0]) if ts else None),
        "last_ts": iso(ts[-1]) if ts else None, "last_epoch": ts[-1] if ts else None,
        "first_epoch": first_ts or (ts[0] if ts else None),
        "critical": bool(spec and spec.critical), "note": spec.note if spec else "",
        "dead_fields": [f["field"] for f in fields if f["status"] in ("DEAD_NULL", "DEAD_ZERO")][:40],
        "watch_alarms": [f"{f['field']}={f['status']}" + (f" ({f['constant_value']})" if f["constant_value"] else "")
                         for f in fields if f["alarm"]],
    }
    if spec and spec.cadence in ("second", "minute") and ts:
        doc["hourly"] = _hourly(ts, now, 48, spec.expected_per_hour, cadence)
        doc["gaps"] = _gaps(ts, GAP_MIN_SEC[cadence], interruptions)
    elif ts:
        doc["hourly"] = _hourly(ts, now, 48, None, cadence)
        doc["gaps"] = []
    return doc, fields


def tape_completeness(store, now: float, interruptions: list[tuple[float, float, str]], hours: int = 48) -> dict | None:
    """Exact per-second fill of the Bitfinex tape over the last N hours (DuckDB over the raw view)."""
    lo = int(now - hours * 3600)
    try:
        buckets = store.read(f"SELECT CAST(bucket_ts // 3600 AS BIGINT) AS h, count(DISTINCT bucket_ts) AS n "
                             f"FROM raw_tape_1s WHERE bucket_ts >= {lo} GROUP BY 1 ORDER BY 1")
        gaps = store.read(f"""SELECT a, b, b - a AS sec FROM (
                                SELECT lag(bucket_ts) OVER (ORDER BY bucket_ts) AS a, bucket_ts AS b
                                FROM (SELECT DISTINCT bucket_ts FROM raw_tape_1s WHERE bucket_ts >= {lo}))
                              WHERE b - a > {GAP_MIN_SEC['second']} ORDER BY sec DESC LIMIT 500""")
        rng = store.read(f"SELECT min(bucket_ts) AS t0, max(bucket_ts) AS t1, count(DISTINCT bucket_ts) AS n, "
                         f"count(DISTINCT bucket_ts) FILTER (WHERE bucket_ts >= {int(now - 86400)}) AS n24 "
                         f"FROM raw_tape_1s WHERE bucket_ts >= {lo}")[0]
    except Exception:  # noqa: BLE001
        return None
    if not rng["n"]:
        return None
    t0, t1 = int(rng["t0"]), int(rng["t1"])
    last24 = int(rng["n24"] or 0)
    exp24 = max(1, t1 - max(t0, int(now - 86400)) + 1)
    gl = [{"start": iso(g["a"]), "end": iso(g["b"]), "sec": int(g["sec"]), "a": g["a"], "b": g["b"],
           "explained_by": next((c for s, e, c in interruptions if s <= g["b"] and e >= g["a"]), None)} for g in gaps]
    explained24 = sum(min(g["b"], now) - max(g["a"], now - 86400) - 1 for g in gl
                      if g["explained_by"] and g["b"] >= now - 86400)
    for g in gl:
        g.pop("a"), g.pop("b")
    in24 = [g for g in gl if parse_ts(g["end"]) >= now - 86400]
    return {
        "window_start": iso(t0), "window_end": iso(t1), "seconds_present": int(rng["n"]),
        "seconds_expected": t1 - t0 + 1, "fill_pct": round(100 * int(rng["n"]) / (t1 - t0 + 1), 3),
        "fill_pct_24h": round(100 * last24 / exp24, 3),
        "fill_pct_24h_excl_interruptions": round(100 * last24 / max(1, exp24 - max(0, explained24)), 3),
        "gaps_24h": len(in24), "gap_sec_24h": sum(g["sec"] for g in in24),
        "hourly": [{"hour": iso(int(b["h"]) * 3600), "actual": int(b["n"]), "expected": 3600,
                    "fill_pct": round(100 * int(b["n"]) / 3600, 2)} for b in buckets],
        "gaps": gl[:25],
        "unexplained_gaps_24h": [g for g in in24 if not g["explained_by"]][:25],
    }


# ------------------------------------------------------------------ capacity

SLOPE_MIN_SPAN_SEC = 6 * 3600
# A drop this large between two samples is a one-off reclaim (manual dedupe, scratch purge, accounting change),
# not steady-state retention; the slope restarts after it so it cannot hide real growth.
RECLAIM_STEP_BYTES = 2e9
GROWTH_KINDS = ("APPEND", "SEAL", "BASELINE")


def _record(hist: list, now: float, value: float, *, min_gap: float = 600, keep: int = 400) -> None:
    if value and (not hist or now - hist[-1][0] >= min_gap):
        hist.append([now, value])
        del hist[:-keep]


def _slope_per_day(hist: list, now: float, *, window_sec: float = 3 * 86400, invert: bool = False) -> float | None:
    """Least-squares bytes/day over the window, restarted after the latest one-off reclaim step."""
    recent = [(t, (-b if invert else b)) for t, b in hist if t >= now - window_sec]
    for i in range(len(recent) - 1, 0, -1):
        if recent[i - 1][1] - recent[i][1] >= RECLAIM_STEP_BYTES:
            recent = recent[i:]
            break
    if len(recent) < 3 or recent[-1][0] - recent[0][0] < SLOPE_MIN_SPAN_SEC:
        return None
    xs = [t for t, _ in recent]
    ys = [b for _, b in recent]
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    den = sum((x - mx) ** 2 for x in xs)
    return (sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den) * 86400 if den else None


def fly_ingest_from_manifests(manifest_dir: Path, now: float, window_sec: float = 86400) -> dict | None:
    """Real Fly->laptop ingest from the archived segment manifests (member payload bytes, not stream samples)."""
    if not manifest_dir or not Path(manifest_dir).is_dir():
        return None
    growth = churn = 0
    kinds: dict[str, int] = {}
    t0 = t1 = None
    manifests = 0
    for entry in os.scandir(manifest_dir):
        if not entry.name.endswith(".json"):
            continue
        try:
            if entry.stat().st_mtime < now - window_sec - 3600:
                continue
            doc = json.loads(Path(entry.path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        end = doc.get("window_end")
        if not isinstance(end, (int, float)) or end < now - window_sec:
            continue
        manifests += 1
        t0 = end if t0 is None else min(t0, end)
        t1 = end if t1 is None else max(t1, end)
        for member in doc.get("members") or []:
            size = int(member.get("size") or 0)
            kind = str(member.get("kind") or "")
            kinds[kind] = kinds.get(kind, 0) + size
            if kind in GROWTH_KINDS:
                growth += size
            else:
                churn += size
    if not manifests or t0 is None or t1 is None or t1 - t0 < 3600:
        return None
    span = t1 - t0
    return {"append_gb_per_day": round(growth / span * 86400 / 1e9, 3),
            "snapshot_churn_gb_per_day": round(churn / span * 86400 / 1e9, 3),
            "bytes_by_kind": kinds, "manifests": manifests, "span_h": round(span / 3600, 2),
            "basis": "sum of APPEND/SEAL/BASELINE member bytes in archived segment manifests"}


def _disk_usage(root: Path) -> tuple[int, int] | None:
    try:
        usage = shutil.disk_usage(str(root))
    except OSError:
        return None
    return int(usage.free), int(usage.total)


def capacity(paths, facts: dict, streams: list[dict], state: dict, now: float) -> dict:
    ret = read_json(paths.retention / "status.json") or {}
    used = float(ret.get("bytes_after") or 0)
    cap = float(ret.get("cap_bytes") or 50e9)
    # Retention sizes became physical (hardlinks counted once) with sizes_basis=physical_v1; logical history
    # from before would fake a huge negative slope, so it is dropped once.
    basis = ret.get("sizes_basis") or "logical"
    if state.get("capacity_basis") != basis:
        state["capacity_basis"] = basis
        state["capacity_history"] = []
        state["capacity_area_history"] = {}
    hist = state.setdefault("capacity_history", [])
    _record(hist, now, used)
    measured = _slope_per_day(hist, now)
    sizes = ret.get("sizes_after") or {}
    area_hist = state.setdefault("capacity_area_history", {})
    by_area_growth = {}
    for area, value in sizes.items():
        series = area_hist.setdefault(area, [])
        _record(series, now, float(value or 0))
        slope_area = _slope_per_day(series, now)
        by_area_growth[area] = round(slope_area / 1e9, 3) if slope_area is not None else None
    ingest_streams = sum(s.get("bytes_per_day") or 0 for s in streams)
    retained = sum(s.get("bytes_per_day") or 0 for s in streams if s.get("retention_tier") != "TIER_B")
    fly_real = fly_ingest_from_manifests(getattr(paths, "segment_manifests", None), now)
    real_ingest = fly_real["append_gb_per_day"] * 1e9 if fly_real else None
    mirror_bytes = float(sizes.get("mirror_tree") or 0)
    copies = (used / mirror_bytes) if used and mirror_bytes else 1.0
    # Until 6 h of history exists, project from the measured Fly ingest (falling back to stream samples)
    # times the physical laptop/mirror ratio.
    base_ingest = real_ingest if real_ingest is not None else retained
    slope = measured if measured is not None else base_ingest * copies
    room = cap * LAPTOP_CAP_FRACTION - used
    days_to_cap = (room / slope) if slope and slope > 0 else None
    # Whole-disk truth: every writer on the laptop volume, managed or not.
    disk_now = _disk_usage(getattr(paths, "laptop_root", Path("C:\\")))
    disk_hist = state.setdefault("disk_free_history", [])
    disk_slope = None
    if disk_now:
        _record(disk_hist, now, float(disk_now[0]))
        freed = _slope_per_day(disk_hist, now, invert=True)
        disk_slope = freed
    disk_days = (disk_now[0] / disk_slope) if disk_now and disk_slope and disk_slope > 0 else None
    unmanaged = (disk_slope - measured) if disk_slope is not None and measured is not None else None
    dedupe = ret.get("storage_dedupe") or {}
    disk = (watcher_check(facts, "disk.space") or {}).get("observed") or ""
    m_free = re.search(r"Fly volume free ([\d.]+)GB \(([\d.]+)h to full\)", disk)
    m_seg = re.search(r"segment store ([\d.]+)% of cap", disk)
    m_lap = re.search(r"laptop free ([\d.]+)GB", disk)
    return {
        "laptop": {
            "bot_data_gb": round(used / 1e9, 2), "cap_gb": round(cap / 1e9, 1), "usage_pct": round(100 * used / cap, 1) if cap else None,
            "sizes_basis": basis,
            "by_area_gb": {k: round(v / 1e9, 2) for k, v in sizes.items()},
            "by_area_growth_gb_per_day": by_area_growth,
            "retention_mode": ret.get("mode"), "retention_last_run": ret.get("finished_at"),
            "growth_gb_per_day": round(slope / 1e9, 3) if slope else None,
            "growth_basis": ("measured net slope of physical bot-data bytes (restarted after one-off reclaims)"
                             if measured is not None else
                             f"estimate: {'measured Fly append ingest' if real_ingest is not None else 'per-stream ingest excluding Tier B'}"
                             f" x {copies:.1f} (physical laptop bytes / mirror bytes); replaced by the measured slope after 6 h"),
            "mirror_copies_factor": round(copies, 2),
            "days_to_90pct_cap": round(days_to_cap, 1) if days_to_cap is not None else None,
            "disk_free_gb": round(disk_now[0] / 1e9, 2) if disk_now else (float(m_lap.group(1)) if m_lap else None),
            "disk_growth_gb_per_day": round(disk_slope / 1e9, 3) if disk_slope is not None else None,
            "disk_days_to_full": round(disk_days, 1) if disk_days is not None else None,
            "unmanaged_growth_gb_per_day": round(unmanaged / 1e9, 3) if unmanaged is not None else None,
            "duplicate_physical_gb": dedupe.get("duplicate_physical_gb"),
            "hardlink_saved_gb": dedupe.get("hardlink_saved_gb"),
            "link_fallback_alarms_24h": dedupe.get("link_fallback_alarms_24h"),
            "source": "bot-data-retention/status.json (physical) + disk free history + segment manifests",
        },
        "fly": {
            "volume_free_gb": float(m_free.group(1)) if m_free else None,
            "hours_to_full": float(m_free.group(2)) if m_free else None,
            "segment_store_pct_of_cap": float(m_seg.group(1)) if m_seg else None,
            "ingest_gb_per_day": (fly_real["append_gb_per_day"] if fly_real else round(ingest_streams / 1e9, 3)),
            "ingest_basis": fly_real["basis"] if fly_real else "sum of sampled per-stream rates (manifests unavailable)",
            "snapshot_churn_gb_per_day": fly_real["snapshot_churn_gb_per_day"] if fly_real else None,
            "stream_sample_ingest_gb_per_day": round(ingest_streams / 1e9, 3),
            "source": "watcher disk.space (no Fly call) + archived segment manifests",
        },
        "tier_a": [{"dataset": d.get("dataset"), "bytes": d.get("bytes"), "partitions": d.get("partitions"),
                    "status": d.get("status")} for d in (ret.get("tier_a_schema") or [])],
    }


# ------------------------------------------------------------------ sufficiency

def _field_state(stream: str, fld: str | None, streams: dict[str, dict], fields: dict[str, dict[str, dict]]) -> str:
    s = streams.get(stream)
    if not s:
        return "STREAM_MISSING"
    if not s["sample_rows"]:
        return "NO_ROWS"
    if fld is None:
        return "OK"
    f = fields.get(stream, {}).get(fld)
    if not f:
        return "FIELD_MISSING"
    return {"DEAD_NULL": "DEAD", "DEAD_ZERO": "DEAD", "CONSTANT": "CONSTANT"}.get(f["status"], "OK")


def sufficiency(store, streams: dict[str, dict], fields: dict[str, dict[str, dict]], now: float) -> list[dict]:
    ev = None
    try:
        if store.table_exists("res_edge_events"):
            ev = store.read("SELECT count(*) AS n, count(DISTINCT CAST(ts // 86400 AS BIGINT)) AS days, "
                            "count(DISTINCT CAST(ts // 3600 AS BIGINT)) AS clusters, min(ts) AS t0, max(ts) AS t1 "
                            "FROM res_edge_events")[0]
    except Exception:  # noqa: BLE001
        ev = None
    out = []
    for q in QUESTIONS:
        blockers, full_blockers, short, etas = [], [], [], []
        for st, fld in q.screen_inputs:
            s = _field_state(st, fld, streams, fields)
            if s != "OK":
                blockers.append(f"{st}.{fld or '*'} {s}")
        for st, fld in q.full_inputs:
            s = _field_state(st, fld, streams, fields)
            if s != "OK":
                full_blockers.append(f"{st}.{fld or '*'} {s}")
        samples: dict[str, Any] = {}
        need = q.samples.get("edge_events")
        if need:
            have = ev or {"n": 0, "days": 0, "clusters": 0}
            samples["edge_events"] = {k: int(have.get(k) or 0) for k in ("n", "days", "clusters")}
            if int(have.get("days") or 0) < need["min_days"]:
                short.append(f"edge_events days {have.get('days') or 0} < {need['min_days']}")
                etas.append(now + (need["min_days"] - int(have.get("days") or 0)) * 86400)
            if int(have.get("clusters") or 0) < need["min_clusters"]:
                short.append(f"edge_events hour clusters {have.get('clusters') or 0} < {need['min_clusters']}")
                etas.append(now + (need["min_clusters"] - int(have.get("clusters") or 0)) * 3600)
            if int(have.get("n") or 0) < need["min_n"]:
                short.append(f"edge_events n {have.get('n') or 0} < {need['min_n']}")
                etas.append(now + (need["min_n"] - int(have.get("n") or 0)) * 300)
        for st, need_s in (q.samples.get("streams") or {}).items():
            s = streams.get(st) or {}
            rows = int(s.get("rows_estimated") or 0)
            days = ((s.get("last_epoch") or now) - (s.get("first_epoch") or now)) / 86400 if s else 0.0
            samples[st] = {"rows": rows, "days_in_mirror": round(days, 2), "per_hour": s.get("observed_per_hour")}
            if need_s.get("min_rows") and rows < need_s["min_rows"]:
                short.append(f"{st} rows {rows} < {need_s['min_rows']}")
                rate = (s.get("observed_per_hour") or 0) * 24
                if rate > 0:
                    etas.append(now + (need_s["min_rows"] - rows) / rate * 86400)
            if need_s.get("min_days") and days < need_s["min_days"]:
                short.append(f"{st} days {days:.1f} < {need_s['min_days']} (mirror horizon)")
                etas.append(now + (need_s["min_days"] - days) * 86400)
        gating = blockers if q.screens else full_blockers
        status = "BLOCKED" if gating else "ACCUMULATING" if short else "READY"
        out.append({
            "id": q.id, "question": q.question, "status": status,
            "full_question_status": "BLOCKED" if full_blockers else ("ACCUMULATING" if short else "READY"),
            "screens": list(q.screens), "screens_released": status == "READY" and bool(q.screens),
            "blockers": blockers, "missing_for_full_answer": full_blockers, "short_samples": short,
            "samples": samples, "eta_ready": iso(max(etas)) if etas and not gating else None, "note": q.note,
        })
    return out


# ------------------------------------------------------------------ run

def run(store, paths, facts: dict, state: dict, interruptions: list[tuple[float, float, str]] | None = None,
        now: float | None = None, repo_root: Path | None = None) -> dict:
    now = time.time() if now is None else now
    started = time.time()
    interruptions = interruptions or []
    tier = _retention_tier_fn(repo_root or Path(__file__).resolve().parents[2])
    groups = discover(paths.mirror)
    streams, field_rows = [], []
    fields_by: dict[str, dict[str, dict]] = {}
    for base, g in sorted(groups.items()):
        if g.get("dir"):
            streams.append({"stream": base, "path": base, "catalogued": False, "kind": "dir", "cadence": "event",
                            "what": "object store (one file per record); aggregate only",
                            "files": g["n_files"], "bytes": g["bytes"], "bytes_per_day": g["bytes_24h"],
                            "newest_mtime": iso(g["newest_mtime"]), "sample_rows": 0, "fields": 0, "critical": False,
                            "status": "FRESH" if now - g["newest_mtime"] < STALE_SEC["event"] else "IDLE",
                            "retention_tier": tier(base), "fly_location": f"{FLY_DATA_DIR}/{base}",
                            "laptop_location": str(paths.mirror / base), "watch_alarms": [], "dead_fields": [],
                            "bytes_per_day_basis": "bytes of files modified in the last 24 h"})
            continue
        spec = _BY_PATH.get(base)
        doc, flds = _stream_profile(base, g, spec, now, interruptions)
        doc["retention_tier"] = tier(base)
        doc["laptop_location"] = str(paths.mirror / base)
        streams.append(doc)
        fields_by[doc["stream"]] = {f["field"]: f for f in flds}
        field_rows.extend({"stream": doc["stream"], **f} for f in flds)
    for spec in CATALOG:
        if spec.path not in groups:
            streams.append({"stream": spec.name, "path": spec.path, "catalogued": True, "what": spec.what, "venue": spec.venue,
                            "cadence": spec.cadence, "status": "MISSING", "files": 0, "bytes": 0, "sample_rows": 0,
                            "critical": spec.critical, "fly_location": f"{FLY_DATA_DIR}/{spec.path}", "watch_alarms": [],
                            "dead_fields": [], "bytes_per_day": None, "retention_tier": tier(spec.path)})
    head = max((s.get("last_epoch") or 0) for s in streams if s.get("critical")) or None
    for s in streams:
        if s.get("status") == "MISSING" or s.get("kind") == "dir":
            continue
        lag = (head - s["last_epoch"]) if head and s.get("last_epoch") else None
        s["lag_vs_mirror_head_sec"] = round(lag, 1) if lag is not None else None
        limit = STALE_SEC.get(s["cadence"], STALE_SEC["event"])
        s["status"] = ("NO_TIMESTAMP" if s.get("last_epoch") is None else
                       "STALE" if (lag or 0) > limit and s.get("critical") else
                       "IDLE" if (lag or 0) > limit else "FRESH")
    tape = tape_completeness(store, now, interruptions)
    by_name = {s["stream"]: s for s in streams}
    if tape and "bitfinex_tape_1s" in by_name:
        by_name["bitfinex_tape_1s"]["exact"] = tape
    cap = capacity(paths, facts, streams, state, now)
    suff = sufficiency(store, by_name, fields_by, now)
    fr = pd.DataFrame(field_rows)
    if len(fr):
        for c in ("distinct", "constant_value"):
            fr[c] = fr[c].astype(str)
        store.publish("data_fields", fr, sources=["fly mirror tree (tail samples)"], window=(iso(now), iso(now)),
                      note=f"tail {TAIL_BYTES}B / {MAX_ROWS} rows per stream")
    cat_cols = ("stream", "path", "catalogued", "what", "venue", "cadence", "kind", "status", "critical", "retention_tier",
                "expected_per_hour", "observed_per_hour", "files", "bytes", "rows_estimated", "bytes_per_day", "first_ts",
                "last_ts", "lag_vs_mirror_head_sec", "fields", "sample_rows", "fly_location", "laptop_location")
    store.publish("data_catalog", pd.DataFrame([{c: (json.dumps(s.get(c)) if isinstance(s.get(c), (list, dict)) else s.get(c))
                                                  for c in cat_cols} for s in streams]),
                  sources=["fly mirror tree", "data_retention_policy.py"], window=(iso(now), iso(now)))
    store.publish("data_sufficiency", pd.DataFrame([{**q, **{k: json.dumps(q[k]) for k in
                                                         ("screens", "blockers", "missing_for_full_answer", "short_samples", "samples")}}
                                                    for q in suff]),
                  sources=["res_data_fields", "res_data_catalog", "res_edge_events"], window=(iso(now), iso(now)))
    crit = [s for s in streams if s.get("critical")]
    summary = {
        "schema": "self_aware_data_v1", "generated_at": iso(now), "ms": int((time.time() - started) * 1000),
        "mirror_head": iso(head) if head else None,
        "streams": len(streams), "catalogued": sum(1 for s in streams if s.get("catalogued")),
        "uncatalogued": sum(1 for s in streams if not s.get("catalogued")),
        "stale_critical": [s["stream"] for s in crit if s.get("status") in ("STALE", "MISSING", "NO_TIMESTAMP")],
        "watch_alarms": {s["stream"]: s["watch_alarms"] for s in streams if s.get("watch_alarms")},
        "dead_field_count": int((fr["status"].isin(["DEAD_NULL", "DEAD_ZERO"])).sum()) if len(fr) else 0,
        "tape": {k: tape[k] for k in ("fill_pct", "fill_pct_24h", "fill_pct_24h_excl_interruptions", "gaps_24h",
                                      "gap_sec_24h", "window_start", "window_end")} | {
            "unexplained_gaps_24h": tape["unexplained_gaps_24h"][:5], "largest_gaps": tape["gaps"][:5]} if tape else None,
        "minute_streams": {s["stream"]: _minute_fill(s, now) for s in crit if s.get("cadence") == "minute"},
        "capacity": cap, "sufficiency": [{k: q[k] for k in ("id", "status", "full_question_status", "eta_ready",
                                                            "screens_released")} for q in suff],
    }
    return {"summary": summary, "streams": streams, "sufficiency": suff, "capacity": cap}


def _minute_fill(s: dict, now: float) -> dict:
    hrs = [h for h in s.get("hourly") or [] if parse_ts(h["hour"]) >= now - 86400 and not h.get("partial")]
    exp = sum(h["expected"] or 0 for h in hrs)
    act = sum(min(h["actual"], h["expected"] or h["actual"]) for h in hrs)
    return {"fill_pct_24h": round(100 * act / exp, 2) if exp else None, "hours": len(hrs),
            "sample_span_h": s.get("sample_span_h"), "largest_gaps": (s.get("gaps") or [])[:3]}
