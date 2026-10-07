"""Append-only, signed, hash-chained audit log for real Bitfinex actions.

Every real exchange action — order placed / changed / cancelled / filled,
stop-loss, take-profit, rejects, balance snapshots, and their timing — is
recorded as one immutable row with:

* a **monotonic sequence** (``seq``);
* a **hash chain** (each row hashes the previous row's hash plus its own
  canonical payload), so any deletion or reordering breaks the chain;
* an **HMAC-SHA256 signature** over the canonical payload, keyed by a secret
  that is supplied by the caller and **never persisted or logged**.

The module is pure and importable without ``bot.py`` or network access. The
log file is append-only; there is no update/delete path. Verification returns
the exact sequence and type of the first breakage, so a corrupted log fails
closed instead of silently trusting tampered data.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

SCHEMA = "bitfinex_order_action_audit_v1"
GENESIS_HASH = "0" * 64

# Stable action-type vocabulary.
ACTION_ORDER_PLACED = "ORDER_PLACED"
ACTION_ORDER_CHANGED = "ORDER_CHANGED"
ACTION_ORDER_CANCELLED = "ORDER_CANCELLED"
ACTION_ORDER_FILLED = "ORDER_FILLED"
ACTION_STOP_LOSS = "STOP_LOSS"
ACTION_TAKE_PROFIT = "TAKE_PROFIT"
ACTION_ORDER_REJECTED = "ORDER_REJECTED"
ACTION_BALANCE_SNAPSHOT = "BALANCE_SNAPSHOT"
ACTION_RECONCILE = "RECONCILE"
ACTION_TYPES = frozenset({
    ACTION_ORDER_PLACED, ACTION_ORDER_CHANGED, ACTION_ORDER_CANCELLED,
    ACTION_ORDER_FILLED, ACTION_STOP_LOSS, ACTION_TAKE_PROFIT,
    ACTION_ORDER_REJECTED, ACTION_BALANCE_SNAPSHOT, ACTION_RECONCILE,
})


def _canonical(row: dict) -> bytes:
    """Deterministic bytes over the signable fields (excluding seq/hash/sig)."""
    material = {
        "schema": row.get("schema"),
        "action_type": row.get("action_type"),
        "trade_id": row.get("trade_id"),
        "intent_id": row.get("intent_id"),
        "order_id": row.get("order_id"),
        "client_order_id": row.get("client_order_id"),
        "lane": row.get("lane"),
        "side": row.get("side"),
        "qty": row.get("qty"),
        "price": row.get("price"),
        "ts": row.get("ts"),
        "detail": row.get("detail"),
    }
    return json.dumps(material, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def _sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


class OrderActionAudit:
    def __init__(self, path: Path | str, key: bytes | None = None,
                 clock: Callable[[], float] = time.time):
        self.path = Path(path)
        self._key = key
        self._clock = clock
        self._lock = threading.Lock()
        self._rows: list[dict] = []
        self._load()

    def _load(self) -> None:
        try:
            if not self.path.exists():
                return
            with self.path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    try:
                        row = json.loads(line)
                    except (ValueError, json.JSONDecodeError):
                        continue
                    if isinstance(row, dict) and row.get("seq") is not None:
                        self._rows.append(row)
        except OSError:
            self._rows = []

    def _sign(self, payload: bytes) -> str:
        if not self._key:
            return ""
        return hmac.new(self._key, payload, hashlib.sha256).hexdigest()

    def _next_seq(self) -> int:
        return (self._rows[-1].get("seq") or 0) + 1 if self._rows else 1

    def _prev_hash(self) -> str:
        return self._rows[-1].get("hash") if self._rows else GENESIS_HASH

    def record(self, *, action_type: str, trade_id: str | None = None,
               intent_id: str | None = None, order_id: str | None = None,
               client_order_id: str | None = None, lane: str | None = None,
               side: str | None = None, qty: Any = None, price: Any = None,
               detail: dict | None = None, ts: float | None = None) -> dict:
        """Append one signed, chained row and return it. Never mutates history."""
        if action_type not in ACTION_TYPES:
            raise ValueError(f"unknown action_type: {action_type!r}")
        ts = self._clock() if ts is None else float(ts)
        prev_hash = self._prev_hash()
        body = {
            "schema": SCHEMA,
            "action_type": action_type,
            "trade_id": trade_id,
            "intent_id": intent_id,
            "order_id": order_id,
            "client_order_id": client_order_id,
            "lane": (str(lane).upper() if lane else None),
            "side": side,
            "qty": qty,
            "price": price,
            "ts": round(ts, 6),
            "detail": detail or {},
        }
        canonical = _canonical(body)
        row = {
            **body,
            "seq": self._next_seq(),
            "prev_hash": prev_hash,
            "hash": _sha256(prev_hash.encode("utf-8") + canonical),
            "sig": self._sign(canonical),
        }
        line = json.dumps(row, sort_keys=True, separators=(",", ":"), default=str)
        with self._lock:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as handle:
                    handle.write(line + "\n")
                    handle.flush()
                    os.fsync(handle.fileno())
            except OSError as exc:
                raise OSError(f"audit append failed: {exc}") from exc
            self._rows.append(row)
        return dict(row)

    def query(self, *, action_type: str | None = None, trade_id: str | None = None,
              intent_id: str | None = None, lane: str | None = None,
              since_seq: int | None = None, since_ts: float | None = None,
              limit: int = 200) -> list[dict]:
        """Read rows (newest first) filtered by the supplied predicates."""
        rows = list(self._rows)
        out: list[dict] = []
        for row in reversed(rows):
            if action_type is not None and row.get("action_type") != action_type:
                continue
            if trade_id is not None and row.get("trade_id") != trade_id:
                continue
            if intent_id is not None and row.get("intent_id") != intent_id:
                continue
            if lane is not None and row.get("lane") != str(lane).upper():
                continue
            if since_seq is not None and int(row.get("seq") or 0) <= int(since_seq):
                continue
            if since_ts is not None and float(row.get("ts") or 0) <= float(since_ts):
                continue
            out.append(dict(row))
            if len(out) >= limit:
                break
        return out

    def verify(self) -> dict:
        """Verify the chain and signatures. Fail closed on any breakage."""
        prev = GENESIS_HASH
        for index, row in enumerate(self._rows):
            canonical = _canonical(row)
            expected_hash = _sha256(prev.encode("utf-8") + canonical)
            if row.get("hash") != expected_hash:
                return {"ok": False, "rows": len(self._rows), "broken_at_seq": row.get("seq"),
                        "reason": "HASH_CHAIN_BREAK", "index": index}
            if self._key:
                expected_sig = self._sign(canonical)
                if row.get("sig") != expected_sig:
                    return {"ok": False, "rows": len(self._rows), "broken_at_seq": row.get("seq"),
                            "reason": "SIGNATURE_MISMATCH", "index": index}
            prev = row.get("hash") or GENESIS_HASH
        return {"ok": True, "rows": len(self._rows), "last_seq": self._rows[-1].get("seq") if self._rows else 0,
                "last_hash": self._rows[-1].get("hash") if self._rows else GENESIS_HASH}

    def status(self) -> dict:
        return {
            "schema": SCHEMA,
            "file": self.path.name,
            "rows": len(self._rows),
            "last_seq": self._rows[-1].get("seq") if self._rows else 0,
            "last_ts": self._rows[-1].get("ts") if self._rows else None,
            "last_hash": self._rows[-1].get("hash") if self._rows else GENESIS_HASH,
            "signed": bool(self._key),
            "verify": self.verify(),
        }


def audit_key_from_env(env: Mapping[str, str] | None = None) -> bytes | None:
    """Resolve the signing key from the environment. Returns None when unset.

    Only the environment variable NAME is referenced here; its value is never
    logged, persisted, or returned.
    """
    raw = (env if env is not None else os.environ).get("BITFINEX_AUDIT_HMAC_SECRET") or ""
    return raw.encode("utf-8") if raw else None
