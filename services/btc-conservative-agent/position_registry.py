"""Narrow atomic registry transitions for paper-order lifecycle state."""
from __future__ import annotations

from typing import Any, Dict, MutableMapping, MutableSequence, Optional, Tuple


def promote_pending_to_open(
    order: Dict[str, Any],
    position: Dict[str, Any],
    *,
    trade_lock,
    pending_orders: MutableSequence[Dict[str, Any]],
    lane_pending_orders: MutableMapping[str, MutableSequence[Dict[str, Any]]],
    open_positions: MutableSequence[Dict[str, Any]],
    lane_open_positions: MutableMapping[str, MutableSequence[Dict[str, Any]]],
    lane: str,
) -> Tuple[Dict[str, Any], bool]:
    """Atomically consume one pending order and expose at most one open trade."""
    trade_id = str(order.get("trade_id") or "")
    with trade_lock:
        if order in pending_orders:
            pending_orders.remove(order)
        pending_lane = lane_pending_orders.get(lane, [])
        if order in pending_lane:
            pending_lane.remove(order)
        existing = next(
            (
                row for row in open_positions
                if isinstance(row, dict)
                and trade_id
                and str(row.get("trade_id") or "") == trade_id
                and str(row.get("status") or "").upper() != "CLOSED"
            ),
            None,
        )
        if existing is not None:
            return existing, False
        open_positions.append(position)
        open_lane = lane_open_positions.setdefault(lane, [])
        if position not in open_lane:
            open_lane.append(position)
        return position, True


def claim_position_close(position: Dict[str, Any], *, position_close_lock, open_positions) -> bool:
    """Claim exactly one close without retaining the lock across slow callbacks."""
    with position_close_lock:
        if position not in open_positions:
            return False
        if str(position.get("status") or "").upper() == "CLOSED":
            return False
        if position.get("_close_in_progress"):
            return False
        position["_close_in_progress"] = True
        return True


def release_position_close_claim(position: Dict[str, Any], *, position_close_lock) -> None:
    with position_close_lock:
        if str(position.get("status") or "").upper() != "CLOSED":
            position.pop("_close_in_progress", None)


class PositionCloseClaimScope:
    """Compatibility scope whose lock is held only during claim, never its body."""
    def __init__(self, position, *, position_close_lock, open_positions):
        self.position = position
        self.position_close_lock = position_close_lock
        self.open_positions = open_positions
        self.claimed = False

    def __enter__(self):
        self.claimed = claim_position_close(
            self.position,
            position_close_lock=self.position_close_lock,
            open_positions=self.open_positions,
        )
        return self.claimed

    def __exit__(self, exc_type, _exc, _tb):
        if exc_type is not None and self.claimed:
            release_position_close_claim(
                self.position, position_close_lock=self.position_close_lock,
            )
        return False


def finalize_position_close(
    position: Dict[str, Any],
    *,
    position_close_lock,
    trade_lock,
    open_positions,
    lane_open_positions,
    lane: str,
) -> bool:
    """Commit the terminal mutation and registry removal in one narrow claim scope."""
    with position_close_lock:
        if not position.get("_close_in_progress"):
            return False
        if str(position.get("status") or "").upper() == "CLOSED":
            return False
        position["status"] = "CLOSED"
        position.pop("_close_in_progress", None)
        with trade_lock:
            if position in open_positions:
                open_positions.remove(position)
            lane_rows = lane_open_positions.get(lane, [])
            if position in lane_rows:
                lane_rows.remove(position)
        return True
