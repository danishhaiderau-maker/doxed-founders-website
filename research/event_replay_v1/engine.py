"""Synthetic full-cycle CPU reference replay.

Manufactured traces only. No real-input mode, no network, and no live policy.
Stage A records are fixture stubs; compatibility remains BLOCKED.
"""

from __future__ import annotations

import copy
import heapq
import itertools
import json
import time
from decimal import Decimal
from typing import Any

from . import (
    AUTHORITATIVE_BASE,
    COMPATIBILITY,
    FIXTURE_STUB,
    LIVE,
    STAGE_A_DESIGN_CARD_SHA256,
    STAGE_A_IMPLEMENTATION_PIN,
)
from .accounting import build_accounting, require_fee_disposition
from .entry import arrival_time, observation_visible, passive_limit
from .exits import FIXTURE_TIE_PRIORITY, ExitState, choose_winner, evaluate_exit
from .guards import (
    DEFAULT_LIMITS,
    CapStop,
    ResourceLimits,
    ResourceObservation,
    RuntimeGuard,
    enforce_observation,
    monotonic_seconds,
    sample_rss_bytes,
)
from .order_state import Order
from .pricer import (
    PRICER_ID,
    D,
    S,
    depth_slippage,
    passive_match,
    select_book,
    sort_levels,
    walk_levels,
)
from .risk import liquidation_price, margin_matches, size_decision

STUB_LABEL = FIXTURE_STUB
MISS_STATUSES = {
    "MIN_SIZE",
    "DUST",
    "INVALID_FOR_FILLS",
    "INVALID_CAUSAL",
    "NOT_STRICTLY_AFTER_CLOSE",
    "CONCURRENCY",
    "UNFILLED",
    "NEGATIVE_DELAY",
}


class FixtureError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def strip_canaries(value: Any) -> Any:
    if isinstance(value, dict):
        cleaned = {}
        for key, item in value.items():
            if "canary" in str(key).lower():
                continue
            cleaned[key] = strip_canaries(item)
        return cleaned
    if isinstance(value, list):
        return [strip_canaries(item) for item in value]
    if isinstance(value, str) and "CANARY-" in value:
        return "[REDACTED]"
    return value


def _opt_decimal(mapping: dict, key: str) -> Decimal | None:
    if key not in mapping or mapping[key] is None:
        return None
    return D(mapping[key])


def _protections(raw: dict) -> dict:
    ladder = []
    for rung in raw.get("ladder", []):
        ladder.append({"price": D(rung["price"]), "qty": D(rung["qty"])})
    return {
        "hard_stop": _opt_decimal(raw, "hard_stop"),
        "breakeven_arm": _opt_decimal(raw, "breakeven_arm"),
        "breakeven_lock": _opt_decimal(raw, "breakeven_lock"),
        "trail_arm": _opt_decimal(raw, "trail_arm"),
        "trail_distance": _opt_decimal(raw, "trail_distance"),
        "giveback": _opt_decimal(raw, "giveback"),
        "target": _opt_decimal(raw, "target"),
        "ladder": ladder,
    }


class _Signal:
    def __init__(self, spec: dict, min_qty: Decimal) -> None:
        self.spec = spec
        self.min_qty = min_qty
        self.side = spec["side"]
        self.orders: list[Order] = []
        self.position_qty = Decimal(0)
        self.position_notional = Decimal(0)
        self.position_realized = Decimal(0)
        self.had_reduction = False
        self.exit_state = ExitState()
        self.exit_pending = None
        self.entry_order: Order | None = None
        self.duplicate_acks = 0
        self.status = "OPEN"
        self.is_miss = False
        self.incomplete = False
        self.realized_out: str | None = None
        self.rounded_up = False
        self.liq_price: Decimal | None = None
        self.liquidation_status = "NOT_MODELLED"
        self.initial_margin: str | None = None
        self.notional: str | None = None
        self.loss_can_exceed_margin = None
        self.realized_loss_exceeds_margin = False
        self.dust_qty_refused = "0"
        self.opened = False

    @property
    def signal_id(self) -> str:
        return self.spec["signal_id"]

    def avg_entry(self) -> Decimal:
        if self.position_qty == 0:
            return Decimal(0)
        return self.position_notional / self.position_qty

    def add_entry(self, qty: Decimal, notional: Decimal) -> None:
        self.position_qty += qty
        self.position_notional += notional
        self.opened = True

    def reduce(self, qty: Decimal, price: Decimal) -> None:
        avg = self.avg_entry()
        if self.side == "LONG":
            self.position_realized += (price - avg) * qty
        else:
            self.position_realized += (avg - price) * qty
        self.position_notional -= avg * qty
        self.position_qty -= qty
        self.had_reduction = True


class _Scenario:
    def __init__(self, spec: dict, mutation: str | None, limits: ResourceLimits) -> None:
        self.spec = spec
        self.mutation = mutation
        self.limits = limits
        self.q: list = []
        self.counter = itertools.count(50_000_000)
        self.seen_ids: set[str] = set()
        self.observations: dict[str, dict] = {}
        self.received_books: list[dict] = []
        self.catalog_books: list[dict] = []
        self.signals: dict[str, _Signal] = {}
        self.orders: dict[str, Order] = {}
        self.fills: list[dict] = []
        self.cancels: list[dict] = []
        self.partials: list[dict] = []
        self.exits: list[dict] = []
        self.actions: list[dict] = []
        self.fill_count = 0
        self.duplicate_events = 0
        self.min_qty = D(spec["min_qty"])
        self.max_concurrent = int(spec["max_concurrent"])
        self.exit_delay_us = int(spec["exit_delay_us"])
        if self.exit_delay_us <= 0:
            raise FixtureError("EXIT_DELAY_EXPLICIT_REQUIRED")

    def schedule(self, time_us: int, kind: str, payload: dict) -> None:
        heapq.heappush(self.q, (int(time_us), next(self.counter), kind, payload))

    def act(self, time_us: int, action: str, signal_id: str, order_id: str | None) -> None:
        self.actions.append(
            {
                "time_us": int(time_us),
                "action": action,
                "signal_id": signal_id,
                "order_id": order_id,
            }
        )

    def run(self, started: float) -> None:
        events = list(self.spec["events"])
        seqs = set()
        for event in events:
            if event["type"] == "book":
                self.catalog_books.append(self._book(event))
            identity = (int(event["time_us"]), int(event["seq"]))
            if identity in seqs:
                raise FixtureError("DUPLICATE_SEQ")
            seqs.add(identity)
            if int(event["seq"]) >= 50_000_000:
                raise FixtureError("SEQ_SPACE")
            heapq.heappush(self.q, (int(event["time_us"]), int(event["seq"]), "input", event))
        while self.q:
            if monotonic_seconds(started) > self.limits.max_command_seconds:
                raise CapStop("command")
            if sample_rss_bytes() > self.limits.max_rss_bytes:
                raise CapStop("rss")
            time_us, _seq, kind, payload = heapq.heappop(self.q)
            if kind == "input":
                self._input(payload)
            else:
                self._scheduled(time_us, kind, payload)

    def _input(self, event: dict) -> None:
        event_id = event["event_id"]
        if event_id in self.seen_ids:
            self.duplicate_events += 1
            return
        self.seen_ids.add(event_id)
        kind = event["type"]
        if kind == "observation":
            self.observations[event_id] = self._observation(event)
        elif kind == "book":
            self.received_books.append(self._book(event))
        elif kind == "trade":
            self._trade(event)
        elif kind == "signal":
            self._signal(event)
        elif kind == "mark":
            self._mark(event)
        elif kind == "thesis":
            self._thesis(event)
        elif kind == "duplicate_ack":
            self._duplicate_ack(event)
        else:
            raise FixtureError("UNKNOWN_EVENT")

    def _scheduled(self, time_us: int, kind: str, payload: dict) -> None:
        if kind == "arrival":
            self._arrival(time_us, payload)
        elif kind == "cancel_request":
            self._cancel_request(time_us, payload)
        elif kind == "cancel_effective":
            self._cancel_effective(time_us, payload)
        elif kind == "chase_arrival":
            self._chase_arrival(time_us, payload)
        elif kind == "time_trigger":
            self._time_trigger(time_us, payload)
        elif kind == "exit_exec":
            self._exit_exec(time_us, payload)
        else:
            raise FixtureError("UNKNOWN_SCHEDULE")

    def _observation(self, event: dict) -> dict:
        if event.get("fixture_only") is not True:
            raise FixtureError("STAGE_A_STUB_LABEL_REQUIRED")
        if event.get("compatibility") != "BLOCKED":
            raise FixtureError("STAGE_A_STUB_LABEL_REQUIRED")
        if event.get("stage_a_stub") != STUB_LABEL:
            raise FixtureError("STAGE_A_STUB_LABEL_REQUIRED")
        if event.get("approved_stage_a_pin") is not None:
            raise FixtureError("STAGE_A_IMPLEMENTATION_PIN_NOT_APPROVED")
        if "exchange_timestamp_utc" not in event:
            raise FixtureError("EXCHANGE_TIMESTAMP_REQUIRED_OR_NULL")
        linkage = event.get("linkage")
        if not isinstance(linkage, dict) or "segment_member_record" not in linkage:
            raise FixtureError("LINKAGE_INCOMPLETE")
        return {
            "event_id": event["event_id"],
            "source_id": event["source_id"],
            "series_id": event["series_id"],
            "source_time_us": int(event["source_time_us"]),
            "publication_us": int(event["publication_us"]),
            "bar_close_us": int(event["bar_close_us"]),
            "received_us": int(event["time_us"]),
            "receive_time_utc": event["receive_time_utc"],
            "exchange_timestamp_utc": event["exchange_timestamp_utc"],
            "connection_epoch": event["connection_epoch"],
            "release_status": event["release_status"],
            "validity": event["validity"],
            "fixture_only": True,
            "compatibility": "BLOCKED",
            "stage_a_stub": STUB_LABEL,
            "schema_id": event["schema_id"],
            "approved_stage_a_pin": None,
            "linkage": linkage,
            "time_us": int(event["time_us"]),
        }

    def _book(self, event: dict) -> dict:
        if event.get("value_class") != "SYNTHETIC":
            raise FixtureError("SYNTHETIC_BOOK_LABEL_REQUIRED")
        return {
            "event_id": event["event_id"],
            "received_us": int(event["time_us"]),
            "availability_us": int(event["availability_us"]),
            "valid_from_us": int(event["valid_from_us"]),
            "valid_to_us": int(event["valid_to_us"]),
            "stale": bool(event["stale"]),
            "bids": sort_levels(event["bids"], bids=True) if event["bids"] else [],
            "asks": sort_levels(event["asks"], bids=False) if event["asks"] else [],
            "exchange_timestamp_utc": event.get("exchange_timestamp_utc"),
            "seq": int(event["seq"]),
            "value_class": "SYNTHETIC",
            "time_us": int(event["time_us"]),
        }

    def _signal(self, event: dict) -> None:
        if event.get("delay_value_class") != "SYNTHETIC":
            raise FixtureError("SYNTHETIC_DELAY_LABEL_REQUIRED")
        if "entry_offset_bps" not in event or "regime" not in event:
            raise FixtureError("OFFSET_AND_REGIME_EXPLICIT_REQUIRED")
        if event["regime"] == "":
            raise FixtureError("OFFSET_AND_REGIME_EXPLICIT_REQUIRED")
        signal = _Signal(event, self.min_qty)
        self.signals[signal.signal_id] = signal
        self.act(int(event["time_us"]), "SIGNAL", signal.signal_id, None)
        code, _arrival = arrival_time(
            int(event["bar_close_us"]),
            int(event["time_us"]),
            int(event["arrival_delay_us"]),
        )
        if code != "OK":
            self._miss(signal, code, int(event["time_us"]))
            return
        for obs_id in event["observation_ids"]:
            obs = self.observations.get(obs_id)
            if obs is None or not observation_visible(obs, int(event["time_us"])):
                self._miss(signal, "INVALID_CAUSAL", int(event["time_us"]))
                return
        if self._open_slots() >= self.max_concurrent:
            self._miss(signal, "CONCURRENCY", int(event["time_us"]))
            return
        decision, qty, rounded = size_decision(
            D(event["qty"]),
            self.min_qty,
            increase_size=self.mutation == "increase_size",
        )
        signal.rounded_up = rounded
        if decision != "OK":
            rejected = self._make_order(
                signal,
                role="ENTRY",
                side="BUY" if signal.side == "LONG" else "SELL",
                qty=D(event["qty"]),
                style=event["style"],
                created_us=int(event["time_us"]),
                limit_price=None,
                queue_ahead=None,
                suffix="entry",
            )
            rejected.reject_open(int(event["time_us"]), "MIN_SIZE")
            self.act(int(event["time_us"]), "REJECT", signal.signal_id, rejected.order_id)
            self._miss(signal, "MIN_SIZE", int(event["time_us"]))
            return
        order = self._submit_entry(signal, qty, rounded)
        if order is None:
            return
        self.schedule(order.created_us, "arrival", {"order_id": order.order_id})

    def _submit_entry(self, signal: _Signal, qty: Decimal, rounded: bool) -> Order | None:
        event = signal.spec
        style = event["style"]
        side = "BUY" if signal.side == "LONG" else "SELL"
        code, arrival = arrival_time(
            int(event["bar_close_us"]),
            int(event["time_us"]),
            int(event["arrival_delay_us"]),
        )
        if code != "OK" or arrival is None:
            self._miss(signal, code, int(event["time_us"]))
            return None
        limit_price = None
        queue_ahead = None
        if style == "PASSIVE":
            if event.get("queue_value_class") != "SYNTHETIC" or "queue_ahead_qty" not in event:
                raise FixtureError("QUEUE_AHEAD_EXPLICIT_SYNTHETIC_REQUIRED")
            limit_price = passive_limit(
                side=side,
                explicit_limit=_opt_decimal(event, "limit_price"),
                reference_price=_opt_decimal(event, "reference_price"),
                offset_bps=D(event["entry_offset_bps"]),
            )
            queue_ahead = D(event["queue_ahead_qty"])
        elif style != "MARKETABLE":
            raise FixtureError("STYLE")
        if event.get("ttl_us") is not None or event.get("chase_after_us") is not None:
            if event.get("cancel_ack_delay_us") is None:
                raise FixtureError("CANCEL_ACK_DELAY_EXPLICIT_REQUIRED")
        order = self._make_order(
            signal,
            role="ENTRY",
            side=side,
            qty=qty,
            style=style,
            created_us=arrival,
            limit_price=limit_price,
            queue_ahead=queue_ahead,
            suffix="entry",
        )
        order.rounded_up = rounded
        signal.entry_order = order
        self.act(arrival, "SUBMIT", signal.signal_id, order.order_id)
        return order

    def _make_order(
        self,
        signal: _Signal,
        *,
        role: str,
        side: str,
        qty: Decimal,
        style: str,
        created_us: int,
        limit_price: Decimal | None,
        queue_ahead: Decimal | None,
        suffix: str,
        parent: str | None = None,
    ) -> Order:
        order = Order(
            order_id=f"{signal.signal_id}:{suffix}",
            signal_id=signal.signal_id,
            strategy_id=signal.spec["strategy_id"],
            role=role,
            side=side,
            original=qty,
            style=style,
            created_us=created_us,
            limit_price=limit_price,
            queue_ahead=queue_ahead,
            parent_order_id=parent,
        )
        self.orders[order.order_id] = order
        signal.orders.append(order)
        return order

    def _miss(self, signal: _Signal, status: str, time_us: int) -> None:
        signal.status = status
        signal.is_miss = status in MISS_STATUSES
        signal.realized_out = "0"
        signal.incomplete = False
        self.act(time_us, "REJECT", signal.signal_id, None)

    def _open_slots(self) -> int:
        count = 0
        for signal in self.signals.values():
            if signal.position_qty > 0:
                count += 1
                continue
            order = signal.entry_order
            if order is not None and order.open_qty > 0 and order.state in {
                "PENDING_ARRIVAL",
                "WORKING",
                "PENDING_CANCEL",
                "PARTIAL",
            }:
                count += 1
        return count

    def _arrival(self, time_us: int, payload: dict) -> None:
        order = self.orders[payload["order_id"]]
        signal = self.signals[order.signal_id]
        if order.open_qty <= 0:
            return
        ack = order.apply_ack(f"{order.order_id}:arrival", time_us)
        self.act(time_us, "ACK" if ack == "ACK" else "DUPLICATE_ACK", signal.signal_id, order.order_id)
        if order.style == "MARKETABLE":
            self._take(signal, order, time_us, role="ENTRY")
        self._arm_timers(signal, order, time_us)

    def _arm_timers(self, signal: _Signal, order: Order, arrival_us: int) -> None:
        ttl = signal.spec.get("ttl_us")
        chase = signal.spec.get("chase_after_us")
        if ttl is not None:
            self.schedule(arrival_us + int(ttl), "cancel_request", {"order_id": order.order_id, "reason": "TTL"})
        if chase is not None:
            self.schedule(
                arrival_us + int(chase),
                "cancel_request",
                {"order_id": order.order_id, "reason": "CHASE"},
            )

    def _cancel_request(self, time_us: int, payload: dict) -> None:
        order = self.orders[payload["order_id"]]
        if order.open_qty <= 0 or order.state == "PENDING_CANCEL":
            return
        order.note(time_us, "PENDING_CANCEL", payload["reason"])
        self.act(time_us, "CANCEL_REQUEST", order.signal_id, order.order_id)
        delay = int(self.signals[order.signal_id].spec["cancel_ack_delay_us"])
        if self.mutation == "instant_cancels":
            delay = 0
        self.schedule(
            time_us + delay,
            "cancel_effective",
            {"order_id": order.order_id, "reason": payload["reason"]},
        )

    def _cancel_effective(self, time_us: int, payload: dict) -> None:
        order = self.orders[payload["order_id"]]
        if order.open_qty <= 0:
            return
        qty = order.cancel_open(time_us, payload["reason"])
        self.cancels.append(
            {
                "time_us": time_us,
                "order_id": order.order_id,
                "signal_id": order.signal_id,
                "qty": S(qty),
                "reason": payload["reason"],
            }
        )
        self.act(time_us, "CANCEL_EFFECTIVE", order.signal_id, order.order_id)
        signal = self.signals[order.signal_id]
        if payload["reason"] == "CHASE" and qty > 0:
            chase = self._make_order(
                signal,
                role="ENTRY",
                side=order.side,
                qty=qty,
                style="MARKETABLE",
                created_us=time_us + int(signal.spec["arrival_delay_us"]),
                limit_price=None,
                queue_ahead=None,
                suffix="chase",
                parent=order.order_id,
            )
            self.act(chase.created_us, "SUBMIT", signal.signal_id, chase.order_id)
            self.schedule(chase.created_us, "chase_arrival", {"order_id": chase.order_id})
        elif signal.position_qty == 0 and not signal.opened:
            self._miss(signal, "UNFILLED", time_us)

    def _chase_arrival(self, time_us: int, payload: dict) -> None:
        order = self.orders[payload["order_id"]]
        signal = self.signals[order.signal_id]
        if order.open_qty <= 0:
            return
        order.apply_ack(f"{order.order_id}:arrival", time_us)
        self.act(time_us, "ACK", signal.signal_id, order.order_id)
        self._take(signal, order, time_us, role="ENTRY")

    def _trade(self, event: dict) -> None:
        if event.get("value_class") != "SYNTHETIC":
            raise FixtureError("SYNTHETIC_TRADE_LABEL_REQUIRED")
        if self.mutation == "omit_partials":
            return
        price = D(event["price"])
        qty = D(event["qty"])
        for order in list(self.orders.values()):
            if order.style != "PASSIVE" or order.open_qty <= 0:
                continue
            if order.state not in {"WORKING", "PENDING_CANCEL", "PARTIAL"}:
                continue
            if order.queue_ahead is None or order.limit_price is None:
                continue
            matched = passive_match(order.side, order.limit_price, order.queue_ahead, price, qty)
            order.queue_ahead = matched["queue_ahead"]
            if matched["qty"] <= 0:
                continue
            take = matched["qty"] if matched["qty"] < order.open_qty else order.open_qty
            self._record_fill(
                self.signals[order.signal_id],
                order,
                int(event["time_us"]),
                qty=take,
                price=matched["price"],
                notional=matched["price"] * take,
                parts=[{"price": S(matched["price"]), "qty": S(take)}],
                mechanism="PASSIVE_RESTING_LIMIT",
                evidence=matched["evidence"],
                role="ENTRY",
                book_id=None,
                touch=matched["price"],
                depth_slip=Decimal(0),
                trigger_price=None,
            )

    def _take(self, signal: _Signal, order: Order, time_us: int, *, role: str) -> None:
        buys = order.side == "BUY"
        book = select_book(
            self.catalog_books if self.mutation == "admit_future_books" else self.received_books,
            time_us,
            admit_future=self.mutation == "admit_future_books",
        )
        if book is None:
            order.reject_open(time_us, "INVALID_FOR_FILLS")
            self.act(time_us, "REJECT", signal.signal_id, order.order_id)
            if role == "ENTRY" and not signal.opened:
                self._miss(signal, "INVALID_FOR_FILLS", time_us)
            else:
                signal.incomplete = True
                signal.status = "INCOMPLETE"
            return
        levels = book["asks"] if buys else book["bids"]
        walked = walk_levels(levels, order.open_qty)
        filled = walked["filled"]
        if filled <= 0 or filled < signal.min_qty:
            if filled > 0:
                signal.dust_qty_refused = S(filled)
            order.reject_open(time_us, "DUST" if filled > 0 else "INVALID_FOR_FILLS")
            self.act(time_us, "REJECT", signal.signal_id, order.order_id)
            if role == "ENTRY" and not signal.opened:
                self._miss(signal, "DUST" if filled > 0 else "INVALID_FOR_FILLS", time_us)
            else:
                signal.incomplete = True
                signal.status = "INCOMPLETE"
            return
        residual = walked["residual"]
        slip = depth_slippage(buys, walked["vwap"], walked["touch"], filled)
        self._record_fill(
            signal,
            order,
            time_us,
            qty=filled,
            price=walked["vwap"],
            notional=walked["notional"],
            parts=walked["parts"],
            mechanism="OPPOSITE_SIDE_DEPTH_WALK",
            evidence="DEPTH",
            role=role,
            book_id=book["event_id"],
            touch=walked["touch"],
            depth_slip=slip,
            trigger_price=None,
        )
        if order.open_qty > 0:
            order.reject_open(time_us, "INSUFFICIENT_DEPTH")
            self.act(time_us, "REJECT", signal.signal_id, order.order_id)
            del residual

    def _record_fill(
        self,
        signal: _Signal,
        order: Order,
        time_us: int,
        *,
        qty: Decimal,
        price: Decimal,
        notional: Decimal,
        parts: list,
        mechanism: str,
        evidence: str | None,
        role: str,
        book_id: str | None,
        touch: Decimal,
        depth_slip: Decimal,
        trigger_price: Decimal | None,
    ) -> None:
        was_partial = qty < order.open_qty or order.state == "PENDING_CANCEL"
        order.apply_fill(qty, time_us)
        self.fill_count += 1
        if role == "ENTRY":
            signal.add_entry(qty, notional)
            self._on_entry_fill(signal, time_us)
        else:
            signal.reduce(qty, price)
        delay_gap = Decimal(0)
        if trigger_price is not None:
            if order.side == "SELL":
                delay_gap = max(Decimal(0), (trigger_price - price) * qty)
            else:
                delay_gap = max(Decimal(0), (price - trigger_price) * qty)
        fill = {
            "fill_id": f"{order.order_id}:fill:{self.fill_count}",
            "signal_id": signal.signal_id,
            "order_id": order.order_id,
            "strategy_id": signal.spec["strategy_id"],
            "pricer_id": PRICER_ID,
            "role": role,
            "time_us": int(time_us),
            "qty": S(qty),
            "price": S(price),
            "notional": S(notional),
            "mechanism": mechanism,
            "evidence": evidence,
            "parts": parts,
            "book_id": book_id,
            "reference_touch": S(touch),
            "depth_slippage": S(depth_slip),
            "trigger_price": None if trigger_price is None else S(trigger_price),
            "delay_gap_adverse": S(delay_gap),
            "queue_value_class": "SYNTHETIC" if evidence in {"TRADED_THROUGH", "QUEUE_CONSUMPTION"} else None,
            "value_class": "SYNTHETIC",
            "rounded_up": False,
        }
        self.fills.append(fill)
        if was_partial or role == "ENTRY" and S(qty) != S(order.original):
            self.partials.append(
                {
                    "time_us": int(time_us),
                    "signal_id": signal.signal_id,
                    "order_id": order.order_id,
                    "fill_id": fill["fill_id"],
                    "qty": S(qty),
                    "price": S(price),
                }
            )
        self.act(time_us, "FILL", signal.signal_id, order.order_id)

    def _on_entry_fill(self, signal: _Signal, time_us: int) -> None:
        self._apply_maintenance(signal)
        if signal.spec.get("time_exit_us") is not None and signal.exit_pending is None:
            when = int(signal.spec["time_exit_us"])
            if when > time_us:
                self.schedule(when, "time_trigger", {"signal_id": signal.signal_id})

    def _apply_maintenance(self, signal: _Signal) -> None:
        maintenance = signal.spec.get("maintenance")
        posted = signal.spec.get("posted_margin")
        leverage = signal.spec.get("leverage")
        if posted is not None:
            if leverage is None:
                raise FixtureError("LEVERAGE_REQUIRED_WITH_MARGIN")
            notional = signal.position_qty * signal.avg_entry()
            if not margin_matches(signal.position_qty, signal.avg_entry(), D(leverage), D(posted)):
                raise FixtureError("MARGIN_LEVERAGE_INCONSISTENT")
            signal.initial_margin = S(D(posted))
            signal.notional = S(notional)
            signal.loss_can_exceed_margin = D(leverage) > 1
        if maintenance is None:
            signal.liquidation_status = "NOT_MODELLED"
            return
        if maintenance.get("status") == "UNKNOWN" or "mmr" not in maintenance:
            signal.liquidation_status = "INVALID_UNKNOWN_MAINTENANCE"
            signal.liq_price = None
            signal.incomplete = True
            return
        if leverage is None or posted is None:
            signal.liquidation_status = "INVALID_UNKNOWN_MAINTENANCE"
            signal.liq_price = None
            signal.incomplete = True
            return
        signal.liq_price = liquidation_price(
            signal.side, signal.avg_entry(), D(leverage), D(maintenance["mmr"])
        )
        signal.liquidation_status = "MODELLED_FIXTURE_IDENTITY_NOT_VENUE_PROOF"

    def _mark(self, event: dict) -> None:
        signal = self.signals[event["signal_id"]]
        if signal.position_qty <= 0 or signal.exit_pending is not None:
            return
        triggers = evaluate_exit(
            side=signal.side,
            price=D(event["price"]),
            state=signal.exit_state,
            protections=_protections(signal.spec.get("protections") or {}),
            liq_price=signal.liq_price,
            thesis=False,
            time_due=False,
        )
        self._schedule_exit(signal, int(event["time_us"]), triggers, D(event["price"]))

    def _thesis(self, event: dict) -> None:
        signal = self.signals[event["signal_id"]]
        if not signal.spec.get("thesis_cut"):
            raise FixtureError("THESIS_NOT_ENABLED")
        if signal.position_qty <= 0 or signal.exit_pending is not None:
            return
        price = signal.avg_entry() if signal.position_qty else None
        triggers = evaluate_exit(
            side=signal.side,
            price=price,
            state=signal.exit_state,
            protections=_protections(signal.spec.get("protections") or {}),
            liq_price=signal.liq_price,
            thesis=True,
            time_due=False,
        )
        self._schedule_exit(signal, int(event["time_us"]), triggers, price)

    def _time_trigger(self, time_us: int, payload: dict) -> None:
        signal = self.signals[payload["signal_id"]]
        if signal.position_qty <= 0 or signal.exit_pending is not None:
            return
        triggers = evaluate_exit(
            side=signal.side,
            price=None,
            state=signal.exit_state,
            protections=_protections(signal.spec.get("protections") or {}),
            liq_price=signal.liq_price,
            thesis=False,
            time_due=True,
        )
        self._schedule_exit(signal, time_us, triggers, None)

    def _schedule_exit(self, signal: _Signal, time_us: int, triggers: list[dict], observed: Decimal | None) -> None:
        winner = choose_winner(triggers)
        if winner is None:
            return
        if winner["qty_mode"] == "ALL":
            qty = signal.position_qty
        else:
            qty = winner["qty"] if winner["qty"] < signal.position_qty else signal.position_qty
        if qty <= 0:
            return
        if qty < signal.min_qty:
            signal.dust_qty_refused = S(qty)
            return
        if winner["name"] == "LADDER":
            signal.exit_state.ladder_index += 1
        signal.exit_pending = {
            "name": winner["name"],
            "qty": qty,
            "trigger_price": winner["trigger_price"],
            "trigger_time_us": time_us,
            "suppressed": winner["suppressed"],
            "considered": winner["considered"],
            "observed_price": None if observed is None else S(observed),
        }
        self.act(time_us, "EXIT_TRIGGER", signal.signal_id, None)
        self.schedule(time_us + self.exit_delay_us, "exit_exec", {"signal_id": signal.signal_id})

    def _exit_exec(self, time_us: int, payload: dict) -> None:
        signal = self.signals[payload["signal_id"]]
        pending = signal.exit_pending
        if pending is None or signal.position_qty <= 0:
            signal.exit_pending = None
            return
        qty = pending["qty"] if pending["qty"] < signal.position_qty else signal.position_qty
        side = "SELL" if signal.side == "LONG" else "BUY"
        order = self._make_order(
            signal,
            role="EXIT",
            side=side,
            qty=qty,
            style="MARKETABLE",
            created_us=time_us,
            limit_price=None,
            queue_ahead=None,
            suffix=f"exit:{pending['name']}",
        )
        self.act(time_us, "EXIT_EXEC", signal.signal_id, order.order_id)
        trigger = pending["trigger_price"]
        if self.mutation == "fill_at_trigger_price" and trigger is not None:
            order.apply_ack(f"{order.order_id}:arrival", time_us)
            self._record_fill(
                signal,
                order,
                time_us,
                qty=qty,
                price=trigger,
                notional=trigger * qty,
                parts=[{"price": S(trigger), "qty": S(qty)}],
                mechanism="TRIGGER_PRICE_MUTATION",
                evidence="TRIGGER_PRICE",
                role="EXIT",
                book_id=None,
                touch=trigger,
                depth_slip=Decimal(0),
                trigger_price=trigger,
            )
        else:
            order.apply_ack(f"{order.order_id}:arrival", time_us)
            before = len(self.fills)
            self._take(signal, order, time_us, role="EXIT")
            if len(self.fills) > before and trigger is not None:
                fill = self.fills[-1]
                price = D(fill["price"])
                if order.side == "SELL":
                    gap = max(Decimal(0), (trigger - price) * D(fill["qty"]))
                else:
                    gap = max(Decimal(0), (price - trigger) * D(fill["qty"]))
                fill["trigger_price"] = S(trigger)
                fill["delay_gap_adverse"] = S(gap)
        self.exits.append(
            {
                "time_us": time_us,
                "signal_id": signal.signal_id,
                "order_id": order.order_id,
                "winner": pending["name"],
                "suppressed": pending["suppressed"],
                "considered": pending["considered"],
                "trigger_price": None if trigger is None else S(trigger),
                "trigger_time_us": pending["trigger_time_us"],
                "observed_price": pending["observed_price"],
                "qty": S(qty),
            }
        )
        signal.exit_pending = None

    def _duplicate_ack(self, event: dict) -> None:
        signal = self.signals[event["signal_id"]]
        order = self.orders.get(event["order_id"])
        if order is None:
            return
        result = order.apply_ack(event["ack_id"], int(event["time_us"]))
        if result == "DUPLICATE_IGNORED":
            signal.duplicate_acks += 1
            self.act(int(event["time_us"]), "DUPLICATE_ACK", signal.signal_id, order.order_id)

    def finish(self) -> dict:
        public_signals = {}
        for signal in self.signals.values():
            if signal.status in MISS_STATUSES:
                public_signals[signal.signal_id] = self._public_signal(signal)
                continue
            if signal.position_qty > 0:
                signal.incomplete = True
                if signal.liquidation_status == "INVALID_UNKNOWN_MAINTENANCE":
                    signal.status = "INVALID_UNKNOWN_MAINTENANCE"
                else:
                    signal.status = "INCOMPLETE"
                signal.realized_out = S(signal.position_realized) if signal.had_reduction else None
            elif signal.opened:
                signal.status = "CLOSED"
                signal.incomplete = False
                signal.realized_out = S(signal.position_realized)
            elif signal.status == "OPEN":
                signal.status = "UNFILLED"
                signal.is_miss = True
                signal.realized_out = "0"
            if signal.initial_margin is not None and signal.realized_out is not None:
                realized = Decimal(signal.realized_out)
                loss = -realized if realized < 0 else Decimal(0)
                signal.realized_loss_exceeds_margin = loss > Decimal(signal.initial_margin)
            public_signals[signal.signal_id] = self._public_signal(signal)
        for order in self.orders.values():
            if not order.conserved():
                raise FixtureError("QUANTITY_NOT_CONSERVED")
        status = "COMPLETE"
        if any(row["incomplete"] for row in public_signals.values()):
            if any(row["status"] == "INVALID_UNKNOWN_MAINTENANCE" for row in public_signals.values()):
                status = "INVALID_UNKNOWN_MAINTENANCE"
            else:
                status = "INCOMPLETE"
        return {
            "scenario_id": self.spec["scenario_id"],
            "status": status,
            "signals": public_signals,
            "fills": self.fills,
            "orders": [self._public_order(order) for order in self.orders.values()],
            "cancels": self.cancels,
            "partials": self.partials,
            "exits": self.exits,
            "actions": self.actions,
            "observations": list(self.observations.values()),
            "duplicate_events_ignored": self.duplicate_events,
        }

    def _public_signal(self, signal: _Signal) -> dict:
        return {
            "signal_id": signal.signal_id,
            "scenario_id": self.spec["scenario_id"],
            "strategy_id": signal.spec["strategy_id"],
            "pricer_id": PRICER_ID,
            "side": signal.side,
            "qty": str(signal.spec["qty"]),
            "time_us": int(signal.spec["time_us"]),
            "status": signal.status,
            "is_miss": signal.is_miss,
            "incomplete": signal.incomplete,
            "realized_pnl": signal.realized_out,
            "rounded_up": signal.rounded_up,
            "delay_value_class": "SYNTHETIC",
            "regime": signal.spec["regime"],
            "entry_offset_bps": str(signal.spec["entry_offset_bps"]),
            "liquidation_status": signal.liquidation_status,
            "liq_price": None if signal.liq_price is None else S(signal.liq_price),
            "initial_margin": signal.initial_margin,
            "notional": signal.notional,
            "margin_is_maximum_loss": False,
            "loss_can_exceed_margin": signal.loss_can_exceed_margin,
            "realized_loss_exceeds_margin": signal.realized_loss_exceeds_margin,
            "dust_qty_refused": signal.dust_qty_refused,
            "duplicate_acks_ignored": signal.duplicate_acks,
            "real_policy": {
                "dates": None,
                "n": None,
                "maxhold": None,
                "cost_threshold": None,
                "status": "NULL/BLOCKED",
            },
        }

    def _public_order(self, order: Order) -> dict:
        return {
            "order_id": order.order_id,
            "signal_id": order.signal_id,
            "strategy_id": order.strategy_id,
            "role": order.role,
            "side": order.side,
            "time_us": order.created_us,
            "original_qty": S(order.original),
            "filled_qty": S(order.filled),
            "cancelled_qty": S(order.cancelled),
            "rejected_qty": S(order.rejected),
            "open_qty": S(order.open_qty),
            "state": order.state,
            "reason": order.reason,
            "rounded_up": order.rounded_up,
            "limit_price": None if order.limit_price is None else S(order.limit_price),
            "style": order.style,
            "mechanism": (
                "PASSIVE_RESTING_LIMIT" if order.style == "PASSIVE" else "OPPOSITE_SIDE_DEPTH_WALK"
            ),
            "queue_value_class": "SYNTHETIC" if order.queue_ahead is not None else None,
            "history": order.history,
            "parent_order_id": order.parent_order_id,
            "conserved": order.conserved(),
        }


def _require_scope(fixture: dict) -> None:
    if fixture.get("fixture_label") != STUB_LABEL:
        raise FixtureError("FIXTURE_LABEL_REQUIRED")
    if fixture.get("compatibility") != "BLOCKED":
        raise FixtureError("COMPATIBILITY_BLOCKED_REQUIRED")
    if fixture.get("approved_stage_a_pin") is not None:
        raise FixtureError("STAGE_A_IMPLEMENTATION_PIN_NOT_APPROVED")
    if fixture.get("realistic_v1") is not None:
        raise FixtureError("REALISTIC_V1_NOT_RESOLVED_HERE")
    if fixture.get("live") != "OFF":
        raise FixtureError("LIVE_OFF")
    if fixture.get("r57_actual_sweep") is not False:
        raise FixtureError("NOT_R57_SWEEP")
    policy = fixture.get("real_policy") or {}
    for key in ("dates", "n", "maxhold", "cost_threshold"):
        if policy.get(key) is not None:
            raise FixtureError("REAL_POLICY_NULL_BLOCKED")


def replay(
    fixture: dict,
    *,
    mutation: str | None = None,
    workers: int = 1,
    limits: ResourceLimits | None = None,
) -> dict:
    limits = limits or DEFAULT_LIMITS
    started = time.monotonic()
    enforce_observation(
        ResourceObservation(workers=workers, events=0, rss_bytes=sample_rss_bytes()),
        limits,
    )
    clean = strip_canaries(copy.deepcopy(fixture))
    require_fee_disposition(clean)
    _require_scope(clean)
    manufactured = sum(len(item["events"]) for item in clean["scenarios"])
    if manufactured > limits.max_events:
        raise CapStop("events")
    guard = RuntimeGuard()
    guard.activate()
    scenarios = {}
    try:
        for spec in clean["scenarios"]:
            runner = _Scenario(spec, mutation, limits)
            runner.run(started)
            scenarios[spec["scenario_id"]] = runner.finish()
    finally:
        guard.deactivate()
    signals = []
    fills = []
    for spec in clean["scenarios"]:
        scenario = scenarios[spec["scenario_id"]]
        signals.extend(scenario["signals"].values())
        fills.extend(scenario["fills"])
    accounting = build_accounting(signals, fills, drop_misses=mutation == "drop_misses")
    elapsed = monotonic_seconds(started)
    rss = sample_rss_bytes()
    payload = {
        "compatibility": {
            "stage_a": COMPATIBILITY,
            "fixture_stub": STUB_LABEL,
            "approved_stage_a_implementation_pin": STAGE_A_IMPLEMENTATION_PIN,
            "stage_a_design_card_sha256": STAGE_A_DESIGN_CARD_SHA256,
            "stage_a_design_role": "APPROVED_DESIGN_ONLY",
            "producer_schemas_redefined": False,
            "interface_compatibility": "BLOCKED",
        },
        "authoritative_base": AUTHORITATIVE_BASE,
        "live": LIVE,
        "realistic_v1": None,
        "real_policy": {
            "dates": None,
            "n": None,
            "maxhold": None,
            "cost_threshold": None,
            "status": "NULL/BLOCKED",
        },
        "r57_actual_sweep": False,
        "cpu_reference_is_r57_actual_sweep": False,
        "research_complete": False,
        "profitability_claim": False,
        "findings_claim": False,
        "synthetic_not_venue_proof": True,
        "pricer_id": PRICER_ID,
        "fixture_tie_priority": list(FIXTURE_TIE_PRIORITY),
        "tie_priority_is_live_policy": False,
        "mutation": mutation,
        "scenarios": scenarios,
        "accounting": accounting,
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    enforce_observation(
        ResourceObservation(
            workers=workers,
            rss_bytes=rss,
            command_seconds=elapsed,
            suite_seconds=0,
            receipt_bytes=len(raw),
            events=manufactured,
        ),
        limits,
    )
    payload["resource"] = {
        "enforced": True,
        "workers": workers,
        "workers_cap": limits.max_workers,
        "rss_bytes": rss,
        "rss_cap_bytes": limits.max_rss_bytes,
        "command_seconds": elapsed,
        "command_cap_seconds": limits.max_command_seconds,
        "suite_cap_seconds": limits.max_suite_seconds,
        "receipt_bytes": len(raw),
        "receipt_cap_bytes": limits.max_receipt_bytes,
        "events": manufactured,
        "events_cap": limits.max_events,
    }
    return payload
