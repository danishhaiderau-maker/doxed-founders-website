"""Offline per-signal research table (PR-D, post-freeze; PAPER / analysis only).

Never imported by ``bot.py`` or any runtime path. It runs on the laptop or the box over a copy of the Fly
``runtime/`` folder (or a promoted shadow tree) and writes one row per signal:

* ``AI_CALL``         - every shared AI call (``scan-*``), with the AI's raw decision, scores and commit flags;
* ``XVENUE_SIGNAL``   - every cross-venue lead / premium / session-follow trigger (``xvl-*``, ``xvp-*``, ``xvs-*``);
* ``TILE_ENTRY``      - every tile entry candidate in the trade ledger (filled) or the expired-order ledger (unfilled).

For each row it takes the bot's own 1 s Bitfinex tape (``market_microstructure_1s_v1``: L1 bid/ask) and records the
forward price path at 1 s, 5 s, 15 s, 30 s, 1 m, 3 m, 5 m, 15 m, 30 m, 60 m, 90 m, 120 m and 240 m, with the maximum
favourable / adverse excursion up to each horizon. The schema is documented in
``docs/research/PER_SIGNAL_RESEARCH_TABLE.md`` and versioned by ``TABLE_SCHEMA``.

Usage::

    python -m research.signal_research_table --data-dir <runtime dir> --out-dir <dir>

The walk-forward variant scorer that consumes this table is ``research/walk_forward_scorer.py``.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import math
import sys
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

import numpy as np

try:
    from research import fill_model as fm
except ImportError:  # pragma: no cover - run as a plain script from the research/ folder
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from research import fill_model as fm

TABLE_SCHEMA = "per_signal_research_table_v1"
MANIFEST_SCHEMA = "per_signal_research_table_manifest_v1"
TAPE_SCHEMA = "market_microstructure_1s_v1"
TAPE_GLOB = "market_microstructure_1s.jsonl*"

HORIZONS: tuple[tuple[str, int], ...] = (
    ("1s", 1), ("5s", 5), ("15s", 15), ("30s", 30), ("1m", 60), ("3m", 180), ("5m", 300), ("15m", 900),
    ("30m", 1800), ("60m", 3600), ("90m", 5400), ("120m", 7200), ("240m", 14400),
)
MAX_HORIZON_SEC = HORIZONS[-1][1]
# A horizon sample is null when the last fresh quote at that second is older than this (tape hole / bot down).
PATH_MAX_QUOTE_AGE_SEC = 60.0
# The anchor quote is the first fresh quote at or after the signal second, within this wait (REALISTIC_V1 taker wait).
ANCHOR_MAX_WAIT_SEC = fm.TAKER_MAX_WAIT_SEC
AI_COMMIT_MIN_SCORE_GAP = 30.0  # mirrors combo_pathway_config.AI_COMMIT_MIN_SCORE_GAP / bot.ai_commit_flags
SESSION_HOURS_UTC = {"ASIA": (0, 8), "EU": (8, 16), "US": (16, 24)}  # combo_pathway_config.HYPOTHESIS_SESSION_HOURS_UTC

_TAPE_FIELDS = ("bid", "ask", "bid_qty", "ask_qty", "last", "source_age_sec", "buy_qty", "sell_qty", "buy_vwap",
                "sell_vwap", "trade_low", "trade_high")


# ------------------------------------------------------------------ tape

def _f(value: Any) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return math.nan
    return out if math.isfinite(out) else math.nan


class Tape:
    """Dense per-second arrays over the 1 s tape (index = bucket_ts - t0). Missing seconds are NaN / not present."""

    def __init__(self, rows: Iterable[Mapping[str, Any]]):
        by_ts: dict[int, Mapping[str, Any]] = {}
        for row in rows:
            if row.get("schema", TAPE_SCHEMA) != TAPE_SCHEMA:
                continue
            try:
                by_ts[int(row["bucket_ts"])] = row  # rotations overlap at the seams: last write wins
            except (KeyError, TypeError, ValueError):
                continue
        if not by_ts:
            raise ValueError("EMPTY_TAPE")
        self.t0, self.t1 = min(by_ts), max(by_ts)
        n = self.t1 - self.t0 + 1
        self.n_rows = len(by_ts)
        self.present = np.zeros(n, dtype=bool)
        self.fresh = np.zeros(n, dtype=bool)
        cols = {k: np.full(n, np.nan) for k in _TAPE_FIELDS}
        for ts, row in by_ts.items():
            i = ts - self.t0
            self.present[i] = True
            for k in _TAPE_FIELDS:
                cols[k][i] = _f(row.get(k))
            self.fresh[i] = bool(fm._row_fresh(row))
        for k, v in cols.items():
            setattr(self, k, v)
        valid = self.fresh & np.isfinite(self.bid) & np.isfinite(self.ask) & (self.ask >= self.bid) & (self.bid > 0)
        self.valid = valid
        # forward-filled executable quotes + age of the last valid quote (seconds)
        idx = np.where(valid, np.arange(n), -1)
        idx = np.maximum.accumulate(idx)
        has = idx >= 0
        safe = np.where(has, idx, 0)
        self.bid_ff = np.where(has, self.bid[safe], np.nan)
        self.ask_ff = np.where(has, self.ask[safe], np.nan)
        self.quote_age = np.where(has, np.arange(n) - idx, np.inf).astype(float)
        self.mid_ff = (self.bid_ff + self.ask_ff) / 2.0

    # -- index helpers
    def index(self, ts: float | int) -> int:
        return int(ts) - self.t0

    def covers(self, ts: float) -> bool:
        return self.t0 <= ts <= self.t1

    def first_valid_at_or_after(self, ts: float, max_wait: int = ANCHOR_MAX_WAIT_SEC) -> int | None:
        first = int(math.ceil(float(ts) - fm.PRICE_EPS)) - self.t0
        for i in range(max(first, 0), min(first + max_wait + 1, len(self.valid))):
            if self.valid[i]:
                return i
        return None

    def rows(self) -> "TapeRows":
        return TapeRows(self)

    def fingerprint(self) -> str:
        h = hashlib.sha256()
        h.update(np.ascontiguousarray(self.bid).tobytes())
        h.update(np.ascontiguousarray(self.ask).tobytes())
        return "sha256:" + h.hexdigest()[:16]


class TapeRows(Mapping):
    """Read-only ``{bucket_ts: row}`` view so ``fill_model`` REALISTIC_V1 functions run unchanged on the arrays."""

    def __init__(self, tape: Tape):
        self._t = tape

    def __getitem__(self, ts: int) -> dict[str, Any]:
        i = int(ts) - self._t.t0
        if i < 0 or i >= len(self._t.present) or not self._t.present[i]:
            raise KeyError(ts)
        t = self._t
        out: dict[str, Any] = {"bucket_ts": int(ts), "fresh": bool(t.fresh[i]), "valid_bbo": True}
        for k in _TAPE_FIELDS:
            v = getattr(t, k)[i]
            out[k] = None if math.isnan(v) else float(v)
        return out

    def __iter__(self) -> Iterator[int]:
        return (int(i) + self._t.t0 for i in np.flatnonzero(self._t.present))

    def __len__(self) -> int:
        return int(self._t.present.sum())


def iter_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    opener = gzip.open if path.suffix == ".gz" else open
    try:
        with opener(path, "rt", encoding="utf-8", errors="replace") as fh:
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
    except OSError:
        return


def tape_files(data_dir: Path) -> list[Path]:
    return sorted(p for p in Path(data_dir).glob(TAPE_GLOB)
                  if p.is_file() and not p.name.endswith((".json", ".tmp")) and ".validation" not in p.name)


def load_tape(data_dir: Path | str, extra_files: Iterable[Path] = ()) -> Tape:
    files = tape_files(Path(data_dir)) + [Path(p) for p in extra_files]
    if not files:
        raise FileNotFoundError(f"no {TAPE_GLOB} under {data_dir}")

    def _rows() -> Iterator[dict[str, Any]]:
        for p in files:
            yield from iter_jsonl(p)
    return Tape(_rows())


# ------------------------------------------------------------------ signals

def _iso_ts(value: Any) -> float | None:
    if value in (None, ""):
        return None
    v = _f(value)
    if not math.isnan(v):
        return v
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _side(value: Any) -> str:
    s = str(value or "").upper()
    return s if s in ("LONG", "SHORT") else "NONE"


def ai_commit_flags(raw_direction: Any, long_score: Any, short_score: Any, ai_error: bool = False) -> dict[str, Any]:
    """Same rule as ``bot.ai_commit_flags`` (re-implemented here so the tool never imports the bot)."""
    raw = _side(raw_direction)
    ls, ss = _f(long_score), _f(short_score)
    gap = abs(ls - ss) if not (math.isnan(ls) or math.isnan(ss)) else None
    score_side = "NONE" if gap is None or gap == 0 else ("LONG" if ls > ss else "SHORT")
    abstain = raw == "NONE"
    mismatch = (not abstain) and score_side != "NONE" and raw != score_side
    return {"score_led_side": score_side, "score_gap": gap, "explicit_abstain": abstain,
            "score_direction_mismatch": mismatch, "score_tie": gap == 0,
            "ai_explicit_aligned": bool(not abstain and not mismatch and score_side != "NONE" and not ai_error),
            "ai_committed": bool(not abstain and not mismatch and not ai_error and gap is not None
                                 and gap >= AI_COMMIT_MIN_SCORE_GAP)}


def _merge(dst: dict[str, Any], src: Mapping[str, Any]) -> None:
    for k, v in src.items():
        if v is not None and v != "" and dst.get(k) in (None, ""):
            dst[k] = v


def load_signals(data_dir: Path | str) -> list[dict[str, Any]]:
    """All signals found in a runtime folder. Every source is optional; absent files contribute nothing."""
    d = Path(data_dir)
    ai: dict[str, dict[str, Any]] = {}
    xv: dict[str, dict[str, Any]] = {}

    def ai_row(cid: str) -> dict[str, Any]:
        return ai.setdefault(cid, {"signal_id": cid, "signal_kind": "AI_CALL", "sources": set()})

    for r in iter_jsonl(d / "decision_feature_snapshots.jsonl"):
        cid = str(r.get("shared_ai_call_id") or "")
        if not cid.startswith("scan-") or r.get("row_kind", "SNAPSHOT") != "SNAPSHOT":
            continue
        a = r.get("ai") or {}
        row = ai_row(cid)
        row["sources"].add("decision_feature_snapshots")
        _merge(row, {"signal_ts": _f(r.get("decision_ts")), "ai_raw_decision": a.get("decision"),
                     "ai_raw_direction": a.get("raw_direction") or a.get("direction"),
                     "long_score": a.get("long_score"), "short_score": a.get("short_score"),
                     "ai_error": a.get("ai_error"), "ai_committed_logged": a.get("ai_committed"),
                     "epoch_id": r.get("epoch_id"), "prompt_id": a.get("prompt_id")})
    for r in iter_jsonl(d / "adaptive_entry_decisions.jsonl"):
        cid = str(r.get("shared_ai_call_id") or "")
        if cid.startswith("scan-"):
            a = r.get("ai_feature") or {}
            row = ai_row(cid)
            row["sources"].add("adaptive_entry_decisions")
            _merge(row, {"signal_ts": _f(r.get("signal_ts")), "ai_raw_decision": a.get("raw_decision"),
                         "ai_raw_direction": a.get("raw_direction"), "long_score": a.get("long_score"),
                         "short_score": a.get("short_score")})
        elif cid[:4] in ("xvl-", "xvp-", "xvs-"):
            row = xv.setdefault(cid, {"signal_id": cid, "signal_kind": "XVENUE_SIGNAL", "sources": set()})
            row["sources"].add("adaptive_entry_decisions")
            _merge(row, {"signal_ts": _f(r.get("signal_ts")), "direction": _side(r.get("direction")),
                         "xv_trigger_source": r.get("direction_source"), "research_lane": r.get("lane")})
    for r in iter_jsonl(d / "xvs_shadow_signals.jsonl"):
        cid = str(r.get("trigger_id") or "")
        if not cid or not (r.get("qualifies") or r.get("gate") == "TRIGGER"):
            continue
        row = xv.setdefault(cid, {"signal_id": cid, "signal_kind": "XVENUE_SIGNAL", "sources": set()})
        row["sources"].add("xvs_shadow_signals")
        _merge(row, {"signal_ts": _f(r.get("evaluated_ts")) if r.get("evaluated_ts") is not None
                     else _f(r.get("anchor_bucket_ts")), "direction": _side(r.get("side")),
                     "xv_trigger_source": r.get("trigger_source"),
                     "research_lane": "FAMILY_XVENUE_SESSION_FOLLOW_60M"})
    for r in iter_jsonl(d / "taker_signal_counterfactuals.jsonl"):
        cid = str(r.get("shared_ai_call_id") or "")
        if not cid.startswith("scan-"):
            continue
        row = ai_row(cid)
        row["sources"].add("taker_signal_counterfactuals")
        _merge(row, {"signal_ts": _f(r.get("signal_ts")), "ai_raw_decision": r.get("raw_ai_decision"),
                     "counterfactual_direction": _side(r.get("direction")), "epoch_id": r.get("data_epoch_id")})
    for r in iter_jsonl(d / "ai_input_log.jsonl"):
        cid = str(r.get("trade_id") or "")
        if cid.startswith("scan-"):
            row = ai_row(cid)
            row["sources"].add("ai_input_log")
            _merge(row, {"signal_ts": _f(r.get("ts_epoch"))})

    out: list[dict[str, Any]] = []
    for row in ai.values():
        flags = ai_commit_flags(row.get("ai_raw_direction"), row.get("long_score"), row.get("short_score"),
                                bool(row.get("ai_error")))
        row.update(flags)
        if row.get("ai_committed_logged") not in (None, ""):
            row["ai_committed"] = bool(row["ai_committed_logged"])
        row["direction"] = _side(row.get("ai_raw_direction"))
        out.append(row)
    out.extend(xv.values())

    def ledger(path: Path, kind: str, filled: bool) -> None:
        try:
            fh = open(path, newline="", encoding="utf-8", errors="replace")
        except OSError:
            return
        with fh:
            for r in csv.DictReader(fh):
                tid = r.get("trade_id") or ""
                ts = (_iso_ts(r.get("shared_ai_call_ts")) or _iso_ts(r.get("created_ts"))
                      or _iso_ts(r.get("time")) or _iso_ts(r.get("ts")))
                if not tid or ts is None:
                    continue
                out.append({"signal_id": f"{kind}:{tid}", "signal_kind": "TILE_ENTRY", "sources": {path.name},
                            "signal_ts": ts, "direction": _side(r.get("dir")), "research_lane": r.get("research_lane"),
                            "parent_signal_id": r.get("shared_ai_call_id") or None, "tile_filled": filled,
                            "tile_trade_id": tid, "epoch_id": r.get("epoch_id") or None,
                            "tile_entry_type": r.get("execution_entry_type") or r.get("entry_type") or None,
                            "tile_recorded_pnl_bp": _f(r.get("pnl")) if filled else None,
                            "tile_limit_price": _f(r.get("limit_price")) if not filled else _f(r.get("entry"))})
    ledger(d / "trades_3factor.csv", "trade", True)
    ledger(d / "expired_orders_3factor.csv", "expired", False)
    out = [r for r in out if r.get("signal_ts") is not None and not math.isnan(float(r["signal_ts"]))]
    out.sort(key=lambda r: (float(r["signal_ts"]), r["signal_id"]))
    return out


# ------------------------------------------------------------------ table

def session_of(ts: float) -> str:
    hour = datetime.fromtimestamp(ts, tz=timezone.utc).hour
    for name, (a, b) in SESSION_HOURS_UTC.items():
        if a <= hour < b:
            return name
    return "US"


def _bp(a: float, b: float) -> float | None:
    if not (math.isfinite(a) and math.isfinite(b)) or b == 0:
        return None
    return round((a - b) / b * 1e4, 4)


def _r(v: Any, nd: int = 4) -> Any:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return v
    return round(f, nd) if math.isfinite(f) else None


def table_columns() -> list[str]:
    base = ["schema", "signal_id", "signal_kind", "signal_ts", "signal_utc", "utc_day", "utc_hour", "session",
            "direction", "score_led_side", "long_score", "short_score", "score_gap", "ai_raw_decision",
            "ai_raw_direction", "ai_committed", "ai_explicit_aligned", "explicit_abstain", "score_direction_mismatch",
            "score_tie", "counterfactual_direction", "xv_trigger_source", "research_lane", "parent_signal_id",
            "tile_trade_id", "tile_filled", "tile_entry_type", "tile_limit_price", "tile_recorded_pnl_bp", "epoch_id",
            "sources", "tape_status", "anchor_ts", "anchor_lag_sec", "anchor_bid", "anchor_ask", "anchor_mid",
            "anchor_last", "spread_bp", "l1_imbalance", "ret_prior_1m_bp", "ret_prior_15m_bp", "ret_prior_60m_bp",
            "range_prior_15m_bp", "path_complete_sec"]
    per_h = ["mid_{h}_bp", "bid_{h}", "ask_{h}", "mfe_up_{h}_bp", "mae_dn_{h}_bp", "long_taker_{h}_bp",
             "short_taker_{h}_bp", "dir_{h}_bp", "dir_mfe_{h}_bp", "dir_mae_{h}_bp"]
    return base + [c.format(h=h) for h, _ in HORIZONS for c in per_h]


def build_row(sig: Mapping[str, Any], tape: Tape) -> dict[str, Any]:
    ts = float(sig["signal_ts"])
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    row: dict[str, Any] = {c: None for c in table_columns()}
    row.update({k: sig.get(k) for k in row if k in sig})
    row.update({"schema": TABLE_SCHEMA, "signal_ts": round(ts, 3), "signal_utc": dt.isoformat(),
                "utc_day": dt.strftime("%Y-%m-%d"), "utc_hour": dt.hour, "session": session_of(ts),
                "sources": "|".join(sorted(sig.get("sources") or ())),
                "direction": sig.get("direction") or "NONE"})
    for k in ("long_score", "short_score", "score_gap", "tile_limit_price", "tile_recorded_pnl_bp"):
        row[k] = _r(row.get(k))
    if not tape.covers(ts):
        row["tape_status"] = "OUTSIDE_TAPE"
        return row
    a = tape.first_valid_at_or_after(ts)
    if a is None:
        row["tape_status"] = "NO_FRESH_ANCHOR"
        return row
    bid0, ask0 = tape.bid[a], tape.ask[a]
    mid0 = (bid0 + ask0) / 2.0
    row.update({"tape_status": "OK", "anchor_ts": a + tape.t0, "anchor_lag_sec": round(a + tape.t0 - ts, 3),
                "anchor_bid": bid0, "anchor_ask": ask0, "anchor_mid": mid0, "anchor_last": _r(tape.last[a], 2),
                "spread_bp": _bp(ask0, bid0)})
    bq, aq = tape.bid_qty[a], tape.ask_qty[a]
    if math.isfinite(bq) and math.isfinite(aq) and bq + aq > 0:
        row["l1_imbalance"] = round((bq - aq) / (bq + aq), 4)
    for name, lb in (("ret_prior_1m_bp", 60), ("ret_prior_15m_bp", 900), ("ret_prior_60m_bp", 3600)):
        j = a - lb
        if j >= 0 and tape.quote_age[j] <= PATH_MAX_QUOTE_AGE_SEC:
            row[name] = _bp(mid0, tape.mid_ff[j])
    j = a - 900
    if j >= 0:
        seg = tape.mid_ff[j:a + 1]
        seg = seg[np.isfinite(seg)]
        if len(seg) > 60:
            row["range_prior_15m_bp"] = round((seg.max() - seg.min()) / mid0 * 1e4, 4)
    sign = {"LONG": 1.0, "SHORT": -1.0}.get(row["direction"])
    n = len(tape.mid_ff)
    end = min(a + MAX_HORIZON_SEC, n - 1)
    mids = tape.mid_ff[a:end + 1]
    ages = tape.quote_age[a:end + 1]
    ok = ages <= PATH_MAX_QUOTE_AGE_SEC
    mids_ok = np.where(ok, mids, np.nan)
    with np.errstate(invalid="ignore"):
        run_max = np.fmax.accumulate(mids_ok)
        run_min = np.fmin.accumulate(mids_ok)
    complete = int(np.argmin(ok)) - 1 if not ok.all() else len(ok) - 1
    row["path_complete_sec"] = complete
    for h, sec in HORIZONS:
        if sec >= len(mids) or not ok[sec]:
            continue
        i = a + sec
        mid_h, bid_h, ask_h = tape.mid_ff[i], tape.bid_ff[i], tape.ask_ff[i]
        up = _bp(run_max[sec], mid0)
        dn = _bp(run_min[sec], mid0)
        row[f"mid_{h}_bp"] = _bp(mid_h, mid0)
        row[f"bid_{h}"], row[f"ask_{h}"] = bid_h, ask_h
        row[f"mfe_up_{h}_bp"], row[f"mae_dn_{h}_bp"] = up, dn
        row[f"long_taker_{h}_bp"] = _bp(bid_h, ask0)
        row[f"short_taker_{h}_bp"] = None if _bp(ask_h, bid0) is None else -_bp(ask_h, bid0)
        if sign is not None and row[f"mid_{h}_bp"] is not None:
            row[f"dir_{h}_bp"] = round(sign * row[f"mid_{h}_bp"], 4)
            row[f"dir_mfe_{h}_bp"] = up if sign > 0 else (None if dn is None else -dn)
            row[f"dir_mae_{h}_bp"] = dn if sign > 0 else (None if up is None else -up)
    return row


def build_table(signals: Iterable[Mapping[str, Any]], tape: Tape) -> list[dict[str, Any]]:
    return [build_row(s, tape) for s in signals]


def _csv_value(v: Any) -> Any:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (float, np.floating)):
        return "" if not math.isfinite(float(v)) else repr(round(float(v), 6))
    return v


def write_table(rows: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    cols = table_columns()
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "wt", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({c: _csv_value(r.get(c)) for c in cols})


def read_table(path: Path | str) -> list[dict[str, Any]]:
    p = Path(path)
    opener = gzip.open if p.suffix == ".gz" else open
    with opener(p, "rt", newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def manifest(rows: list[dict[str, Any]], tape: Tape, data_dir: Path, table_path: Path) -> dict[str, Any]:
    kinds: dict[str, dict[str, int]] = {}
    for r in rows:
        k = kinds.setdefault(r["signal_kind"], {"rows": 0, "tape_ok": 0})
        k["rows"] += 1
        k["tape_ok"] += int(r.get("tape_status") == "OK")
    return {
        "schema": MANIFEST_SCHEMA, "table_schema": TABLE_SCHEMA, "generated_utc": datetime.now(timezone.utc).isoformat(),
        "data_dir": str(data_dir), "table_path": str(table_path), "rows": len(rows), "by_kind": kinds,
        "horizons": [{"label": h, "sec": s} for h, s in HORIZONS],
        "tape": {"schema": TAPE_SCHEMA, "files": [p.name for p in tape_files(data_dir)], "rows": tape.n_rows,
                 "first_utc": datetime.fromtimestamp(tape.t0, tz=timezone.utc).isoformat(),
                 "last_utc": datetime.fromtimestamp(tape.t1, tz=timezone.utc).isoformat(),
                 "valid_quote_share": round(float(tape.valid.sum()) / len(tape.valid), 4),
                 "fingerprint": tape.fingerprint()},
        "path_max_quote_age_sec": PATH_MAX_QUOTE_AGE_SEC, "anchor_max_wait_sec": ANCHOR_MAX_WAIT_SEC,
        "ai_commit_min_score_gap": AI_COMMIT_MIN_SCORE_GAP,
        "fill_model": fm.fill_model_declaration(),
        "paper_only": True, "runtime_hot_path": False,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--data-dir", required=True, type=Path, help="Fly runtime/ copy (tape + signal ledgers)")
    ap.add_argument("--tape-dir", type=Path, help="Directory holding market_microstructure_1s.jsonl* "
                                                  "(defaults to --data-dir)")
    ap.add_argument("--out-dir", required=True, type=Path)
    ap.add_argument("--since-utc", help="Drop signals before this ISO time (e.g. the clean-epoch start)")
    args = ap.parse_args(argv)
    tape_dir = args.tape_dir or args.data_dir
    tape = load_tape(tape_dir)
    signals = load_signals(args.data_dir)
    if args.since_utc:
        cut = _iso_ts(args.since_utc) or 0.0
        signals = [s for s in signals if float(s["signal_ts"]) >= cut]
    rows = build_table(signals, tape)
    out = args.out_dir / "per_signal_research_table.csv.gz"
    write_table(rows, out)
    man = manifest(rows, tape, tape_dir, out)
    (args.out_dir / "per_signal_research_table.manifest.json").write_text(json.dumps(man, indent=2, default=str))
    print(json.dumps({"rows": man["rows"], "by_kind": man["by_kind"], "tape": man["tape"]}, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
