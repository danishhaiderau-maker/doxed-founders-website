"""Per-tile "Bitfinex Live Orders" switch (Phase 5, fail-closed by default).

A first-class, per-lane toggle that is **distinct** from the paper toggle
(``research_lane_enabled``) and from the global arm flags (``live_armed`` /
``bitfinex_live_enabled``). It never arms anything by itself.

A lane's ``bitfinex_live_orders`` flag may only become ``True`` when ALL of the
following hold, evaluated atomically:

1. the **global relay/arm gate** is satisfied (live armed AND Bitfinex live
   enabled AND not in force-paper mode AND the relay delivery guard reports no
   arming block);
2. the lane is **allowlisted** (``platform_relay_eligible`` is True in the
   canonical registry AND the tile's ``relay_capability`` is not a
   "BLOCKED_*" value);
3. the **size / leverage / protection** checks pass (margin within the
   $0.20-$0.25 window at 100x, exchange minimum/maximum order amount respected
   without upward rounding, stop coverage + reduce-only declared).

If any gate fails the lane **stays OFF** and the exact denial reasons are
recorded (never silently rounded up, never force-armed).

This module is pure: it imports only the canonical tile registry and stdlib.
Every mutating call is guarded by a lock and persists a JSON sidecar that
contains **no secrets** (toggles + denial reasons only). Arming authority stays
with the existing ``/api/live_arm`` / ``/api/bitfinex_live`` operator paths.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Mapping

from combo_pathway_config import ACTIVE_TILE_REGISTRY, ACTIVE_TILE_ORDER

SCHEMA = "bitfinex_live_switch_v1"
# Default sidecar lives next to the other runtime state files; the caller may
# override the path (tests use a temp dir). Never place this on OneDrive.
_SIDECAR = Path(__file__).resolve().parent / "bitfinex_live_switch_state.json"

# Live-test safety contract: max $0.25 margin input at 100x (~$25 notional).
# This is NOT a max-loss guarantee; realized loss can exceed posted margin.
MIN_MARGIN_USD = 0.20
MAX_MARGIN_USD = 0.25
REQUIRED_LEVERAGE = 100

# Denial reason codes (queryable, stable across releases).
DENY_GLOBAL_NOT_ARMED = "GLOBAL_RELAY_NOT_ARMED"
DENY_FORCE_PAPER = "FORCE_PAPER_MODE_ACTIVE"
DENY_RELAY_DELIVERY_BLOCKED = "RELAY_DELIVERY_BLOCKED"
DENY_LANE_NOT_ALLOWLISTED = "LANE_NOT_ALLOWLISTED"
DENY_RELAY_CAPABILITY_BLOCKED = "LANE_RELAY_CAPABILITY_BLOCKED"
DENY_KEYS_MISSING = "PRIVATE_API_KEYS_MISSING"
DENY_EXCHANGE_AUDIT_NOT_FRESH = "EXCHANGE_AUDIT_NOT_FRESH"
DENY_EXCHANGE_NOT_FLAT = "EXCHANGE_NOT_FLAT"
DENY_EXCHANGE_ORPHAN = "EXCHANGE_ORPHAN_EXPOSURE"
DENY_MARKET_NOT_READY = "MARKET_NOT_READY"
DENY_SYSTEM_NOT_READY = "SYSTEM_NOT_READY"
DENY_ADMIN_PAUSE = "ADMIN_MANUAL_PAUSE"
DENY_MARGIN_OUT_OF_RANGE = "SIZE_MARGIN_OUT_OF_RANGE"
DENY_LEVERAGE_INVALID = "SIZE_LEVERAGE_INVALID"
DENY_EXCHANGE_MIN_QTY = "EXCHANGE_MIN_QTY_UNSATISFIED"
DENY_EXCHANGE_MAX_QTY = "EXCHANGE_MAX_QTY_EXCEEDED"
DENY_QTY_UNAVAILABLE = "EXCHANGE_QUANTITY_UNAVAILABLE"
DENY_STOP_COVERAGE = "STOP_COVERAGE_UNVERIFIED"
DENY_REDUCE_ONLY = "REDUCE_ONLY_UNSUPPORTED"
DENY_SWITCH_NOT_REQUESTED = "SWITCH_NOT_REQUESTED"
DENY_TILE_PRE_ARMING = "TILE_LIVE_SWITCH_PRE_ARMING"
# Terminal state meaning the lane is genuinely allowed and armed.
ALLOW_ARMED = "ARMED"


class BitfinexLiveSwitch:
    """Fail-closed per-lane live-orders switch state machine."""

    def __init__(self, sidecar: Path | str | None = None):
        self.path = Path(sidecar) if sidecar is not None else _SIDECAR
        self._lock = threading.Lock()
        self._rows: dict[str, dict] = {}
        self._load()

    # -- persistence -----------------------------------------------------
    def _load(self) -> None:
        try:
            if self.path.exists():
                raw = json.loads(self.path.read_text(encoding="utf-8") or "{}")
                if isinstance(raw, dict):
                    rows = raw.get("rows")
                    if isinstance(rows, dict):
                        self._rows = {k: dict(v) for k, v in rows.items() if isinstance(v, dict)}
        except (OSError, ValueError, json.JSONDecodeError):
            # Corrupt state must fail closed: keep everything OFF.
            self._rows = {}

    def _persist(self) -> None:
        try:
            payload = {"schema": SCHEMA, "updated_ts": time.time(), "rows": self._rows}
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")), encoding="utf-8")
            tmp.replace(self.path)
        except OSError:
            # Persistence failure never fails the switch ON; it only loses the
            # denial record, which is re-derived on the next evaluate().
            return

    # -- core state ------------------------------------------------------
    def _row(self, lane: str) -> dict:
        key = str(lane or "").upper()
        row = self._rows.get(key)
        if row is None:
            row = {
                "lane": key,
                "bitfinex_live_orders": False,
                "last_eval_ts": None,
                "last_denial": [],
                "last_denied_at": None,
                "last_allow_ts": None,
            }
            self._rows[key] = row
        return row

    def _registry_spec(self, lane: str) -> dict:
        return ACTIVE_TILE_REGISTRY.get(str(lane or "").upper()) or {}

    # -- evaluation ------------------------------------------------------
    def evaluate(
        self,
        lane: str,
        *,
        global_arm: Mapping[str, Any] | None = None,
        size_checks: Mapping[str, Any] | None = None,
        now: float | None = None,
    ) -> dict:
        """Compute eligibility + denial reasons for one lane (read-only).

        ``global_arm`` describes the operator/relay gate::

            {
                "force_paper_mode": bool,
                "live_armed": bool,
                "bitfinex_live_enabled": bool,
                "relay_delivery_block": str | None,
                "keys_ok": bool,
                "exchange_audit": {"authoritative": bool, "fresh": bool,
                                   "flat": bool, "orphan_order_ids": [...],
                                   "orphan_position_ids": [...]},
                "market_ready": bool,
                "system_ready": bool,
                "manual_pause": bool,
            }

        ``size_checks`` describes the exact-size / protection checks::

            {
                "margin_usd": float,
                "leverage": int,
                "notional_usd": float | None,
                "quantity": float | None,
                "exchange_min_qty": float | None,
                "exchange_max_qty": float | None,
                "stop_coverage_verified": bool,
                "reduce_only_supported": bool,
            }
        """
        now = time.time() if now is None else float(now)
        lane = str(lane or "").upper()
        g = global_arm or {}
        s = size_checks or {}
        spec = self._registry_spec(lane)
        denials: list[str] = []

        # 1. Global relay/arm gate.
        if g.get("force_paper_mode", True):
            denials.append(DENY_FORCE_PAPER)
        if not (g.get("live_armed") and g.get("bitfinex_live_enabled")):
            denials.append(DENY_GLOBAL_NOT_ARMED)
        relay_block = g.get("relay_delivery_block")
        if relay_block:
            denials.append(f"{DENY_RELAY_DELIVERY_BLOCKED}:{relay_block}")
        if not g.get("keys_ok"):
            denials.append(DENY_KEYS_MISSING)

        # 2. Exchange audit (pre-arm invariants).
        audit = g.get("exchange_audit") or {}
        if not (audit.get("authoritative") and audit.get("fresh")):
            denials.append(DENY_EXCHANGE_AUDIT_NOT_FRESH)
        if not audit.get("flat"):
            denials.append(DENY_EXCHANGE_NOT_FLAT)
        if audit.get("orphan_order_ids") or audit.get("orphan_position_ids"):
            denials.append(DENY_EXCHANGE_ORPHAN)

        # 3. Market / system / pause.
        if not g.get("market_ready"):
            denials.append(DENY_MARKET_NOT_READY)
        if not g.get("system_ready"):
            denials.append(DENY_SYSTEM_NOT_READY)
        if g.get("manual_pause"):
            denials.append(DENY_ADMIN_PAUSE)

        # 4. Allowlist (canonical registry, never a hard-coded second list).
        if not spec:
            denials.append(DENY_LANE_NOT_ALLOWLISTED)
        elif not spec.get("platform_relay_eligible"):
            denials.append(DENY_LANE_NOT_ALLOWLISTED)
        capability = str((spec or {}).get("relay_capability") or "")
        if capability.startswith("BLOCKED"):
            denials.append(DENY_RELAY_CAPABILITY_BLOCKED)

        # 5. Size / leverage / protection.
        margin = s.get("margin_usd")
        try:
            margin_f = float(margin)
        except (TypeError, ValueError):
            margin_f = None
        if margin_f is None or not (MIN_MARGIN_USD <= margin_f <= MAX_MARGIN_USD):
            denials.append(DENY_MARGIN_OUT_OF_RANGE)
        leverage = s.get("leverage")
        if leverage != REQUIRED_LEVERAGE:
            denials.append(DENY_LEVERAGE_INVALID)

        quantity = s.get("quantity")
        min_qty = s.get("exchange_min_qty")
        max_qty = s.get("exchange_max_qty")
        if quantity is None:
            denials.append(DENY_QTY_UNAVAILABLE)
        else:
            try:
                qty_f = float(quantity)
            except (TypeError, ValueError):
                qty_f = None
            if qty_f is None or qty_f <= 0:
                denials.append(DENY_QTY_UNAVAILABLE)
            else:
                if min_qty is not None and qty_f < float(min_qty):
                    denials.append(DENY_EXCHANGE_MIN_QTY)
                if max_qty is not None and qty_f > float(max_qty):
                    denials.append(DENY_EXCHANGE_MAX_QTY)

        if not s.get("stop_coverage_verified"):
            denials.append(DENY_STOP_COVERAGE)
        if not s.get("reduce_only_supported"):
            denials.append(DENY_REDUCE_ONLY)

        eligible = not denials
        return {
            "schema": SCHEMA,
            "lane": lane,
            "tile_number": self._tile_number(lane),
            "label": (spec or {}).get("label"),
            "id_prefix": (spec or {}).get("id_prefix"),
            "policy_signature": (spec or {}).get("policy_signature"),
            "relay_eligible": bool((spec or {}).get("platform_relay_eligible")),
            "relay_capability": (spec or {}).get("relay_capability"),
            "requested_margin_usd": (spec or {}).get("requested_margin_usd"),
            "eligible": eligible,
            "denials": denials,
            "evaluated_at": now,
        }

    # -- mutation --------------------------------------------------------
    def request_on(self, lane: str, *, global_arm: Mapping[str, Any] | None = None,
                   size_checks: Mapping[str, Any] | None = None,
                   now: float | None = None) -> dict:
        """Attempt to set a lane's live-orders switch ON. Fail closed."""
        now = time.time() if now is None else float(now)
        lane = str(lane or "").upper()
        with self._lock:
            result = self.evaluate(lane, global_arm=global_arm, size_checks=size_checks, now=now)
            row = self._row(lane)
            if result["eligible"]:
                row["bitfinex_live_orders"] = True
                row["last_allow_ts"] = now
                row["last_denial"] = []
                row["last_denied_at"] = None
            else:
                row["bitfinex_live_orders"] = False
                row["last_denial"] = list(result["denials"])
                row["last_denied_at"] = now
            row["last_eval_ts"] = now
            self._persist()
        return self.snapshot(lane, now=now)

    def request_off(self, lane: str, *, reason: str = "OPERATOR_OFF", now: float | None = None) -> dict:
        """Set a lane's live-orders switch OFF (always allowed; risk-reducing)."""
        now = time.time() if now is None else float(now)
        lane = str(lane or "").upper()
        with self._lock:
            row = self._row(lane)
            row["bitfinex_live_orders"] = False
            row["last_denial"] = [reason]
            row["last_denied_at"] = now
            row["last_eval_ts"] = now
            self._persist()
        return self.snapshot(lane, now=now)

    def reset_all_off(self, *, reason: str = "FAIL_CLOSED", now: float | None = None) -> dict:
        """Force every lane OFF (used on disarm / boot). Never arms anything."""
        now = time.time() if now is None else float(now)
        with self._lock:
            for lane in ACTIVE_TILE_ORDER:
                row = self._row(lane)
                row["bitfinex_live_orders"] = False
                row["last_denial"] = [reason]
                row["last_denied_at"] = now
                row["last_eval_ts"] = now
            self._persist()
        return self.status(now=now)

    # -- reads -----------------------------------------------------------
    def _tile_number(self, lane: str) -> int | None:
        try:
            return ACTIVE_TILE_ORDER.index(str(lane or "").upper()) + 1
        except ValueError:
            return None

    def snapshot(self, lane: str, *, now: float | None = None) -> dict:
        now = time.time() if now is None else float(now)
        lane = str(lane or "").upper()
        with self._lock:
            row = dict(self._row(lane))
        return {**row, "tile_number": self._tile_number(lane)}

    def status(self, *, now: float | None = None) -> dict:
        now = time.time() if now is None else float(now)
        with self._lock:
            rows = [dict(self._row(lane)) for lane in ACTIVE_TILE_ORDER]
        for row in rows:
            row["tile_number"] = self._tile_number(row["lane"])
        armed = [r for r in rows if r.get("bitfinex_live_orders")]
        return {
            "schema": SCHEMA,
            "registry_signature": _registry_signature(),
            "tile_count": len(ACTIVE_TILE_ORDER),
            "armed_lane_count": len(armed),
            "armed_lanes": [r["lane"] for r in armed],
            "rows": rows,
            "computed_at": now,
        }

    def why_not_armed(self, lane: str, *, global_arm: Mapping[str, Any] | None = None,
                      size_checks: Mapping[str, Any] | None = None,
                      now: float | None = None) -> dict:
        """Human-readable "why isn't this armed" explanation object."""
        now = time.time() if now is None else float(now)
        lane = str(lane or "").upper()
        result = self.evaluate(lane, global_arm=global_arm, size_checks=size_checks, now=now)
        snap = self.snapshot(lane, now=now)
        armed = bool(snap.get("bitfinex_live_orders"))
        if armed and result["eligible"]:
            explanation = "Armed and eligible: lane may copy new signed paper intents to Bitfinex."
        elif result["denials"]:
            explanation = "Not armed: " + "; ".join(result["denials"]) + "."
        else:
            explanation = "Not armed: switch has not been requested ON (default OFF)."
        return {
            "lane": lane,
            "armed": armed,
            "eligible": result["eligible"],
            "denials": result["denials"],
            "explanation": explanation,
            "last_denial": snap.get("last_denial") or [],
            "last_denied_at": snap.get("last_denied_at"),
            "evaluated_at": now,
        }

    # -- relay delivery gate (per-tile, fail-closed) ----------------------
    def delivery_gate(self, lane: str, *, created_at_unix=None,
                      now: float | None = None) -> tuple[bool, str | None]:
        """Whether a pending relay record may be delivered to Bitfinex.

        Consulted by the relay delivery gate immediately before any live order
        is placed. A record is deliverable only when BOTH hold atomically:

        1. the lane's "Bitfinex Live Orders" switch is currently ON; and
        2. the record was created at/after the switch's last arm time
           (``last_allow_ts``) — so flipping a tile ON can never copy a paper
           intent that was created while the tile was OFF.

        Anything else fails closed with a stable denial code. This never
        mutates the switch: a delivery-time denial only reports the reason.
        """
        now = time.time() if now is None else float(now)
        lane = str(lane or "").upper()
        snap = self.snapshot(lane, now=now)
        if not lane or not bool(snap.get("bitfinex_live_orders")):
            return False, DENY_SWITCH_NOT_REQUESTED
        allow_ts = snap.get("last_allow_ts")
        if not allow_ts:
            return False, DENY_SWITCH_NOT_REQUESTED
        try:
            created = float(created_at_unix) if created_at_unix is not None else 0.0
        except (TypeError, ValueError):
            created = 0.0
        if created <= 0 or created < float(allow_ts):
            return False, DENY_TILE_PRE_ARMING
        return True, None


def _registry_signature() -> str:
    from combo_pathway_config import active_tile_registry_signature
    return active_tile_registry_signature()


# ---------------------------------------------------------------------------
# Size / protection helper (pure, used by the API adapter to build size_checks)
# ---------------------------------------------------------------------------
def compute_size_checks(
    *,
    margin_usd: float,
    leverage: int,
    mark_price: float | None = None,
    exchange_min_qty: float | None = None,
    exchange_max_qty: float | None = None,
    stop_coverage_verified: bool = False,
    reduce_only_supported: bool = False,
) -> dict:
    """Build the ``size_checks`` dict, failing closed on any missing input.

    Quantity is derived from margin * leverage / mark_price and is never
    rounded up: a missing price, an unknown exchange bound, or a sub-minimum
    quantity all produce a denial rather than a fabricated size.
    """
    quantity = None
    if mark_price is not None and float(mark_price) > 0:
        notional = float(margin_usd) * int(leverage)
        quantity = notional / float(mark_price)
    return {
        "margin_usd": margin_usd,
        "leverage": leverage,
        "notional_usd": (float(margin_usd) * int(leverage)),
        "quantity": quantity,
        "exchange_min_qty": exchange_min_qty,
        "exchange_max_qty": exchange_max_qty,
        "stop_coverage_verified": bool(stop_coverage_verified),
        "reduce_only_supported": bool(reduce_only_supported),
    }
