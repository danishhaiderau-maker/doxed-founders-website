"""Generic binding of a registry tile to a resting maker limit that converts to a capped taker on confirmation.

Same side selection, refusal, sizing, exit and dashboard contract as
``MakerTimeExitBinding``. The tile rests one passive limit
``entry_policy.offset_pct`` beyond the decision-time price. If, before it
fills, the mid moves ``confirm_move_bps`` in the trade direction from the
signal price, the limit is cancelled and replaced by one marketable limit
capped ``confirm_market_cap_bps`` beyond the confirmation price; when the
executable side is already past that cap the signal is dropped
(``CONFIRM_CAP_EXCEEDED``). An unfilled confirmation taker is dropped after
``confirm_taker_ttl_sec``; an unconfirmed, unfilled limit expires after
``maker_ttl_sec``. The bot calls ``confirm_market_action`` from its pending-
order loop; this module never places, changes or cancels an order itself.
"""
from __future__ import annotations

import math
from typing import Any

from adaptive_regime_entry import ACTION_MAKER, price_tick
from combo_pathway_config import COMBO_LANE_SPECS
from family_policy_common import dashboard_policy as _dashboard, protection_chips
from maker_time_exit_binding import MakerTimeExitBinding
from taker_time_exit_binding import evidence_badge, session_chips, signal_source_detail

CONFIRM_MARKET_ENTRY_MODE = "MAKER_LIMIT_OFFSET_CONFIRM_MARKET"
CONFIRM_HOLD = "HOLD"
CONFIRM_MARKET = "MARKET"
CONFIRM_DROP = "DROP"
_SIGN = {"LONG": 1.0, "SHORT": -1.0}


def confirm_price(direction: str, signal_price: float, confirm_move_bps: float) -> float:
    return float(signal_price) * (1.0 + _SIGN[direction] * float(confirm_move_bps) / 1e4)


def capped_taker_limit(direction: str, confirm_px: float, cap_bps: float, tick: float) -> float:
    """Marketable limit rounded inside the cap (never past it)."""
    raw = confirm_px * (1.0 + _SIGN[direction] * float(cap_bps) / 1e4)
    if direction == "LONG":
        return math.floor(raw / tick + 1e-9) * tick
    return math.ceil(raw / tick - 1e-9) * tick


class MakerConfirmMarketTimeExitBinding(MakerTimeExitBinding):
    ENTRY_MODE = CONFIRM_MARKET_ENTRY_MODE

    def __init__(self, lane: str, label: str):
        super().__init__(lane, label)
        entry = self.entry
        move, cap, ttl = (float(entry["confirm_move_bps"]), float(entry["confirm_market_cap_bps"]),
                          int(entry["confirm_taker_ttl_sec"]))
        if move <= 0 or cap <= 0 or ttl <= 0:
            raise ValueError(f"{lane}: confirm-market binding needs a positive move, cap and taker TTL")
        spec = COMBO_LANE_SPECS[lane]
        if spec.get("platform_relay_eligible") or spec.get("live_copy_eligible"):
            raise ValueError(f"{lane}: confirm-market entries are paper-only and relay-ineligible")

    def decide_entry(self, **kwargs) -> dict[str, Any]:
        record = super().decide_entry(**kwargs)
        if record.get("action") == ACTION_MAKER:
            record["reason"] = "MAKER_OFFSET_AT_SIGNAL_CONFIRM_MARKET"
            record["confirm_move_bps"] = float(self.entry["confirm_move_bps"])
            record["confirm_market_cap_bps"] = float(self.entry["confirm_market_cap_bps"])
        return record

    def confirm_market_action(self, *, direction: str, signal_price: float, limit_price: float,
                              bid: float, ask: float, confirmed_ts: float | None, now: float) -> dict[str, Any]:
        """HOLD the resting limit, convert it to a capped taker (MARKET), or DROP the signal."""
        entry = self.entry
        direction = str(direction or "").upper()
        out: dict[str, Any] = {"action": CONFIRM_HOLD, "limit_price": float(limit_price or 0) or None,
                               "confirm_price": None, "reason": "LIMIT_RESTING"}
        if confirmed_ts is not None:
            if float(now) - float(confirmed_ts) >= int(entry["confirm_taker_ttl_sec"]):
                out.update(action=CONFIRM_DROP, reason="CONFIRM_TAKER_UNFILLED")
            else:
                out["reason"] = "CONFIRM_TAKER_WORKING"
            return out
        bid = float(bid or 0); ask = float(ask or 0); signal_price = float(signal_price or 0)
        if direction not in _SIGN or signal_price <= 0 or bid <= 0 or ask <= bid:
            out["reason"] = "BBO_UNAVAILABLE"
            return out
        sign = _SIGN[direction]
        mid = (bid + ask) / 2.0
        confirm_px = confirm_price(direction, signal_price, float(entry["confirm_move_bps"]))
        if sign * (mid - confirm_px) < 0:
            return out
        tick = price_tick(mid)
        cap_limit = capped_taker_limit(direction, confirm_px, float(entry["confirm_market_cap_bps"]), tick)
        executable = ask if direction == "LONG" else bid
        out["confirm_price"] = round(confirm_px, 8)
        if sign * (executable - cap_limit) > 0:
            out.update(action=CONFIRM_DROP, reason="CONFIRM_CAP_EXCEEDED")
            return out
        out.update(action=CONFIRM_MARKET, limit_price=round(cap_limit, 8), reason="CONFIRMED_MOVE_TAKER")
        return out

    def dashboard_policy(self):
        tile = COMBO_LANE_SPECS[self.lane]
        entry, exit_policy = self.entry, self.exit
        payload = _dashboard(self.spec, signal_detail=signal_source_detail(tile))
        offset = float(entry["offset_pct"])
        ttl_min = int(entry["maker_ttl_sec"]) // 60
        hold_min = int(exit_policy["max_duration_sec"]) // 60
        max_open = int(exit_policy.get("max_open_positions") or 1)
        move, cap = float(entry["confirm_move_bps"]), float(entry["confirm_market_cap_bps"])
        payload["filter_chips"] = [
            "PAPER ONLY", evidence_badge(tile) or "HINT",
            "Side = opposite of score-led AI side",
            "Only committed calls: explicit AI side matching the scores", "Never fades NO_TRADE",
            *session_chips(entry),
            f"Maker limit {offset:g}% better than the signal price",
            f"Price moves {move:g}bp our way first → taker within {cap:g}bp cap (skip above it)",
            f"Neither within {ttl_min}m → signal dropped",
            f"Spread >{float(entry['max_spread_bps']):g}bp or BBO older than {float(entry['max_bbo_age_sec']):g}s"
            " → stand aside",
            f"Stop {exit_policy['hard_stop_bps']:g}bp catastrophic",
            f"{hold_min}m time backstop after fill",
            *protection_chips(exit_policy),
            f"Max {max_open} signals pending or open",
        ]
        payload["entry"].update({
            "trigger": ("Shared three-minute call; side is the opposite of the AI's committed side (explicit "
                        "LONG/SHORT matching the scores; NO_TRADE, mismatches, ties and errors refuse)"),
            "entry_path": self.lane,
            "chase_detail": (f"Passive limit {offset:g}% better than the decision-time price; if the mid moves "
                             f"{move:g}bp in the trade direction first, cancel and take a marketable limit within "
                             f"{cap:g}bp of the confirmation price ({int(entry['confirm_taker_ttl_sec'])}s), else "
                             f"skip; unfilled after {ttl_min}m the signal is dropped"),
            "direction_source": entry["direction_source"],
            "liquidity_intent": "MAKER_THEN_CONFIRMED_TAKER",
        })
        payload["exit"].update({
            "profile": exit_policy["family"],
            "fixed_time_exit": f"{hold_min}m",
            "hard_stop_bps": exit_policy["hard_stop_bps"],
            "stop_fill": exit_policy["stop_fill"],
            "max_open_positions": exit_policy.get("max_open_positions"),
        })
        return self._with_pre_registration(payload, tile)
