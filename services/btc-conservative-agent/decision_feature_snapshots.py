"""Versioned per-decision feature snapshots with forward labels (training data for future models).

One SNAPSHOT row per shared AI call carries every causal input the bot had at the
decision (tape, leaders, cross-venue premium/lead, compact facts, the AI call and
its commit flags, the regime shadow answer, tile toggles). LABEL rows mature from
the 1s Bitfinex tape ring at 1/5/15/30/60/120 minutes: mid return, the up/down
excursion of the mid path and path efficiency. Nothing here gates orders.

``FEATURE_SET_VERSION`` changes whenever a field's meaning changes; consumers
must split cohorts on it. Order-book depth beyond L1 is not collected (see
diagnostics/AI-INPUT-REVISION-RECEIPT-20261002.md for the 1 vCPU measurement).
"""
from __future__ import annotations

import threading
from typing import Any, Iterable, Mapping, Optional

from ai_shadow_challengers import MATURITY_GRACE_SEC, MAX_PRICE_LAG_SEC, MAX_TAPE_GAP_SEC, TAPE_RING_SECONDS
from ai_shadow_challengers import _finite, _index_at_or_before, max_gap, quote_at

SNAPSHOT_SCHEMA = "decision_feature_snapshot_v1"
LABEL_SCHEMA = "decision_forward_label_v1"
SNAPSHOT_FILE = "decision_feature_snapshots.jsonl"
FEATURE_SET_VERSION = "dfs_v1_20261002"
LABEL_HORIZONS_MIN = (1, 5, 15, 30, 60, 120)
BOOK_DEPTH_COLLECTED = False


def _leader_compact(leader: Optional[Mapping[str, Any]]) -> dict:
    leader = leader if isinstance(leader, Mapping) else {}
    return {
        "leader_venue": leader.get("leader_venue"),
        "leader_ret_bp": leader.get("leader_ret_bp"),
        "side": leader.get("side"),
        "reason": leader.get("reason"),
        "bfx": dict(leader.get("bfx") or {}),
        "venues": {k: dict(v) for k, v in (leader.get("venues") or {}).items() if isinstance(v, Mapping)},
    }


def build_snapshot(*, call_id: str, decision_ts: float, decision_price: Optional[float],
                   ai_result: Mapping[str, Any], tape: Mapping[str, Any],
                   leader: Optional[Mapping[str, Any]], premium: Optional[Mapping[str, Any]],
                   compact_facts: Optional[Mapping[str, Any]], regime: Optional[Mapping[str, Any]],
                   tile_toggles: Mapping[str, Any], meta: Mapping[str, Any]) -> dict:
    ai = {
        key: ai_result.get(key)
        for key in ("decision", "direction", "raw_direction", "long_score", "short_score",
                    "score_gap", "ai_committed", "explicit_abstain", "score_direction_mismatch",
                    "score_tie", "commit_rule", "prompt_id", "prompt_input_revision",
                    "deepseek_model", "deepseek_served_model", "ai_error")
    }
    return {
        "schema": SNAPSHOT_SCHEMA,
        "row_kind": "SNAPSHOT",
        "feature_set_version": FEATURE_SET_VERSION,
        "shared_ai_call_id": call_id,
        "decision_ts": decision_ts,
        "decision_price": decision_price,
        "book_depth_collected": BOOK_DEPTH_COLLECTED,
        **{k: meta.get(k) for k in ("epoch_id", "git_rev", "decision_utc")},
        "ai": ai,
        "tape": dict(tape or {}),
        "leader": _leader_compact(leader),
        "premium": dict(premium or {}),
        "compact_facts": dict(compact_facts or {}),
        "regime": dict(regime or {}),
        "tile_toggles": dict(tile_toggles or {}),
        "gates_orders": False,
    }


def label_row(call_id: str, item: Mapping[str, Any], minutes: int, ts_list, rows) -> dict:
    t0 = item["decision_ts"]
    th = t0 + minutes * 60
    row = {
        "schema": LABEL_SCHEMA,
        "row_kind": "LABEL",
        "feature_set_version": FEATURE_SET_VERSION,
        "shared_ai_call_id": call_id,
        "decision_ts": t0,
        "horizon_min": minutes,
        "tape_ok": False,
        "maturity": "TAPE_GAP",
        "fwd_ret_bp": None,
        "max_up_bp": None,
        "max_down_bp": None,
        "path_efficiency": None,
        "max_gap_s": None,
    }
    q0, qh = quote_at(ts_list, rows, t0), quote_at(ts_list, rows, th)
    gap = max_gap(ts_list, t0, th)
    row["max_gap_s"] = gap
    if q0 is None or qh is None or gap is None or gap > max(MAX_PRICE_LAG_SEC, min(MAX_TAPE_GAP_SEC, minutes * 60)):
        return row
    m0 = (q0[0] + q0[1]) / 2.0
    lo, hi = _index_at_or_before(ts_list, t0), _index_at_or_before(ts_list, th)
    mids = [(rows[i][0] + rows[i][1]) / 2.0 for i in range(max(lo, 0), hi + 1)]
    mh = (qh[0] + qh[1]) / 2.0
    travel = sum(abs(b - a) for a, b in zip(mids[::10], mids[10::10] + [mh]))
    row.update({
        "tape_ok": True,
        "maturity": "MATURED",
        "fwd_ret_bp": round((mh / m0 - 1.0) * 1e4, 4),
        "max_up_bp": round((max(mids) / m0 - 1.0) * 1e4, 4),
        "max_down_bp": round((min(mids) / m0 - 1.0) * 1e4, 4),
        "path_efficiency": None if travel <= 0 else round(abs(mh - m0) / travel, 4),
    })
    return row


class LabelBook:
    """Snapshots awaiting forward labels from the tape ring."""

    def __init__(self, max_pending: int = 128) -> None:
        self._lock = threading.Lock()
        self._pending: dict = {}
        self._max_pending = int(max_pending)
        self.stats = {"registered": 0, "labels_written": 0, "evicted": 0}

    def register(self, snapshot: Mapping[str, Any], done: Iterable[int] = ()) -> bool:
        call_id = str(snapshot.get("shared_ai_call_id") or "")
        decision_ts = _finite(snapshot.get("decision_ts"))
        if not call_id or decision_ts is None:
            return False
        with self._lock:
            if call_id in self._pending:
                return False
            if len(self._pending) >= self._max_pending:
                oldest = min(self._pending, key=lambda k: self._pending[k]["decision_ts"])
                self._pending.pop(oldest, None)
                self.stats["evicted"] += 1
            self._pending[call_id] = {"decision_ts": decision_ts, "done": set(done)}
            self.stats["registered"] += 1
        return True

    def pending_count(self) -> int:
        with self._lock:
            return len(self._pending)

    def mature(self, ring, now: float) -> list:
        ts_list, rows = ring.snapshot()
        latest = ts_list[-1] if ts_list else None
        out = []
        with self._lock:
            items = list(self._pending.items())
        for call_id, item in items:
            for minutes in LABEL_HORIZONS_MIN:
                if minutes in item["done"]:
                    continue
                th = item["decision_ts"] + minutes * 60
                if not (latest is not None and latest >= th) and now < th + MATURITY_GRACE_SEC:
                    continue
                out.append(label_row(call_id, item, minutes, ts_list, rows))
                item["done"].add(minutes)
            if len(item["done"]) == len(LABEL_HORIZONS_MIN):
                with self._lock:
                    self._pending.pop(call_id, None)
        with self._lock:
            self.stats["labels_written"] += len(out)
        return out


def pending_from_rows(rows: Iterable[Mapping[str, Any]], now: float) -> list:
    """(snapshot, done_horizons) pairs still maturing after a restart."""
    snaps, done = {}, {}
    for row in rows:
        call_id = str(row.get("shared_ai_call_id") or "")
        if not call_id:
            continue
        if row.get("row_kind") == "SNAPSHOT":
            snaps[call_id] = row
        elif row.get("row_kind") == "LABEL":
            done.setdefault(call_id, set()).add(row.get("horizon_min"))
    out = []
    for call_id, snap in snaps.items():
        ts = _finite(snap.get("decision_ts"))
        horizons = done.get(call_id, set())
        if ts is None or now - ts > TAPE_RING_SECONDS or len(horizons) >= len(LABEL_HORIZONS_MIN):
            continue
        out.append((snap, horizons))
    return out
