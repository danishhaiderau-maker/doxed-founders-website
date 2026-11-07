"""Venue and source cadence rules.

Sparse silence is not an outage. Candle gaps require a trade cross-check from
the same run. Sequence holes are reported only for a per-instrument contiguous
contract. Unproven intervals stay UNKNOWN and do not invent timestamps.
"""

from __future__ import annotations

from research.coverage_audit.guards import AuditStop, exceeds, to_iso


def analyze(feed, events, violations: int, cross_events, ledger, gaps: list, unknowns: list) -> dict:
    if feed["verification_status"] != "SYNTHETIC_DECLARED":
        _unknown(unknowns, "VERIFICATION_UNVERIFIED")
        if feed["calendar"] == "UNVERIFIED":
            _unknown(unknowns, "CALENDAR_UNVERIFIED")
        if feed["observation"] == "unknown":
            _unknown(unknowns, "CADENCE_UNKNOWN")
        if feed["audit_publication_delay"] and feed["permissible_publication_delay_ms"] is None:
            _unknown(unknowns, "PUBLICATION_DELAY_UNVERIFIED")
        return {"status": "NOT_CHECKED", "reason": "NOT_CHECKED", "gap_count": 0}
    if not events:
        _unknown(unknowns, "MISSING_EVIDENCE")
        return {"status": "NOT_CHECKED", "reason": "NOT_CHECKED", "gap_count": 0}
    observation = feed["observation"]
    if observation == "periodic":
        if violations:
            _unknown(unknowns, "MONOTONICITY_BLOCKS_CADENCE")
        else:
            _periodic(feed, events, ledger, gaps)
    elif observation == "sparse":
        rule = feed["sparse_rule"]
        if rule == "CANDLE_CROSSCHECK_TRADES":
            if cross_events is None:
                _unknown(unknowns, "NO_TRADE_CROSSCHECK")
            else:
                _candles(feed, events, cross_events, ledger, gaps)
        elif rule != "NO_EVENT_NOT_OUTAGE":
            _unknown(unknowns, "CADENCE_UNKNOWN")
    else:
        _unknown(unknowns, "CADENCE_UNKNOWN")
    _publication_delay(feed, events, ledger, gaps)
    return _sequence(feed, events, ledger, gaps)


def _publication_delay(feed, events, ledger, gaps: list) -> None:
    delay = feed["permissible_publication_delay_ms"]
    if not isinstance(delay, int) or isinstance(delay, bool):
        return
    ref = _ref(feed)
    for event in events:
        if event.publication_ms is None:
            continue
        delta = event.publication_ms - event.ts_ms
        if delta < 0 or exceeds(delta, delay):
            _append(
                ledger,
                gaps,
                {
                    "feed_ref": ref,
                    "utc_start": to_iso(event.ts_ms),
                    "utc_end": to_iso(event.publication_ms if event.publication_ms >= event.ts_ms else event.ts_ms),
                    "rule": "PUBLICATION_DELAY_V1",
                    "justification": "DELAY_EXCEEDS_DECLARED",
                    "evidence": {"declared_delay_ms": delay, "observed_delta_ms": max(delta, 0)},
                },
            )


def _unknown(unknowns: list, reason: str) -> None:
    if reason not in unknowns:
        unknowns.append(reason)


def _append(ledger, gaps: list, gap: dict) -> None:
    ledger.add_gap()
    gaps.append(gap)


def _periodic(feed, events, ledger, gaps: list) -> None:
    cadence = feed["cadence_ms"]
    tolerance = feed["tolerance_ms"]
    ref = _ref(feed)
    previous = events[0]
    for event in events[1:]:
        delta = event.ts_ms - previous.ts_ms
        if delta > cadence + tolerance:
            _append(
                ledger,
                gaps,
                {
                    "feed_ref": ref,
                    "utc_start": to_iso(previous.ts_ms),
                    "utc_end": to_iso(event.ts_ms),
                    "rule": "PERIODIC_CADENCE_V1",
                    "justification": "DELTA_EXCEEDS_CADENCE_PLUS_TOLERANCE",
                    "evidence": {
                        "cadence_ms": cadence,
                        "tolerance_ms": tolerance,
                        "observed_delta_ms": delta,
                    },
                },
            )
        previous = event


def _candles(feed, events, cross_events, ledger, gaps: list) -> None:
    candles = {_minute(event.ts_ms) for event in events}
    ref = _ref(feed)
    for trade in cross_events:
        minute = _minute(trade.ts_ms)
        if minute in candles:
            continue
        _append(
            ledger,
            gaps,
            {
                "feed_ref": ref,
                "utc_start": to_iso(minute),
                "utc_end": to_iso(minute + 60000),
                "rule": "CANDLE_MISSING_WHILE_TRADE_PRESENT",
                "justification": "TRADE_MINUTE_WITHOUT_CANDLE",
                "evidence": {},
            },
        )
        candles.add(minute)


def _minute(ts_ms: int) -> int:
    return (ts_ms // 60000) * 60000


def _sequence(feed, events, ledger, gaps: list) -> dict:
    contract = feed["sequence_contract"]
    if contract is None:
        return {"status": "NOT_APPLICABLE", "reason": "NO_SEQUENCE_CONTRACT", "gap_count": 0}
    if contract["scope"] != "per_instrument" or contract["contiguous"] is not True:
        return {
            "status": "NOT_APPLICABLE",
            "reason": "INTERLEAVED_IDS_NOT_PER_INSTRUMENT",
            "gap_count": 0,
        }
    ref = _ref(feed)
    gap_count = 0
    nonmonotonic = False
    previous = None
    for event in events:
        if event.sequence is None:
            return {"status": "FAILED", "reason": "SEQUENCE_FIELD_ABSENT", "gap_count": gap_count}
        if previous is not None and event.sequence != previous.sequence + 1:
            nonmonotonic = nonmonotonic or event.sequence <= previous.sequence
            _append(
                ledger,
                gaps,
                {
                    "feed_ref": ref,
                    "utc_start": to_iso(previous.ts_ms),
                    "utc_end": to_iso(event.ts_ms),
                    "rule": "SEQUENCE_CONTRACT",
                    "justification": "SEQUENCE_NONMONOTONIC" if nonmonotonic else "SEQUENCE_HOLE",
                    "evidence": {
                        "expected_sequence": previous.sequence + 1,
                        "observed_sequence": event.sequence,
                    },
                },
            )
            gap_count += 1
        previous = event
    if nonmonotonic:
        return {"status": "FAILED", "reason": "SEQUENCE_NONMONOTONIC", "gap_count": gap_count}
    if gap_count:
        return {"status": "FAILED", "reason": "SEQUENCE_GAP", "gap_count": gap_count}
    return {"status": "PROVEN", "reason": "MONOTONIC_PROVEN", "gap_count": 0}


def _ref(feed) -> str:
    return "/".join((feed["venue"], feed["source"], feed["feed"], feed["instrument"], feed["era"]))


def ensure_no_fabricated(gaps: list, unknowns: list) -> None:
    if unknowns and not gaps:
        return
    for gap in gaps:
        if "utc_start" not in gap or "utc_end" not in gap or "rule" not in gap:
            raise AuditStop("UNEXPECTED_STOP")
