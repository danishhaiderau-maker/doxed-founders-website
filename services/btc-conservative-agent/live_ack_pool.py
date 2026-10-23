"""RK ruling A (24 Oct 2026 00:36 AEDT): POOLED_LIVE_ACK delay for paper-only lanes - the ONE shared helper (R4).

It extends the B1/F1 live-ack pool (paper_fill.DelaySampler: dedupe per trade, capped, persisted per DATA epoch,
cleared at reset). No per-tile code: a paper book lane with no live acks of its own uses this pool; which key a row
uses comes from its order model (order_model.entry_model / exit_order_class), never from the tile.

  total delay = the row's OWN measured bot-side signal->Fly intent (per row, never pooled) + pooled Fly intent->ack
              (HM 01:43 blocker 1, pool_def fly_intent_to_ack_v2): one END-TO-END pooled leg that starts exactly where
              the row's own leg ends, so nothing is counted twice and no leg is omitted.
  sample    : entry = exchange_ack_at_ts - fly_intent_emitted_at_ts (live-copy-fly-reporter.ts, approval created on
              Fly), NOT order_sent_at_ts (that skipped Fly->Railway relay + Railway queueing). exit = exit_ack_at_ts -
              the Fly exit intent (the bot's own relay-outbox enqueue of POSITION_CLOSED, stamped with the actual POST
              start + RTT on acknowledgement). No Fly stamp -> FLY_SEND_TS_MISSING, never sampled: EXIT_RO_MKT fails
              closed until exit stamps exist. Clock check per sample: the Railway receive must fall inside
              [Fly intent, ack] (entry) / [Fly POST start, POST start + RTT] (exit), else CLOCK_SKEW (excluded).
  keys      : ENTRY_MKT_GTC (marketable GTC entry), MAKER_REST (resting entry: send -> ack = first possible fill),
              EXIT_RO_MKT (reduce-only market exit). Market and limit are separate keys. The exchange-resident
              stop has no delay and is never pooled.
  samples   : current-epoch REAL live acks only, same account + relay path (report schema). Normal live orders
              only: forced / admin / deploy-flatten / kill-switch / reconcile / manual / exchange-stop closes are
              excluded, and so is any ack whose send->ack window is flagged by the #847 gap rule
              (r17_gap_detector: book hole / book missing now, boot / boot-log-missing once the boot log can
              show it). Without r17_gap_detector (before #847 merges) nothing is admitted: fail closed.
  rule      : n < 10 per key -> fail closed (label carries "default" -> UNVERIFIED -> non-countable);
              10 <= n < 20 -> MAX; n >= 20 -> q0.90 (nearest rank), as B1.
  stamps    : delay_source "POOLED_LIVE_ACK_CROSS_LANE:<KEY>_<max|q90>_n<N>" + delay_source_kind, pool_key,
              pool_n, pool_ts, quantile_method, pool_def, delay_span, own_signal_to_send_s / sig_to_send_ms,
              pooled_send_to_ack_s / pool_send_to_ack_ms.

No look-ahead: the collector stamps a MEASURED receive_ts per book row (#851); paper_fill.book_receive_proof makes a
row without it non-countable (r17 BOOK_RECEIVE_TS_MISSING).
"""
from __future__ import annotations

import os
import re
import threading
import time
from typing import Any, Callable, Mapping, Optional

import order_model as _om
import paper_fill as _pf

DELAY_SOURCE = "POOLED_LIVE_ACK_CROSS_LANE"
KEY_ENTRY_MKT = "ENTRY_MKT_GTC"
KEY_MAKER_REST = "MAKER_REST"
KEY_EXIT_RO_MKT = "EXIT_RO_MKT"
KEYS = (KEY_ENTRY_MKT, KEY_MAKER_REST, KEY_EXIT_RO_MKT)
LABEL_RE = re.compile(rf"^{DELAY_SOURCE}:({'|'.join(KEYS)})_(max|q90|default\w*)_n(\d+)$")
MEASURED_LABEL_PATTERN = rf"{DELAY_SOURCE}:(?:{'|'.join(KEYS)})_(?:max|q90)_n(\d+)"
EXCLUDED_REASON = re.compile(
    r"FORCE|ADMIN|DEPLOY|KILL|RECONCIL|MAINTENANCE|MANUAL|ALREADY_FLAT|EXCHANGE_STOP|USER_RELAY_STOP|CIRCUIT_BREAKER"
    r"|EMERGENCY|FLATTEN|SHOWCASE_MIRROR", re.I)
ENTRY_BOOK_MAX_AGE_S = 2.0       # a send with no book row this fresh cannot be classified -> not pooled
BOOT_ADMIT_AFTER_S = 60.0 + 120.0 + 5.0  # r17_gap_detector BOOT_POST_S + BOOT_LOG_LAG_S (+ margin)
PENDING_CAP = 400
POOL_KEY = "cross_lane_pool"     # sub-document of paper_delay_pool.json
BOOT_LOG_FILE = "runtime_telemetry_1m.jsonl"
POOL_DEF = "fly_intent_to_ack_v2"  # HM 01:43 blocker 1: a persisted pool with another definition is dropped on load
DELAY_SPAN = "OWN_SIGNAL_TO_FLY_INTENT+POOLED_FLY_INTENT_TO_ACK"
CLOCK_SLACK_S = 0.05  # Railway receive may sit this far outside the Fly window (ms rounding of the stamps)
FLY_SEND_TS_MISSING = "FLY_SEND_TS_MISSING"
RAILWAY_RECEIVE_TS_MISSING = "RAILWAY_RECEIVE_TS_MISSING"
CLOCK_SKEW = "CLOCK_SKEW"


def entry_window(tl: Mapping[str, Any]) -> tuple:
    """(start, end, flag) of the live ENTRY sample: Fly intent emitted -> exchange ack; Railway receive inside it."""
    a, b = _pf._f(tl.get("fly_intent_emitted_at_ts")), _pf._f(tl.get("exchange_ack_at_ts"))
    if not b:
        return None, None, None
    if not a:
        return None, None, FLY_SEND_TS_MISSING
    rr = _pf._f(tl.get("railway_received_at_ts"))
    if not rr:
        return a, b, RAILWAY_RECEIVE_TS_MISSING
    if not (a - CLOCK_SLACK_S <= rr <= b + CLOCK_SLACK_S):
        return a, b, CLOCK_SKEW
    return a, b, None


def exit_window(tl: Mapping[str, Any], fly_send: Optional[Mapping[str, Any]]) -> tuple:
    """(start, end, flag) of the live EXIT sample: the bot's own Fly exit intent (relay-outbox enqueue of
    POSITION_CLOSED) -> exit ack. Railway's durable receive must fall inside [POST start, POST start + RTT]."""
    b = _pf._f(tl.get("exit_ack_at_ts"))
    if not b:
        return None, None, None
    fs = fly_send if isinstance(fly_send, Mapping) else {}
    a, post, rtt = _pf._f(fs.get("fly_intent_at_ts")), _pf._f(fs.get("fly_send_started_at_ts")), _pf._f(fs.get("fly_send_rtt_s"))
    if not a or not post or rtt is None:
        return None, None, FLY_SEND_TS_MISSING
    rr = _pf._f(fs.get("railway_received_at_ts"))
    if not rr:
        return a, b, RAILWAY_RECEIVE_TS_MISSING
    if not (post - CLOCK_SLACK_S <= rr <= post + rtt + CLOCK_SLACK_S):
        return a, b, CLOCK_SKEW
    return a, b, None


def key_for(leg: str, *, entry_order_model: Any = None, exit_order_class: Any = None) -> Optional[str]:
    if leg == "exit":
        return None if exit_order_class == _om.EXCHANGE_RESIDENT else KEY_EXIT_RO_MKT
    if leg == "entry":
        return KEY_MAKER_REST if entry_order_model == _om.ENTRY_MAKER_REST else KEY_ENTRY_MKT
    return None


def is_pooled(label: Any) -> bool:
    return str(label or "").startswith(DELAY_SOURCE + ":")


def excluded_reason(report: Mapping[str, Any]) -> Optional[str]:
    """The first non-normal reason on a live report (forced / admin / kill-switch / reconcile ...), else None."""
    vals = [report.get("origin"), report.get("intent_source"), report.get("exit_reason")]
    for k in ("close", "decision", "order"):
        sub = report.get(k)
        if isinstance(sub, Mapping):
            vals += [sub.get("reason"), sub.get("origin"), sub.get("source")]
    for v in vals:
        if v and EXCLUDED_REASON.search(str(v)):
            return str(v)
    return None


def classify_entry(report: Mapping[str, Any], sent: float, book_at: Callable[[float], Any]) -> Optional[str]:
    """Marketable GTC vs resting: the live limit vs the book row at send (shared book source). None = unknown."""
    o = report.get("order") if isinstance(report.get("order"), Mapping) else {}
    px, side = _pf._f(o.get("price")), str(o.get("side") or "").upper()
    if str(o.get("type") or "LIMIT").upper() == "MARKET":
        return KEY_ENTRY_MKT
    if not px or side not in ("LONG", "SHORT", "BUY", "SELL"):
        return None
    book = book_at(sent)
    s = _pf._f(book.get("s")) if isinstance(book, Mapping) else None
    if s is None or sent - (s + 1.0) > ENTRY_BOOK_MAX_AGE_S:
        return None
    try:
        bid, ask = float(book["b"][0][0]), float(book["a"][0][0])
    except (KeyError, IndexError, TypeError, ValueError):
        return None
    buy = side in ("LONG", "BUY")
    return KEY_ENTRY_MKT if (px >= ask if buy else px <= bid) else KEY_MAKER_REST


def _gap_detector():
    try:
        import r17_gap_detector as gd  # #847; absent until it merges -> fail closed
        return gd
    except ImportError:
        return None


def book_gap_flag(a: float, b: float, missing_seconds: Callable[[float, float], Any]) -> Optional[str]:
    gd = _gap_detector()
    if gd is None:
        return "GAP_RULE_UNAVAILABLE"
    try:
        miss = missing_seconds(a - 1.0, b + 1.0)
        if miss is None:
            return "C2:BOOK_MISSING"
        return "C2:BOOK_HOLE" if gd.missing_seconds_are_a_hole(miss) else None
    except Exception:  # noqa: BLE001
        return "GAP_CHECK_ERROR"


def boot_flag(a: float, b: float, boot_log_path: str) -> Optional[str]:
    gd = _gap_detector()
    if gd is None:
        return "GAP_RULE_UNAVAILABLE"
    try:
        log = gd.BootLog.load([boot_log_path])
        if not log.readable(a, b):
            return "C2:BOOT_LOG_MISSING"
        return "C2:BOOT" if log.boot_in(a, b) else None
    except Exception:  # noqa: BLE001
        return "GAP_CHECK_ERROR"


class CrossLanePool(_pf.DelaySampler):
    """B1's DelaySampler keyed (pool key, account@relay) + a pending queue for the deferred boot check."""

    def __init__(self, maxlen: int = 200) -> None:
        super().__init__(maxlen=maxlen)
        self._pending: list = []
        self.excluded: dict = {}

    @staticmethod
    def ident(report: Mapping[str, Any]) -> str:
        return f"{report.get('account') or 'acct-unknown'}@{report.get('schema') or 'relay-unknown'}"

    def _exclude(self, why: str) -> None:
        self.excluded[why] = int(self.excluded.get(why) or 0) + 1

    def observe(self, report: Mapping[str, Any], *, book_at: Callable[[float], Any],
                missing_seconds: Callable[[float, float], Any],
                fly_exit_send: Optional[Callable[[str], Any]] = None) -> list:
        """Queue the Fly intent->ack samples of one VERIFIED live report (never paper). Returns the queued keys.
        ``fly_exit_send(trade_id)`` returns the bot's own Fly exit-send record (None -> FLY_SEND_TS_MISSING)."""
        tl = report.get("timeline") if isinstance(report.get("timeline"), Mapping) else {}
        cid = str(report.get("correlation_id") or report.get("trade_id") or "")
        rtype = str(report.get("type") or "")
        if not cid:
            return []
        why = excluded_reason(report)
        legs = []
        if rtype in ("ORDER_PLACED", "ORDER_SENT"):
            legs.append(("entry",) + entry_window(tl))
        if rtype == "POSITION_CLOSED" and _pf._f(tl.get("exit_ack_at_ts")):
            rec = None
            if callable(fly_exit_send):
                try:
                    rec = fly_exit_send(str(report.get("trade_id") or cid)) or (
                        fly_exit_send(cid) if report.get("trade_id") and str(report.get("trade_id")) != cid else None)
                except Exception:  # noqa: BLE001 - no record -> fail closed
                    rec = None
            legs.append(("exit",) + exit_window(tl, rec))
        out = []
        for leg, a, b, clock in legs:
            if why:
                self._exclude(f"REASON:{why}")
                continue
            if clock:
                self._exclude(clock)
                continue
            if not (a and b):
                continue
            # the order class is read at the live order's send (marketable vs resting); the sample starts at Fly
            key = (classify_entry(report, _pf._f(tl.get("order_sent_at_ts")) or a, book_at) if leg == "entry"
                   else KEY_EXIT_RO_MKT)
            if key is None:
                self._exclude("ENTRY_UNCLASSIFIED")
                continue
            if not (0 <= b - a <= 30) or self.has_trade(key, self.ident(report), cid):
                continue
            flag = book_gap_flag(a, b, missing_seconds)
            if flag:
                self._exclude(flag)
                continue
            with self._lock:
                if len(self._pending) >= PENDING_CAP:
                    self._pending.pop(0)
                self._pending.append([key, self.ident(report), b - a, a, b, cid])
            out.append(key)
        return out

    def admit_pending(self, now: float, boot_log_path: str) -> int:
        """Admit queued samples once the boot log can show a boot in [send-5, ack+60] (#847 clause)."""
        with self._lock:
            due = [p for p in self._pending if now >= p[4] + BOOT_ADMIT_AFTER_S]
            self._pending = [p for p in self._pending if now < p[4] + BOOT_ADMIT_AFTER_S]
        n = 0
        for key, ident, v, a, b, cid in due:
            flag = boot_flag(a, b, boot_log_path)
            if flag:
                self._exclude(flag)
            elif self.add(key, ident, v, measured_ts=b, trade_id=cid) is not None:
                n += 1
        return n

    def _ident_for(self, key: str, ident: Optional[str]) -> Optional[str]:
        if ident:
            return ident
        with self._lock:
            ids = {k[1] for k, dq in self._d.items() if k[0] == key and dq}
        return next(iter(ids)) if len(ids) == 1 else None  # >1 account/relay and none configured: fail closed

    def pooled(self, key: str, ident: Optional[str] = None) -> dict:
        ident = self._ident_for(key, ident)
        d, src = self.delay(key, ident or "") if ident else (_pf.DEFAULT_DELAY_S, "default_until_n10")
        n = len(self._xs(key, ident)) if ident else 0
        method = "max" if "_max_" in src else ("q90" if "_q90_" in src else "default_until_n10")
        return {"pool_key": key, "pool_ident": ident, "pool_n": n, "pool_ts": self.pool_ts(key, ident) if ident else None,
                "pooled_send_to_ack_s": d if n >= _pf.MIN_SAMPLES else None,
                "label": f"{DELAY_SOURCE}:{key}_{method}_n{n}", "quantile_method": _pf.quantile_method_of(src),
                "pool_def": POOL_DEF}

    def total(self, key: str, own_signal_to_send_s: Any, ident: Optional[str] = None) -> tuple:
        """(delay_s, delay_source, meta). Fails closed (default delay, label contains 'default') when n < 10 or the
        row's own signal->send is not measured."""
        meta = self.pooled(key, ident)
        own = _pf._f(own_signal_to_send_s)
        meta["own_signal_to_send_s"] = own if own is not None and own >= 0 else None
        if meta["pooled_send_to_ack_s"] is None:
            return _pf.DEFAULT_DELAY_S, meta["label"], meta
        if meta["own_signal_to_send_s"] is None:
            label = f"{DELAY_SOURCE}:{key}_default_own_s2s_missing_n{meta['pool_n']}"
            return _pf.DEFAULT_DELAY_S, label, {**meta, "label": label}
        return round(meta["own_signal_to_send_s"] + meta["pooled_send_to_ack_s"], 6), meta["label"], meta

    def stamp(self, res: Any, own_signal_to_send_s: Any = None) -> Any:
        """Row stamps for a fill priced with a pooled label (delay_source stays the label)."""
        if not isinstance(res, dict) or not is_pooled(res.get("delay_source")):
            return res
        m = LABEL_RE.match(str(res.get("delay_source")))
        if not m:
            return res
        meta = self.pooled(m.group(1))
        n = int(m.group(3))
        if meta.get("pool_n") != n:  # the pool moved since pricing: the label (n) stays the provenance
            meta = {**meta, "pooled_send_to_ack_s": None}
        unverified = "default" in m.group(2) or n < _pf.MIN_SAMPLES
        res.update({"delay_source_kind": DELAY_SOURCE, "pool_key": m.group(1), "pool_n": n,
                    "pool_ts": meta.get("pool_ts"), "quantile_method": _pf.quantile_method_of(res["delay_source"]),
                    "pooled_send_to_ack_s": None if unverified else meta.get("pooled_send_to_ack_s"),
                    "own_signal_to_send_s": _pf._f(own_signal_to_send_s) if own_signal_to_send_s is not None else (
                        None if unverified or meta.get("pooled_send_to_ack_s") is None or _pf._f(res.get("delay_s")) is None
                        else round(_pf._f(res.get("delay_s")) - meta["pooled_send_to_ack_s"], 6)),
                    "delay_unverified": unverified, "delay_flag": _pf.UNVERIFIED_DELAY if unverified else None,
                    "pool_def": POOL_DEF, "delay_span": DELAY_SPAN})
        own, pooled = _pf._f(res.get("own_signal_to_send_s")), _pf._f(res.get("pooled_send_to_ack_s"))
        res["sig_to_send_ms"] = round(own * 1000) if own is not None else None
        res["pool_send_to_ack_ms"] = round(pooled * 1000) if pooled is not None else None
        return res

    def clear(self) -> None:
        super().clear()
        with self._lock:
            self._pending = []
            self.excluded = {}

    def to_json(self, epoch_id: Any) -> dict:
        out = super().to_json(epoch_id)
        with self._lock:
            out["pending"] = [list(p) for p in self._pending[-PENDING_CAP:]]
            out["excluded"] = dict(self.excluded)
        out["pool_def"] = POOL_DEF
        return out

    def load(self, payload: Any, epoch_id: Any) -> bool:
        if not isinstance(payload, Mapping) or payload.get("pool_def") != POOL_DEF:
            return False  # HM 01:43 blocker 1: order_sent->ack samples (v1) never mix with Fly intent->ack (v2)
        if not super().load(payload, epoch_id):
            return False
        with self._lock:
            self._pending = [list(p) for p in (payload.get("pending") or []) if isinstance(p, list) and len(p) == 6]
            self.excluded = {str(k): int(v) for k, v in (payload.get("excluded") or {}).items()
                             if isinstance(v, (int, float))}
        return True


def boot_log_path() -> str:
    return os.path.join(os.getcwd(), BOOT_LOG_FILE)


def now() -> float:
    return time.time()
