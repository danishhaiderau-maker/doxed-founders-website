"""Tile 15 · DYN-AF stage A (shadow, no orders): frozen 15-minute regime classifier.

Spec: audit/TILE14-TILE15-DESIGN-20261008.md §3.3 (numbers frozen, no retuning),
DYN-AF per Strategist 9 Oct (AI-free; stand aside on missing data, max 1 open).
Input is the bot's own 1 s Bitfinex tape (market_microstructure_1s rows).

Every minute: RV15 = sqrt(sum of squared 10 s log returns over 900 s) in bp,
Drift15 = net log move over 900 s in bp. Coverage must be >= 72 of 90 returns and
the latest mid <= 5 s old, else UNKNOWN. Raw: VIOLENT if RV15 >= 20, else TREND
if |Drift15| >= 12.2, else QUIET. Hysteresis: a new regime takes over after 3
consecutive raw minutes AND >= 10 minutes in the current regime. UNKNOWN is
immediate; leaving UNKNOWN needs 3 consecutive valid labels. The engine never
places orders; the bot appends one row per minute to dyn_regime_minutes.jsonl.
"""
from __future__ import annotations

import math
import threading

SCHEMA = "dyn_regime_minute_v1"
FILE_NAME = "dyn_regime_minutes.jsonl"
CLASSIFIER_ID = "DYN_AF_RV15_DRIFT15_V1"
STEP_SEC = 10
WINDOW_SEC = 900
N_RETURNS = WINDOW_SEC // STEP_SEC  # 90
MIN_RETURNS = 72
MAX_MID_AGE_SEC = 5.0
VIOLENT_RV_BP = 20.0
TREND_DRIFT_BP = 12.2
SWITCH_STREAK = 3
MIN_DWELL_MIN = 10
UNKNOWN = "UNKNOWN"
# DYN-AF cell map (shadow): which leg would trade in each committed regime.
CELL_LEG = {"QUIET": "TREND_SCORE_FADE_HA_EXITS", "TREND": "GS01", "VIOLENT": "STAND_ASIDE",
            UNKNOWN: "STAND_ASIDE"}


def classify_raw(rv15_bp, drift15_bp) -> str:
    if rv15_bp is None or drift15_bp is None:
        return UNKNOWN
    if rv15_bp >= VIOLENT_RV_BP:
        return "VIOLENT"
    if abs(drift15_bp) >= TREND_DRIFT_BP:
        return "TREND"
    return "QUIET"


class DynRegimeEngine:
    def __init__(self):
        self._lock = threading.Lock()
        self._slots: dict[int, float] = {}   # 10 s slot index -> last fresh mid in slot
        self._last_mid_ts = None
        self._last_minute = None
        self.committed = UNKNOWN
        self.committed_since_min = None
        self._streak_label = None
        self._streak = 0
        self.last_row = None

    def observe_row(self, row: dict):
        """Feed one 1 s tape row; returns a minute row when a minute closes."""
        ts = int(float(row.get("bucket_ts") or 0))
        if ts <= 0:
            return None
        with self._lock:
            out = None
            minute = ts // 60
            if self._last_minute is not None and minute > self._last_minute:
                out = self._close_minute(minute * 60)
            self._last_minute = minute
            bid, ask = row.get("bid"), row.get("ask")
            if row.get("fresh") and row.get("valid_bbo") and bid and ask:
                mid = (float(bid) + float(ask)) / 2.0
                if mid > 0:
                    self._slots[ts // STEP_SEC] = mid
                    self._last_mid_ts = ts + 1.0
            floor = ts // STEP_SEC - N_RETURNS - 6
            for key in [k for k in self._slots if k < floor]:
                del self._slots[key]
            return out

    def _measure(self, at_ts: int):
        end = at_ts // STEP_SEC - 1          # last complete slot before the minute
        rets, n = [], 0
        for k in range(end - N_RETURNS + 1, end + 1):
            a, b = self._slots.get(k - 1), self._slots.get(k)
            if a and b:
                rets.append(math.log(b / a)); n += 1
        first, last = self._slots.get(end - N_RETURNS), self._slots.get(end)
        age = None if self._last_mid_ts is None else at_ts - self._last_mid_ts
        ok = n >= MIN_RETURNS and age is not None and age <= MAX_MID_AGE_SEC
        rv = math.sqrt(sum(r * r for r in rets)) * 1e4 if ok else None
        if ok and first and last:
            drift = math.log(last / first) * 1e4
        elif ok:
            drift = sum(rets) * 1e4
        else:
            drift = None
        return rv, drift, n, age

    def _close_minute(self, at_ts: int) -> dict:
        rv, drift, n, age = self._measure(at_ts)
        raw = classify_raw(rv, drift)
        minute = at_ts // 60
        prev = self.committed
        if raw == self._streak_label:
            self._streak += 1
        else:
            self._streak_label, self._streak = raw, 1
        if raw == UNKNOWN:
            if self.committed != UNKNOWN:
                self.committed, self.committed_since_min = UNKNOWN, minute
        elif self.committed == UNKNOWN:
            if self._streak >= SWITCH_STREAK:
                self.committed, self.committed_since_min = raw, minute
        elif raw != self.committed:
            dwell = minute - (self.committed_since_min or minute)
            if self._streak >= SWITCH_STREAK and dwell >= MIN_DWELL_MIN:
                self.committed, self.committed_since_min = raw, minute
        row = {"schema": SCHEMA, "classifier": CLASSIFIER_ID, "minute_ts": at_ts,
               "rv15_bp": None if rv is None else round(rv, 3),
               "drift15_bp": None if drift is None else round(drift, 3),
               "returns": n, "mid_age_sec": None if age is None else round(age, 3),
               "raw": raw, "committed": self.committed, "switched": self.committed != prev,
               "committed_since_ts": None if self.committed_since_min is None else self.committed_since_min * 60,
               "leg": CELL_LEG[self.committed], "orders": "NONE_SHADOW_STAGE_A"}
        self.last_row = row
        return row

    def snapshot(self) -> dict:
        with self._lock:
            return {"classifier": CLASSIFIER_ID, "committed": self.committed,
                    "committed_since_ts": None if self.committed_since_min is None else self.committed_since_min * 60,
                    "last": dict(self.last_row) if self.last_row else None, "file": FILE_NAME,
                    "stage": "A_SHADOW_NO_ORDERS"}


ENGINE = DynRegimeEngine()
