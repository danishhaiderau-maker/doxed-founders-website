"""Automatic stop-or-disarm guardian for the GS-01 live test (HM 19 Oct). Pure decision logic, no I/O.

``evaluate`` looks at the live-copy chains (executor reports), the relay's exchange mirror (balance / positions / orders /
orphans) and its own small state, and returns violations. The bot calls ``_disarm_live_control`` on any violation, which
turns Fly's live output OFF, resets every per-tile switch, cancels managed pending entries and marks open positions exit-only.
It never places or closes an order itself. No trading fees; funding only.
"""
from __future__ import annotations

import time
from typing import Any, Iterable, Mapping

STOP_MISSING_SEC = 5.0          # HM: stop must be confirmed within 5 s of the fill
DAILY_LOSS_CAP_USD = 1.00       # HM: daily realised live loss cap
LEDGER_GRACE_SEC = 10.0         # fill/close propagation grace before a position-count mismatch counts
BALANCE_DRIFT_USD = 0.50        # unexplained balance change (beyond reported pnl + funding) when flat
ORPHAN_GRACE_SEC = 3.0          # an orphan must persist across this long (two reads) to count
LANE_NEEDLE = "GS01"


def _f(v):
    try:
        x = float(v)
        return x if x == x else None
    except (TypeError, ValueError):
        return None


def day_key(now: float) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(now))


HEARTBEAT_STALE_SEC = 10.0      # guardian loop must beat at least this often while live output is ON
PENDING_EXPOSURE_SEC = 300.0    # an order sent and not yet filled/closed counts as exposure for this long


def heartbeat_stale(last_beat_ts, now: float, limit: float = HEARTBEAT_STALE_SEC) -> bool:
    """True when the guardian loop is dead/stale (never beat counts as stale)."""
    b = _f(last_beat_ts)
    return b is None or now - b > limit


def evaluate(chains: Iterable[Mapping[str, Any]], mirror: Mapping[str, Any] | None, state: dict, now: float,
             lane_needle: str = LANE_NEEDLE, funding_usd_today: float = 0.0, epoch_ts: float | None = None) -> list:
    """Return [{code, detail}] violations; ``state`` is mutated (day baseline, orphan first-seen).

    Only FRESH-EPOCH live ledger rows count (``epoch_ts``: chains whose first stage is before it are ignored), so a pre-epoch
    row can never trip the loss cap or the drift check."""
    out: list = []
    live = [c for c in chains if (lane_needle in str(c.get("lane") or c.get("correlation_id") or "").upper() or not lane_needle)
            and (epoch_ts is None or (_f(c.get("fill_at_ts") or c.get("order_sent_at_ts") or c.get("fly_signal_at_ts")) or 0.0) >= float(epoch_ts))]
    open_filled = [c for c in live if c.get("fill_at_ts") and not c.get("closed")]
    pending = [c for c in live if c.get("order_sent_at_ts") and not c.get("fill_at_ts") and not c.get("closed")
               and now - float(c["order_sent_at_ts"]) < PENDING_EXPOSURE_SEC]
    exposed = bool(open_filled or pending)
    # 1. stop missing > 5 s after fill
    for c in open_filled:
        age = now - float(c["fill_at_ts"])
        if not c.get("stop_confirmed") and age > STOP_MISSING_SEC:
            out.append({"code": "STOP_MISSING_GT_5S", "detail": f"{c.get('correlation_id')} unprotected {age:.1f}s"})
    m = mirror if isinstance(mirror, Mapping) else None
    if m is None or m.get("ok") is not True:
        # a blind exchange read while live is open is itself unsafe, but a single failed read must not flap: require 30 s
        state.setdefault("blind_since", now)
        state["read_state"] = "BLIND"   # a failed/stale read is never a clean state
        if exposed and now - state["blind_since"] > 30.0:
            out.append({"code": "EXCHANGE_MIRROR_BLIND", "detail": str((m or {}).get("error") or "no mirror")})
        return out
    state.pop("blind_since", None)
    state["read_state"] = "OK"
    positions, orders = list(m.get("positions") or []), list(m.get("open_orders") or [])
    orphans = list(m.get("orphans") or [])
    # 2. orphan (persisting)
    if orphans:
        first = state.setdefault("orphan_since", now)
        if now - first >= ORPHAN_GRACE_SEC:
            out.append({"code": "ORPHAN", "detail": f"{len(orphans)} orphan(s)"})
    else:
        state.pop("orphan_since", None)
    # 3. ledger mismatch: exchange positions vs our open chains (after propagation grace)
    recent = [c for c in live if any(c.get(k) and now - float(c[k]) < LEDGER_GRACE_SEC for k in ("fill_at_ts",))]
    if not recent and len(positions) != len(open_filled):
        out.append({"code": "LEDGER_MISMATCH", "detail": f"exchange positions={len(positions)} open chains={len(open_filled)}"})
    # 4. daily realised loss cap (balance based; only evaluated when flat so unrealised pnl is zero)
    day = day_key(now)
    ledger_today = [c for c in live if c.get("closed") and _f(c.get("pnl_usd")) is not None
                    and day_key(float(c.get("closed_at_ts") or c.get("exit_at_ts") or 0)) == day]
    ledger_loss = -sum(float(c["pnl_usd"]) for c in ledger_today)
    state["ledger_loss_usd"] = round(ledger_loss, 4)
    if ledger_loss >= DAILY_LOSS_CAP_USD:
        out.append({"code": "DAILY_LOSS_CAP", "detail": f"ledger realised loss ${ledger_loss:.2f} >= ${DAILY_LOSS_CAP_USD:.2f} ({len(ledger_today)} fresh-epoch rows today)"})
    bal = _f((m.get("balance") or {}).get("derivatives_available_usd"))
    flat = not positions and not orders and not open_filled
    if bal is not None and flat:
        if state.get("day") != day:
            state.update(day=day, day_baseline=bal)
        loss = float(state["day_baseline"]) - bal - float(funding_usd_today or 0.0) * 0.0
        state["day_loss_usd"] = round(loss, 4)
        if loss >= DAILY_LOSS_CAP_USD and not any(v["code"] == "DAILY_LOSS_CAP" for v in out):
            out.append({"code": "DAILY_LOSS_CAP", "detail": f"balance loss ${loss:.2f} >= ${DAILY_LOSS_CAP_USD:.2f} (baseline ${state['day_baseline']:.2f}, now ${bal:.2f})"})
        # 5. balance drift while flat vs the last flat reading, explained only by reported realised pnl + funding
        last = state.get("last_flat_balance")
        explained = sum(_f(c.get("pnl_usd")) or 0.0 for c in live if c.get("closed") and (c.get("closed_at_ts") or c.get("exit_at_ts") or 0) > state.get("last_flat_ts", now))
        all_reported = all(_f(c.get("pnl_usd")) is not None for c in live if c.get("closed") and (c.get("closed_at_ts") or c.get("exit_at_ts") or 0) > state.get("last_flat_ts", now))
        if last is not None and all_reported:
            drift = bal - float(last) - explained
            if abs(drift) > BALANCE_DRIFT_USD + abs(float(funding_usd_today or 0.0)):
                out.append({"code": "BALANCE_DRIFT", "detail": f"unexplained {drift:+.2f} USD while flat"})
        state["last_flat_balance"], state["last_flat_ts"] = bal, now
    return out
