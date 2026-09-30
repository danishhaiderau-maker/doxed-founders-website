"""Owner-facing Decision view: one honest row per registered tile.

Every number on the Decision page is produced here from one function per
metric, so the Decision page, the lanes API and any other panel that imports
these helpers can never disagree about the same metric.  Missing, uncollected
or stale inputs are explicit states and are never rendered as zero.
"""
from __future__ import annotations

import html
import math
from datetime import datetime, timezone

MIN_DECISION_SAMPLE = 30
NO_DATA_TEXT = "no data yet"
NOT_ENOUGH_DATA_TEXT = f"NOT ENOUGH DATA (n<{MIN_DECISION_SAMPLE})"

VALUE = "VALUE"
NO_DATA = "NO_DATA"
STALE = "STALE"
INSUFFICIENT = "INSUFFICIENT"

LOCAL_DISK_ALARM_PCT = 85.0
LOCAL_WAL_ALARM_BYTES = 256 * 1024 * 1024

SEGMENT_PULLER_MAX_AGE_SEC = 15 * 60
SEGMENT_ACK_MAX_AGE_SEC = 15 * 60
FLY_SEGMENT_HEAD_MAX_AGE_SEC = 15 * 60
SEGMENT_PARITY_MAX_AGE_SEC = 90 * 60
SEGMENT_UNSHIPPED_ALARM_BYTES = 32 * 1024 * 1024
SEGMENT_ACK_BEHIND_ALARM_SEQ = 30

# Codes produced only by the retired whole-generation mirror (ACK watcher).
# A stale monitor state file must not resurrect them.
RETIRED_TRANSFER_ALARM_CODES = frozenset({
    "SYNC_FAIL_REPEATED", "WATCHER_DEAD",
    "TRANSFER_ACK_LAG", "TRANSFER_SYNC_FAILING", "TRANSFER_NO_ACK", "TRANSFER_STATUS_UNAVAILABLE",
})


def _finite(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _count(value):
    number = _finite(value)
    return None if number is None or number < 0 else int(number)


def metric(value, *, reason: str | None = None, stale_since: str | None = None) -> dict:
    """A display cell: a real value, an explicit NO_DATA, or a STALE value."""
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        return {"state": NO_DATA, "value": None, "reason": reason or "not collected yet"}
    if stale_since:
        return {"state": STALE, "value": value, "stale_since": stale_since}
    return {"state": VALUE, "value": value}


def render_metric(cell: dict | None, fmt=None, *, stale_marker: str | None = None) -> str:
    """Text for a cell.  Absent data is never rendered as 0.

    ``stale_marker`` replaces the per-cell "stale since" suffix on pages that
    already state the stale time once in a banner.
    """
    fmt = fmt or (lambda value: str(value))
    if not isinstance(cell, dict) or cell.get("state") == NO_DATA:
        reason = (cell or {}).get("reason") if isinstance(cell, dict) else None
        return f"{NO_DATA_TEXT} ({reason})" if reason else NO_DATA_TEXT
    if cell.get("state") == INSUFFICIENT:
        return NOT_ENOUGH_DATA_TEXT
    text = fmt(cell.get("value"))
    if cell.get("state") == STALE:
        return f"{text} {stale_marker}".rstrip() if stale_marker is not None else f"{text} · stale since {cell.get('stale_since')}"
    return text


def _usd(value) -> str:
    return f"${value:+.3f}"


def _pct(value) -> str:
    return f"{value:.1f}%"


def execution_funnel_metrics(funnel_lane: dict | None, *, stale_since: str | None = None) -> dict:
    """Single source for closes, fills, fill rate and NO_FILL of one tile.

    Fill rate is fills per submitted order.  ``not_filled`` counts submitted
    orders without a fill in this generation (true NO_FILL or still resting).
    """
    lane = funnel_lane if isinstance(funnel_lane, dict) else None
    if lane is None:
        missing = metric(None, reason="tile absent from analyzer funnel report")
        return {key: dict(missing) for key in (
            "approve", "order_submitted", "filled", "closed", "fill_rate_pct",
            "approve_to_fill_pct", "not_filled")}
    approve = _count(lane.get("approve"))
    submitted = _count(lane.get("order_submitted"))
    filled = _count(lane.get("filled"))
    closed = _count(lane.get("closed"))

    def ratio(numerator, denominator, empty_reason):
        if numerator is None or denominator is None:
            return metric(None, reason="counter not published")
        if denominator == 0:
            return metric(None, reason=empty_reason)
        return metric(round(100.0 * numerator / denominator, 1), stale_since=stale_since)

    not_filled = (
        metric(max(0, submitted - filled), stale_since=stale_since)
        if submitted is not None and filled is not None
        else metric(None, reason="counter not published")
    )
    return {
        "approve": metric(approve, reason="counter not published", stale_since=stale_since),
        "order_submitted": metric(submitted, reason="counter not published", stale_since=stale_since),
        "filled": metric(filled, reason="counter not published", stale_since=stale_since),
        "closed": metric(closed, reason="counter not published", stale_since=stale_since),
        "fill_rate_pct": ratio(filled, submitted, "no orders submitted yet"),
        "approve_to_fill_pct": ratio(filled, approve, "no approvals yet"),
        "not_filled": not_filled,
    }


def after_cost_ev(funnel_lane: dict | None, *, stale_since: str | None = None) -> dict:
    """Net PnL per closed trade (fees/funding as recorded) with a confidence note."""
    lane = funnel_lane if isinstance(funnel_lane, dict) else {}
    stats = lane.get("closed_trade_stats") if isinstance(lane.get("closed_trade_stats"), dict) else {}
    n = _count(stats.get("n")) if stats else None
    forced = _count(stats.get("forced_exits_excluded")) if stats else None
    n_source = (f"strategy exits ({forced} deploy/operator forced exits excluded from EV)"
                if forced else "trade log rows")
    if n is None:
        n = _count(lane.get("closed"))
        n_source = "CLOSED lifecycle events"
    mean = _finite(stats.get("mean_net_pnl_usd")) if stats else None
    stdev = _finite(stats.get("stdev_net_pnl_usd")) if stats else None
    if mean is None and n:
        total = _finite(lane.get("net_pnl_usd"))
        mean = None if total is None else total / n
    result = {"n": n, "n_source": n_source if n is not None else None, "mean_usd": mean, "ci95_usd": None}
    if n is None or n == 0:
        result.update(cell=metric(None, reason="no closed trades yet"),
                      note="no closed trades in this generation")
        return result
    if n < MIN_DECISION_SAMPLE:
        descriptive = "" if mean is None else f"; descriptive mean {_usd(mean)} per close"
        result.update(cell={"state": INSUFFICIENT, "value": mean, "n": n},
                      note=f"{NOT_ENOUGH_DATA_TEXT}: n={n}{descriptive}, not a ranking")
        return result
    if mean is None:
        result.update(cell=metric(None, reason="net PnL not published"),
                      note="net PnL not published for this tile")
        return result
    if stdev is None or n < 2:
        result.update(cell=metric(mean, stale_since=stale_since),
                      note="no confidence interval yet (spread not published by this generation)")
        return result
    half = 1.96 * stdev / math.sqrt(n)
    low, high = mean - half, mean + half
    result["ci95_usd"] = [low, high]
    if low > 0:
        note = f"95% CI {_usd(low)} to {_usd(high)}: above zero"
    elif high < 0:
        note = f"95% CI {_usd(low)} to {_usd(high)}: below zero"
    else:
        note = f"95% CI {_usd(low)} to {_usd(high)}: spans zero, not distinguishable from zero"
    result.update(cell=metric(mean, stale_since=stale_since), note=note)
    return result


def mae_mfe_metrics(funnel_lane: dict | None, *, stale_since: str | None = None) -> dict:
    lane = funnel_lane if isinstance(funnel_lane, dict) else {}
    stats = lane.get("closed_trade_stats") if isinstance(lane.get("closed_trade_stats"), dict) else None
    if not stats:
        reason = "per-tile MAE/MFE is published from the next analyzer generation"
        return {"mae": metric(None, reason=reason), "mfe": metric(None, reason=reason), "rows": None}
    rows = _count(stats.get("mae_mfe_rows"))
    if not rows:
        reason = "no closed trades with MAE/MFE recorded"
        return {"mae": metric(None, reason=reason), "mfe": metric(None, reason=reason), "rows": rows}
    return {
        "mae": metric(_finite(stats.get("median_mae_margin_pct")), reason="MAE not recorded", stale_since=stale_since),
        "mfe": metric(_finite(stats.get("median_mfe_margin_pct")), reason="MFE not recorded", stale_since=stale_since),
        "rows": rows,
    }


def ai_vs_rules(tile: dict | None, funnel_lane: dict | None, ai_coverage: dict | None) -> dict:
    """AI-usefulness versus the rules baseline, or the exact reason it is unknown."""
    tile = tile if isinstance(tile, dict) else {}
    comparison = (ai_coverage or {}).get("matched_selection_comparison") if isinstance(ai_coverage, dict) else None
    policy_ids = {str(tile.get(key) or "") for key in ("raw_policy_id", "analyzer_cohort", "combo_key")} - {""}
    if isinstance(comparison, dict):
        for group in comparison.get("groups") or []:
            if not isinstance(group, dict) or str(group.get("policy_id") or "") not in policy_ids:
                continue
            delta = _finite(group.get("incremental_net_pnl_usd"))
            matched = _count(group.get("matched_rows") or group.get("n"))
            if delta is None or not matched:
                break
            if matched < MIN_DECISION_SAMPLE:
                return {"cell": {"state": INSUFFICIENT, "value": delta},
                        "note": f"{NOT_ENOUGH_DATA_TEXT}: {matched} matched AI-vs-rules outcomes"}
            return {"cell": metric(delta), "note": f"AI minus rules, {matched} matched outcomes"}
    ai_calls = _count(funnel_lane.get("ai_calls")) if isinstance(funnel_lane, dict) else None
    tile_split = funnel_lane.get("ai_vs_rules") if isinstance(funnel_lane, dict) else None
    if isinstance(tile_split, dict) and tile_split.get("status") == "DESCRIPTIVE_ONLY":
        delta = _finite(tile_split.get("incremental_net_pnl_usd"))
        matched = _count(tile_split.get("matched_trades"))
        if delta is not None and matched:
            approved = _count(tile_split.get("approved_same_direction_trades")) or 0
            rejected = _count(tile_split.get("rejected_trades")) or 0
            detail = (f"{ai_calls or 0} AI calls; {matched} closed trades: "
                      f"{approved} AI-approved, {rejected} AI-rejected")
            # The comparison is only as strong as its smaller arm.
            if min(approved, rejected) < MIN_DECISION_SAMPLE:
                return {"cell": {"state": INSUFFICIENT, "value": delta},
                        "note": f"{NOT_ENOUGH_DATA_TEXT}: AI-filtered minus rules-only, {detail}"}
            return {"cell": metric(delta), "note": f"AI-filtered minus rules-only, {detail}"}
    blockers = (
        ", ".join(str(b) for b in (comparison.get("blockers") or [])[:2])
        if isinstance(comparison, dict) else ""
    )
    if ai_calls == 0:
        reason = "no AI calls attributed to this tile in this generation"
    elif blockers:
        reason = blockers
    elif comparison is None:
        reason = "AI-vs-rules comparison not published"
    else:
        reason = "no matched AI-vs-rules outcomes for this tile"
    return {"cell": metric(None, reason=reason), "note": reason}


def tile_verdict(*, has_generation: bool, ev: dict, stale_since: str | None) -> dict:
    n = ev.get("n")
    if not has_generation:
        code, text = "NO_DATA", "NO DATA YET: the analyzer has not published a generation"
    elif not n:
        code, text = "NO_DATA", "NO DATA YET: no closed trades for this tile"
    elif n < MIN_DECISION_SAMPLE:
        code, text = "NOT_ENOUGH_DATA", f"{NOT_ENOUGH_DATA_TEXT}: {n} closed, keep collecting (no ranking)"
    elif ev.get("ci95_usd"):
        low, high = ev["ci95_usd"]
        if low > 0:
            code, text = "POSITIVE", "Positive after-cost EV: 95% CI above zero"
        elif high < 0:
            code, text = "NEGATIVE", "Negative after-cost EV: 95% CI below zero"
        else:
            code, text = "INCONCLUSIVE", "Inconclusive: after-cost EV not distinguishable from zero"
    else:
        code, text = "INCONCLUSIVE", "Inconclusive: no confidence interval yet"
    if stale_since and has_generation:
        text += f" (stale since {stale_since})"
    return {"code": code, "text": text}


def lane_evidence_status(n, *, is_benchmark: bool = False, is_retired: bool = False) -> str | None:
    """Status label for panels that would otherwise rank tiles on thin samples."""
    if is_benchmark:
        return "BENCHMARK"
    if is_retired:
        return "HISTORICAL"
    count = _count(n)
    if not count:
        return "NO DATA YET"
    if count < MIN_DECISION_SAMPLE:
        return NOT_ENOUGH_DATA_TEXT
    return None


def _parse_ts(value):
    if value in (None, ""):
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def freshness_text(ts, *, max_age_sec: float, now: datetime, display=None, missing: str = NO_DATA_TEXT) -> str:
    """'<time>' when fresh, 'stale since <time>' when older than max_age_sec, else missing."""
    parsed = _parse_ts(ts)
    if parsed is None:
        return missing
    shown = display(ts) if display else parsed.isoformat()
    if (now - parsed).total_seconds() > max_age_sec:
        return f"stale since {shown}"
    return shown


def _age_sec(ts, now: datetime):
    parsed = _parse_ts(ts)
    return None if parsed is None else (now - parsed).total_seconds()


def _fly_head_observed(fly_head: dict | None, now: datetime) -> dict | None:
    """The laptop's last Fly segment-head observation, or None when unusable."""
    head = fly_head if isinstance(fly_head, dict) and fly_head.get("schema") == "fly_segment_head_snapshot_v1" else None
    if not head or not head.get("ok"):
        return None
    age = _age_sec(head.get("observedAt"), now)
    return head if age is not None and age <= FLY_SEGMENT_HEAD_MAX_AGE_SEC else None


def collect_alarms(*, freshness: dict | None, analyzer_run: dict | None, monitor_state: dict | None,
                   segment_status: dict | None, fly_segment_head: dict | None, segment_parity: dict | None,
                   local_disk: dict | None, local_wal: list | None, now: datetime) -> list:
    """Every v2 segment transfer / freshness / disk / WAL alarm as a visible row."""
    alarms: dict[str, dict] = {}

    def add(code, severity, detail, since=None):
        alarms.setdefault(code, {"code": code, "severity": severity, "detail": str(detail)[:400], "since": since})

    for alert in (monitor_state or {}).get("alerts") or []:
        if isinstance(alert, dict) and alert.get("code") and alert["code"] not in RETIRED_TRANSFER_ALARM_CODES:
            add(str(alert["code"]), str(alert.get("severity") or "warning"), alert.get("detail") or "", alert.get("openedAt"))
    run = analyzer_run or {}
    if str(run.get("state") or "").upper() in {"FAILED", "TIMEOUT"}:
        add("ANALYZER_RUN_FAILED", "critical", run.get("detail") or run.get("state"), run.get("finishedAt"))
    fresh = freshness or {}
    if fresh and fresh.get("current") is not True:
        add("ANALYZER_GENERATION_STALE", "critical",
            "; ".join(str(r) for r in fresh.get("reasons") or []) or "generation not current")

    puller = segment_status if isinstance(segment_status, dict) else None
    if not puller:
        add("SEGMENT_PULLER_NO_DATA", "critical", "v2 segment puller has not written a status on this laptop")
    else:
        age = _age_sec(puller.get("updated_at"), now)
        if age is None or age > SEGMENT_PULLER_MAX_AGE_SEC:
            add("SEGMENT_PULLER_STALE", "critical",
                f"v2 segment puller status last updated {puller.get('updated_at') or 'never'}; "
                f"applied seq {puller.get('applied_seq')}", puller.get("updated_at"))
        if puller.get("last_error"):
            add("SEGMENT_PULLER_ERROR", "critical", puller.get("last_error"), puller.get("updated_at"))
        receipt = puller.get("ack_receipt") if isinstance(puller.get("ack_receipt"), dict) else {}
        ack_age = _age_sec(receipt.get("received_at"), now)
        if not receipt:
            add("SEGMENT_ACK_NO_DATA", "critical", "Fly has not recorded a v2 ACK from this laptop")
        elif receipt.get("ok") is not True:
            add("SEGMENT_ACK_REJECTED", "critical",
                f"Fly answered the v2 ACK through seq {receipt.get('through_seq')} with {receipt.get('result')}",
                receipt.get("received_at"))
        elif ack_age is None or ack_age > SEGMENT_ACK_MAX_AGE_SEC:
            add("SEGMENT_ACK_STALE", "critical",
                f"last v2 ACK accepted by Fly through seq {receipt.get('through_seq')} at {receipt.get('received_at')}",
                receipt.get("received_at"))

    head = _fly_head_observed(fly_segment_head, now)
    if head is None:
        observed = (fly_segment_head or {}).get("observedAt") if isinstance(fly_segment_head, dict) else None
        add("FLY_SEGMENT_HEAD_NO_DATA", "warning",
            "no current Fly v2 shipper observation on this laptop"
            + (f" (last {observed})" if observed else ""), observed)
    else:
        if head.get("last_error"):
            add("FLY_SEGMENT_SHIPPER_ERROR", "critical", head.get("last_error"), head.get("observedAt"))
        if head.get("segments_enabled") is False:
            add("FLY_SEGMENT_SHIPPER_DISABLED", "critical", "Fly reports research segment shipping disabled")
        unshipped = _count(head.get("unshipped_bytes"))
        if unshipped is not None and unshipped > SEGMENT_UNSHIPPED_ALARM_BYTES:
            add("SEGMENT_UNSHIPPED_BACKLOG", "warning",
                f"Fly holds {unshipped / 1048576:.1f} MB of unshipped v2 research bytes", head.get("observedAt"))
        shipped = _count(head.get("shipped_seq"))
        acked = _count(head.get("laptop_acked_seq"))
        if shipped is not None and acked is not None and shipped - acked > SEGMENT_ACK_BEHIND_ALARM_SEQ:
            add("SEGMENT_ACK_BEHIND", "warning",
                f"Fly published v2 seq {shipped}; laptop ACKed {acked} ({shipped - acked} behind)",
                head.get("observedAt"))

    parity = segment_parity if isinstance(segment_parity, dict) else None
    if not parity:
        add("SEGMENT_PARITY_NO_DATA", "warning", "no v2 checkpoint parity receipt on this laptop")
    else:
        if str(parity.get("verdict") or "").upper() != "GREEN":
            add("SEGMENT_PARITY_NOT_GREEN", "critical",
                f"v2 checkpoint parity {parity.get('verdict') or 'UNKNOWN'} at seq {parity.get('seq')}; "
                f"counts {parity.get('counts')}", parity.get("generated_at"))
        parity_age = _age_sec(parity.get("generated_at"), now)
        if parity_age is None or parity_age > SEGMENT_PARITY_MAX_AGE_SEC:
            add("SEGMENT_PARITY_STALE", "warning",
                f"last v2 checkpoint parity at {parity.get('generated_at') or 'unknown'}", parity.get("generated_at"))

    disk = local_disk or {}
    used_pct = _finite(disk.get("used_pct"))
    if used_pct is not None and used_pct >= LOCAL_DISK_ALARM_PCT:
        add("LOCAL_DISK_PRESSURE", "critical", f"canonical data drive {used_pct:.1f}% used")
    for wal in local_wal or []:
        if isinstance(wal, dict) and (_count(wal.get("bytes")) or 0) >= LOCAL_WAL_ALARM_BYTES:
            add("LOCAL_SQLITE_WAL_LARGE", "warning", f"{wal.get('name')} WAL is {wal['bytes'] / 1048576:.0f} MB")
    order = {"critical": 0, "warning": 1, "info": 2}
    return sorted(alarms.values(), key=lambda a: (order.get(a["severity"], 3), a["code"]))


def segment_freshness_rows(*, segment_status: dict | None, fly_segment_head: dict | None,
                           segment_parity: dict | None, promotion: dict | None, now: datetime,
                           display=None) -> list:
    """Data-freshness rows for the v2 segment transfer, laptop side and Fly side."""
    def fresh(ts, max_age):
        return freshness_text(ts, max_age_sec=max_age, now=now, display=display)

    puller = segment_status if isinstance(segment_status, dict) else {}
    receipt = puller.get("ack_receipt") if isinstance(puller.get("ack_receipt"), dict) else {}
    if puller:
        applied = f"seq {puller.get('applied_seq')} · {fresh(puller.get('updated_at'), SEGMENT_PULLER_MAX_AGE_SEC)}"
    else:
        applied = f"{NO_DATA_TEXT} (v2 segment puller has not run on this laptop)"
    if receipt:
        ack = (f"seq {receipt.get('through_seq')} · {fresh(receipt.get('received_at'), SEGMENT_ACK_MAX_AGE_SEC)}"
               + ("" if receipt.get("ok") is True else f" · REJECTED ({receipt.get('result')})"))
    else:
        ack = f"{NO_DATA_TEXT} (no v2 ACK recorded by Fly)"
    head = fly_segment_head if isinstance(fly_segment_head, dict) and fly_segment_head.get("schema") == "fly_segment_head_snapshot_v1" else None
    if not head:
        shipper = f"{NO_DATA_TEXT} (Fly shipper not observed from this laptop)"
    elif not head.get("ok"):
        shipper = f"{NO_DATA_TEXT} (last Fly /health read failed: {head.get('error') or 'unknown'})"
    else:
        unshipped = _count(head.get("unshipped_bytes"))
        shipper = (f"published seq {head.get('shipped_seq')} · laptop ACKed {head.get('laptop_acked_seq')} · "
                   f"unshipped {(f'{unshipped / 1048576:.1f} MB') if unshipped is not None else NO_DATA_TEXT} · "
                   f"observed {fresh(head.get('observedAt'), FLY_SEGMENT_HEAD_MAX_AGE_SEC)}")
    parity = segment_parity if isinstance(segment_parity, dict) else None
    parity_text = (f"{parity.get('verdict') or 'UNKNOWN'} at seq {parity.get('seq')} · "
                   f"{fresh(parity.get('generated_at'), SEGMENT_PARITY_MAX_AGE_SEC)}"
                   if parity else f"{NO_DATA_TEXT} (no v2 parity receipt)")
    promo = promotion if isinstance(promotion, dict) and promotion.get("segmentPrefix") else None
    promo_text = (f"{promo.get('segmentPrefix')} seq {promo.get('segmentAppliedSeq')} promoted "
                  f"{display(promo.get('syncedAt')) if display and promo.get('syncedAt') else promo.get('syncedAt')}"
                  if promo else f"{NO_DATA_TEXT} (analyzer store not promoted from v2 segments)")
    return [
        {"label": "Last v2 segment applied on laptop", "text": applied},
        {"label": "Last v2 ACK accepted by Fly", "text": ack},
        {"label": "Fly v2 shipper", "text": shipper},
        {"label": "v2 checkpoint parity", "text": parity_text},
        {"label": "Analyzer store promoted from", "text": promo_text},
    ]


def trade_count_reconciliation(funnel_report: dict | None, summary_trades, tile_total) -> dict:
    """Explain the Details 'session trades' count against this page's per-tile total."""
    scope = funnel_report.get("trade_scope") if isinstance(funnel_report, dict) else None
    summary = _count(summary_trades)
    if isinstance(scope, dict) and _count(scope.get("session_trade_rows")) is not None:
        session = _count(scope.get("session_trade_rows"))
        tile = _count(scope.get("tile_trade_rows")) or 0
        others = scope.get("non_tile_trade_rows") if isinstance(scope.get("non_tile_trade_rows"), dict) else {}
        other_text = ", ".join(f"{lane} {n}" for lane, n in others.items()) or "none"
        text = (f"The Details summary trade count ({session}) = {tile} tile trades + {session - tile} "
                f"non-tile trades ({other_text}).")
        consistent = summary is None or summary == session
        if not consistent:
            text += f" MISMATCH: the executive summary reports {summary} for the same generation."
        return {"available": True, "consistent": consistent, "session": session, "tile": tile,
                "non_tile": others, "text": text}
    if summary is None and tile_total is None:
        return {"available": False, "consistent": None, "text": NO_DATA_TEXT}
    shown_summary = summary if summary is not None else NO_DATA_TEXT
    shown_tiles = tile_total if tile_total is not None else NO_DATA_TEXT
    return {
        "available": False, "consistent": None,
        "text": (f"The Details summary trade count ({shown_summary}) counts every session trade row, including "
                 f"non-tile lanes; the per-tile total here is {shown_tiles} (CLOSED lifecycle events). "
                 "The lane-by-lane reconciliation is published from the next analyzer generation."),
    }


RELAY_STATUS_MAX_AGE_SEC = 15 * 60


def relay_state_view(snapshot: dict | None, *, registry: dict, now: datetime) -> dict:
    """Read-only Bitfinex relay state from the laptop's ops relay-status snapshot.

    The allowlist comes from the canonical registry, so it shows even without a
    snapshot. Every platform value is NO_DATA or STALE when the snapshot is
    missing, failed or old; nothing is ever inferred as flat or disarmed.
    """
    eligible = [lane for lane, spec in (registry or {}).items() if spec.get("platform_relay_eligible") is True]
    snap = snapshot if isinstance(snapshot, dict) and snapshot.get("schema") == "relay_status_snapshot_v1" else None
    observed = _parse_ts((snap or {}).get("observedAt"))
    age = (now - observed).total_seconds() if observed else None
    stale_since = (snap or {}).get("observedAt") if age is not None and age > RELAY_STATUS_MAX_AGE_SEC else None
    if not snap:
        reason = "relay status snapshot not collected on this laptop"
    elif not snap.get("ok"):
        reason = f"last relay status fetch failed ({snap.get('error') or 'unknown'})"
    else:
        reason = None

    def cell(value):
        if reason:
            return metric(None, reason=reason)
        return metric(value, reason="not reported by the platform", stale_since=stale_since)

    ok = reason is None

    def section(key):
        value = (snap or {}).get(key) if ok else None
        return value if isinstance(value, dict) else {}

    recon, audit, executor = section("reconciliation"), section("exchangeOrderAudit"), section("relayExecutor")
    armed_at = (snap or {}).get("relayArmedAt") if ok else None
    if not ok:
        armed_text = None
    elif armed_at:
        armed_text = f"ARMED since {armed_at}"
    else:
        armed_text = f"DISARMED ({(snap or {}).get('relayExecutionMode') or (snap or {}).get('status') or 'mode not reported'})"
    heartbeat = None
    if executor.get("status"):
        heartbeat = f"{executor.get('status')} ({'healthy' if executor.get('healthy') else 'unhealthy'})"
        if executor.get("observedAt"):
            heartbeat += f" at {executor.get('observedAt')}"
    return {
        "observed_at": (snap or {}).get("observedAt"),
        "allowlist": eligible,
        "allowlist_text": ", ".join(eligible) if eligible else "empty (no tile may copy to Bitfinex)",
        "armed": cell(armed_text),
        "executor_heartbeat": cell(heartbeat),
        "exchange_position_btc": cell(recon.get("signedExchangePositionQty")),
        "ledger_open_btc": cell(recon.get("signedLedgerOpenQty")),
        "active_orders": cell(audit.get("activeOrderCount") if audit.get("known") else None),
        "reconciled_at": cell(recon.get("updatedAt")),
    }


def build_decision_payload(*, tile_order, registry: dict, funnel_report: dict | None,
                           ai_coverage: dict | None, generation: dict, alarms: list,
                           freshness_rows: list, summary_trades=None,
                           relay_state: dict | None = None) -> dict:
    has_generation = bool(generation.get("generated_at")) and isinstance(funnel_report, dict)
    stale_since = None if generation.get("current") else generation.get("generated_at_display") or generation.get("generated_at")
    funnel_lanes = (funnel_report or {}).get("lanes") if isinstance(funnel_report, dict) else None
    funnel_lanes = funnel_lanes if isinstance(funnel_lanes, dict) else {}
    tiles = []
    for lane in tile_order:
        tile = registry.get(lane) or {}
        funnel_lane = funnel_lanes.get(lane)
        funnel = execution_funnel_metrics(funnel_lane, stale_since=stale_since)
        ev = after_cost_ev(funnel_lane, stale_since=stale_since)
        extremes = mae_mfe_metrics(funnel_lane, stale_since=stale_since)
        ai = ai_vs_rules(tile, funnel_lane, ai_coverage)
        tiles.append({
            "lane": lane,
            "label": tile.get("label") or lane,
            "admission": tile.get("admission_treatment"),
            "sample": metric(ev["n"], reason="no closed trades yet", stale_since=stale_since),
            "ev": ev,
            "funnel": funnel,
            "mae_mfe": extremes,
            "ai_vs_rules": ai,
            "verdict": tile_verdict(has_generation=has_generation, ev=ev, stale_since=stale_since),
        })
    tile_counts = [t["ev"]["n"] for t in tiles if t["ev"]["n"] is not None]
    return {
        "schema": "analyzer_decision_view_v1",
        "min_sample": MIN_DECISION_SAMPLE,
        "generation": generation,
        "alarms": alarms,
        "freshness": freshness_rows,
        "tiles": tiles,
        "trade_counts": trade_count_reconciliation(
            funnel_report, summary_trades, sum(tile_counts) if tile_counts else None),
        "relay_state": relay_state,
    }


def _esc(value) -> str:
    return html.escape(str(value), quote=True)


_VERDICT_COLOURS = {"POSITIVE": "#3fb950", "NEGATIVE": "#f85149", "INCONCLUSIVE": "#d29922",
                    "NOT_ENOUGH_DATA": "#8b949e", "NO_DATA": "#8b949e"}
_SEVERITY_COLOURS = {"critical": "#f85149", "warning": "#d29922", "info": "#8b949e"}


def render_decision_html(payload: dict, *, nav_links, details_href: str = "/details") -> str:
    gen = payload.get("generation") or {}
    stale = not gen.get("current")
    shown_at = gen.get("generated_at_display") or gen.get("generated_at")
    head = f"Generation {shown_at or NO_DATA_TEXT}" + (f" · epoch {gen.get('epoch_id')}" if gen.get("epoch_id") else "")
    banner_colour = "#f85149" if stale else "#3fb950"
    if not shown_at:
        banner = "NO DATA YET: the analyzer has not published a generation"
    elif stale:
        banner = "STALE: numbers below are from the last completed analyzer generation, stale since " + _esc(shown_at)
    else:
        banner = "CURRENT generation"
    if gen.get("registry_error"):
        banner = ("REGISTRY UNAVAILABLE: the tile roster cannot be verified ("
                  + _esc(gen["registry_error"]) + "); " + banner)
    def cell(value, fmt=None):
        return _esc(render_metric(value, fmt, stale_marker="(stale)"))

    rows = []
    for tile in payload.get("tiles") or []:
        funnel, ev, mm, ai = tile["funnel"], tile["ev"], tile["mae_mfe"], tile["ai_vs_rules"]
        if mm["mae"]["state"] == NO_DATA and mm["mae"] == mm["mfe"]:
            extremes = cell(mm["mae"])
        else:
            extremes = f"MAE {cell(mm['mae'], _pct)}<br>MFE {cell(mm['mfe'], _pct)}"
        ai_note = ("" if ai["cell"].get("state") == NO_DATA
                   else f"<div class='sub'>{_esc(ai.get('note') or '')}</div>")
        verdict = tile["verdict"]
        colour = _VERDICT_COLOURS.get(verdict["code"], "#8b949e")
        rows.append(
            "<tr>"
            f"<td><strong>{_esc(tile['label'])}</strong><div class='sub'>{_esc(tile['lane'])}</div></td>"
            f"<td>{cell(tile['sample'])}<div class='sub'>{_esc(ev.get('n_source') or '')}</div></td>"
            f"<td>{cell(ev['cell'], _usd)}<div class='sub'>{_esc(ev.get('note') or '')}</div></td>"
            f"<td>{cell(funnel['fill_rate_pct'], _pct)}"
            f"<div class='sub'>fills {_esc(render_metric(funnel['filled'], stale_marker=''))} of "
            f"{_esc(render_metric(funnel['order_submitted'], stale_marker=''))} orders</div></td>"
            f"<td>{cell(funnel['not_filled'])}"
            "<div class='sub'>submitted, no fill (NO_FILL or still resting)</div></td>"
            f"<td>{extremes}</td>"
            f"<td>{cell(ai['cell'], _usd)}{ai_note}</td>"
            "</tr>"
            f"<tr class='verdict'><td colspan='7' style='color:{colour}'>Verdict: {_esc(verdict['text'])}</td></tr>"
        )
    alarm_html = "".join(
        f"<li style='color:{_SEVERITY_COLOURS.get(a['severity'], '#8b949e')}'>"
        f"<strong>{_esc(a['severity'].upper())} {_esc(a['code'])}</strong>"
        + (f" · since {_esc(a['since'])}" if a.get("since") else "")
        + f"<div class='sub'>{_esc(a['detail'])}</div></li>"
        for a in payload.get("alarms") or []
    ) or "<li style='color:#3fb950'>No transfer, freshness, disk or WAL alarms.</li>"
    relay = payload.get("relay_state") or {}
    relay_rows = [
        ("Relay", relay.get("armed")),
        ("Allowlist (registry)", relay.get("allowlist_text") or NO_DATA_TEXT),
        ("Executor heartbeat", relay.get("executor_heartbeat")),
        ("Exchange position (BTC)", relay.get("exchange_position_btc")),
        ("Ledger open (BTC)", relay.get("ledger_open_btc")),
        ("Active exchange orders", relay.get("active_orders")),
        ("Last reconciliation", relay.get("reconciled_at")),
    ]
    relay_html = "".join(
        f"<tr><td>{_esc(label)}</td><td>{_esc(value if isinstance(value, str) else render_metric(value))}</td></tr>"
        for label, value in relay_rows
    )
    fresh_html = "".join(
        f"<tr><td>{_esc(row['label'])}</td><td>{_esc(row['text'])}</td></tr>"
        for row in payload.get("freshness") or []
    )
    nav = " · ".join(f"<a href=\"{_esc(href)}\">{_esc(label)}</a>" for label, href in nav_links)
    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="180">
<title>Research Dashboard · Decision</title>
<style>
body{{background:#0d1117;color:#c9d1d9;font-family:-apple-system,Segoe UI,Arial,sans-serif;margin:0;padding:16px 20px;}}
h1{{font-size:1.35rem;margin:0 0 4px;}} h2{{font-size:1.05rem;margin:22px 0 8px;color:#58a6ff;}}
a{{color:#58a6ff;}} .sub{{color:#8b949e;font-size:.78rem;margin-top:2px;}}
.banner{{border:2px solid {banner_colour};color:{banner_colour};padding:10px 14px;border-radius:8px;font-weight:700;margin:10px 0;}}
table{{border-collapse:collapse;width:100%;font-size:.88rem;}}
th,td{{border-bottom:1px solid #21262d;padding:7px 8px;text-align:left;vertical-align:top;}}
th{{color:#8b949e;font-weight:600;font-size:.78rem;text-transform:uppercase;}}
tr.verdict td{{border-bottom:2px solid #30363d;font-weight:700;padding-top:2px;}}
ul{{padding-left:18px;}} li{{margin:6px 0;}} .wrap{{overflow-x:auto;}}
</style></head><body>
<h1>Research Dashboard · Decision</h1>
<div class="sub">{_esc(head)} · <a href="{_esc(details_href)}">Details (full report)</a></div>
<div class="banner" id="decisionFreshness">{banner}</div>
<h2>Alarms</h2><ul id="decisionAlarms">{alarm_html}</ul>
<h2>Tiles (from the canonical tile registry)</h2>
<div class="wrap"><table id="decisionTiles"><thead><tr><th>Tile</th><th>Closed trades (n)</th>
<th>After-cost EV / close</th><th>Fill rate</th><th>Not filled</th>
<th>MAE / MFE (median, % margin)</th><th>AI vs rules</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table></div>
<p class="sub">A tile needs at least {payload.get('min_sample', MIN_DECISION_SAMPLE)} closed trades before any EV
verdict or ranking. "no data yet" means the value was not collected or not published; it is never a zero.</p>
<h2>Trade counts</h2><p id="decisionTradeScope">{_esc((payload.get('trade_counts') or {}).get('text') or NO_DATA_TEXT)}</p>
<h2>Bitfinex relay state (read-only)</h2><div class="wrap"><table id="decisionRelayState">{relay_html}</table></div>
<p class="sub">From the laptop's authenticated ops relay-status snapshot. Missing or failed snapshots are shown as
no data, never as flat or disarmed. This page cannot arm or change the relay.</p>
<h2>Data freshness</h2><div class="wrap"><table id="decisionFreshnessTable">{fresh_html}</table></div>
<p class="sub">Fill worlds, did vs missed, AI usefulness, collection rate, quarantine receipt and the n&ge;30 EV
ranking: <a href="/evidence-points">Evidence points</a>.</p>
<h2>More</h2><p>{nav}</p>
</body></html>"""
