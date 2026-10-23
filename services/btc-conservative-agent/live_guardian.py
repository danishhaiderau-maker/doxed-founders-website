"""Automatic stop-or-disarm guardian for EVERY live tile (HM 19 Oct for GS-01; generalised by the two-tile live gate,
24 Oct). Pure decision logic, no I/O.

All live-copy chains count (any tile); every violation carries the ``tile`` key it is attributed to (live_tile_gate),
or ``None`` when it is account-wide. The disarm semantics are unchanged: any violation disarms ALL tiles.

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
BALANCE_DRIFT_USD = 0.50        # unexplained balance change when flat beyond reported pnl; this $0.50 tolerance is the allowance for funding (the bot passes funding_usd_today=0)
ORPHAN_GRACE_SEC = 3.0          # an orphan must persist across this long (two reads) to count
LANE_NEEDLE = ""   # "" = every live tile (was "GS01" while GS-01 was the only live tile)
FILL_TO_STOP_MAX_SEC = 5.0     # HM G6: a stop confirmed later than 5 s after the fill is itself a violation


def _f(v):
    try:
        x = float(v)
        return x if x == x else None
    except (TypeError, ValueError):
        return None


ROLLING_WINDOW_SEC = 24 * 3600.0   # the loss cap and balance baseline use a rolling 24 h window (never a calendar-day reset mid-session)
FLAT_HIST_STEP_SEC = 30.0


def day_key(now: float) -> str:
    """Australia/Sydney calendar day (display/diagnostics only; the cap itself is a rolling 24 h window)."""
    try:
        from zoneinfo import ZoneInfo
        import datetime as _dt
        return _dt.datetime.fromtimestamp(float(now), ZoneInfo("Australia/Sydney")).strftime("%Y-%m-%d")
    except Exception:  # noqa: BLE001
        return time.strftime("%Y-%m-%d", time.gmtime(now))


HEARTBEAT_STALE_SEC = 10.0      # guardian loop must beat at least this often while live output is ON
PENDING_EXPOSURE_SEC = 300.0    # an order sent and not yet filled/closed counts as exposure for this long


def close_ts(c, now: float) -> float:
    """Close time of a closed row. A closed row with pnl but NO close time is counted as closed 'now' (conservative: it stays in
    the rolling loss cap instead of silently becoming timestamp 0)."""
    t = _f(c.get("closed_at_ts") or c.get("exit_at_ts"))
    return now if t is None or t <= 0 else t


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
    import live_tile_gate as _ltg
    tile_of = _ltg.chain_tile_key
    # 0. one live position account-wide (two-tile gate N1): two open live chains is a breach whatever their tiles
    if len(open_filled) > 1:
        out.append({"code": "MUTEX_BREACH", "tile": None,
                    "detail": f"{len(open_filled)} open live positions: " + ",".join(f"{tile_of(c)}:{c.get('correlation_id')}" for c in open_filled[:4])})
    # 0b. stop placement failed / stop confirmed later than 5 s after the fill (HM G6), on any open live chain
    for c in open_filled:
        if any((e or {}).get("type") == "STOP_FAILED" for e in c.get("errors") or []):
            out.append({"code": "STOP_PLACEMENT_FAILED", "tile": tile_of(c), "detail": f"{c.get('correlation_id')} STOP_FAILED reported"})
        sc, fa = _f(c.get("stop_confirmed_at_ts")), _f(c.get("fill_at_ts"))
        if sc is not None and fa is not None and sc - fa > FILL_TO_STOP_MAX_SEC:
            out.append({"code": "FILL_TO_STOP_GT_5S", "tile": tile_of(c), "detail": f"{c.get('correlation_id')} fill->stop {sc - fa:.1f}s"})
    # 1. stop missing > 5 s after fill
    for c in open_filled:
        age = now - float(c["fill_at_ts"])
        if not c.get("stop_confirmed") and age > STOP_MISSING_SEC:
            out.append({"code": "STOP_MISSING_GT_5S", "tile": tile_of(c), "detail": f"{c.get('correlation_id')} unprotected {age:.1f}s"})
    # 4a. realised loss cap from the fresh-epoch ledger: needs no exchange read, so it is evaluated BEFORE the blind-mirror return
    ledger_today = [c for c in live if c.get("closed") and _f(c.get("pnl_usd")) is not None
                    and now - ROLLING_WINDOW_SEC <= close_ts(c, now) <= now + 60]
    ledger_loss = -sum(float(c["pnl_usd"]) for c in ledger_today)
    state["ledger_loss_usd"] = round(ledger_loss, 4)
    if ledger_loss >= DAILY_LOSS_CAP_USD:
        out.append({"code": "DAILY_LOSS_CAP", "detail": f"ledger realised loss ${ledger_loss:.2f} >= ${DAILY_LOSS_CAP_USD:.2f} in the last 24 h ({len(ledger_today)} fresh-epoch rows)"})
    m = mirror if isinstance(mirror, Mapping) else None
    if m is None or m.get("ok") is not True:
        # a blind exchange read while live is open is itself unsafe, but a single failed read must not flap: require 30 s
        state["read_state"] = "BLIND"   # a failed/stale read is never a clean state
        if exposed:
            state.setdefault("blind_since", now)
            if now - state["blind_since"] > 30.0:
                out.append({"code": "EXCHANGE_MIRROR_BLIND", "detail": str((m or {}).get("error") or "no mirror")})
        else:
            state.pop("blind_since", None)   # nothing exposed: blindness is not accumulating towards a disarm
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
        out.append({"code": "LEDGER_MISMATCH", "tile": (tile_of(open_filled[0]) if len(open_filled) == 1 else None),
                    "detail": f"exchange positions={len(positions)} open chains={len(open_filled)}"})
    # 3b. reconcile by tile key (N2): a resting entry order whose cid carries tile T while no live chain of T is open/pending
    #     (any non-closed chain with a sent order counts, however old; the mismatch must persist LEDGER_GRACE_SEC so a report
    #     still in flight to Fly never trips it)
    live_tiles = {tile_of(c) for c in live if not c.get("closed") and (c.get("fill_at_ts") or c.get("order_sent_at_ts"))}
    bad = []
    for o in orders:
        t = _ltg.decode_cid((o or {}).get("cid"))
        if t is None or (o or {}).get("reduce_only") or "STOP" in str((o or {}).get("type") or "").upper():
            continue
        if t not in live_tiles:
            bad.append((o, t))
    if bad:
        first = state.setdefault("tile_mismatch_since", now)
        if now - first >= LEDGER_GRACE_SEC:
            for o, t in bad[:4]:
                out.append({"code": "TILE_KEY_MISMATCH", "tile": t,
                            "detail": f"exchange entry order {o.get('id')} cid tile={t} but live tiles with an order={sorted(x for x in live_tiles if x)}"})
    else:
        state.pop("tile_mismatch_since", None)
    bal = _f((m.get("balance") or {}).get("derivatives_available_usd"))
    flat = not positions and not orders and not open_filled
    if bal is not None and flat:
        # rolling 24 h baseline = the highest flat balance seen in the last 24 h (a legacy seeded day_baseline joins the history)
        hist = state.setdefault("flat_hist", [])
        if not hist and state.get("day_baseline") is not None:
            seed_ts = _f(state.get("day_baseline_ts"))
            if seed_ts is None:
                seed_ts = now - 60.0          # legacy in-process seed without a timestamp
            if now - ROLLING_WINDOW_SEC <= seed_ts <= now + 60:   # a baseline older than 24 h (e.g. after a long OFF gap) is dropped
                hist.append([seed_ts, float(state["day_baseline"])])
        hist[:] = [h for h in hist if now - ROLLING_WINDOW_SEC <= h[0] <= now + 60]
        if not hist or now - hist[-1][0] >= FLAT_HIST_STEP_SEC:
            hist.append([now, bal])
        del hist[:-3000]
        peak_ts, peak = max([(h[0], h[1]) for h in hist] + [(now, bal)], key=lambda x: (x[1], x[0]))
        state["day_baseline"], state["day_baseline_ts"] = peak, peak_ts   # ts = when the peak was seen, so a later seed ages correctly
        state["sydney_day"] = day_key(now)
        loss = float(state["day_baseline"]) - bal   # balance already includes funding: a funding debit is a real loss
        state["day_loss_usd"] = round(loss, 4)
        if loss >= DAILY_LOSS_CAP_USD and not any(v["code"] == "DAILY_LOSS_CAP" for v in out):
            out.append({"code": "DAILY_LOSS_CAP", "detail": f"balance loss ${loss:.2f} >= ${DAILY_LOSS_CAP_USD:.2f} (baseline ${state['day_baseline']:.2f}, now ${bal:.2f})"})
        # 5. balance drift while flat vs the last flat reading, explained only by reported realised pnl + funding
        last = state.get("last_flat_balance")
        # drift check: ONLY rows with a real close time (a time-less row would be 'newer' on every tick; it stays in the loss cap via close_ts)
        since = [c for c in live if c.get("closed") and (_f(c.get("closed_at_ts") or c.get("exit_at_ts")) or 0) > state.get("last_flat_ts", now)]
        explained = sum(_f(c.get("pnl_usd")) or 0.0 for c in since)
        all_reported = all(_f(c.get("pnl_usd")) is not None for c in since)
        if last is not None and all_reported:
            drift = bal - float(last) - explained
            if abs(drift) > BALANCE_DRIFT_USD + abs(float(funding_usd_today or 0.0)):
                out.append({"code": "BALANCE_DRIFT", "detail": f"unexplained {drift:+.2f} USD while flat"})
        state["last_flat_balance"], state["last_flat_ts"] = bal, now
    return out
