"""Synthetic/offline K3 prefix accounting, never a PASS or selection engine.

The batch finality contract is explicit: aggregate by original intent ordinal
before prefix evaluation. It is not a live asynchronous finality adapter.
"""
# PROVISIONAL, pre-freeze, non-evidential. Live OFF. No runtime integration.
PIN7_SPEC_SHA256 = "dc0cc45f24f5afee9a69718fcc120fc27450f80b6e35de1058f0ed0e68eedf79"
P8_SPEC_SHA256 = "28315aeb4423e88d4e3dadcf3e42005b29ddbba1f2e054467aeab7827135641e"
CT_TABLE_SHA256 = "37603987e6dabdc42ce89aec9482a3366d881f14c5d7c53356307e890c7595f0"
RULES_SHA256 = "4305f12a02d23f5cad689fd388387ffad3ecc50e02e614c883894d7ef3bf94f2"
RK_PROVISIONAL_RULING = "72c250a0eb82"

import copy
import json
import math
from collections import Counter
from fractions import Fraction
from pathlib import Path

LOOKS = {30: 12, 60: 20, 100: 30, 200: 54}


def score_k3(data, *, run_root=None):
    seen_events, intents, ordinals = {}, {}, {}
    for index, event in enumerate(data["events"]):
        if index in data.get("restart_after", []):
            # Persist the unfinished batch accumulator; no invented finality or fresh look budget.
            if run_root is None:
                raise ValueError("RUNROOT_REQUIRED_FOR_K3_CHECKPOINT")
            path = Path(run_root) / "k3-batch-checkpoint.json"
            path.write_text(json.dumps(dict(events=seen_events, intents=intents, ordinals=ordinals), allow_nan=False))
            checkpoint = json.loads(path.read_text())
            seen_events, intents = checkpoint["events"], checkpoint["intents"]
            ordinals = {int(k): v for k, v in checkpoint["ordinals"].items()}
        key = event["event_id"]
        if key in seen_events:
            if seen_events[key] != event:
                return {"status": "STOP_CONFLICT"}
            continue
        seen_events[key] = copy.deepcopy(event)
        intent, ordinal = event["intent"], event["ordinal"]
        if ordinal in ordinals and ordinals[ordinal] != intent:
            return {"status": "STOP_CONFLICT"}
        ordinals[ordinal] = intent
        previous = intents.get(intent)
        if previous is not None:
            if previous["ordinal"] != ordinal or previous.get("final") and previous != event:
                return {"status": "STOP_CONFLICT"}
        intents[intent] = copy.deepcopy(event)
    rows, reasons = [], Counter()
    for event in sorted(intents.values(), key=lambda row: row["ordinal"]):
        if not event.get("final"):
            reasons["OPEN_TAIL"] += 1
        elif not event.get("known") or not event.get("valid"):
            reasons[event.get("reason", "INVALID" if not event.get("valid") else "UNKNOWN_MFE")] += 1
        elif any(not isinstance(event.get(k), (int, float)) or not math.isfinite(event[k]) for k in ("mfe_i", "final_i")):
            reasons["UNKNOWN_MFE"] += 1
        else:
            rows.append(event)
    looks, givebacks, qualifying = [], 0, 0
    # A supplied previous kill is monotone; restart never creates a PASS path.
    kill = bool(data.get("prior_kill", False))
    for n, event in enumerate(rows, 1):
        qualifies = event["mfe_i"] >= 10
        qualifying += int(qualifies)
        givebacks += int(qualifies and event["final_i"] <= 0)
        if n in LOOKS:
            at_look = givebacks >= LOOKS[n]
            kill |= at_look
            looks.append(dict(n=n, givebacks=givebacks, threshold=LOOKS[n], kill_at_look=at_look))
    n = len(rows)
    return dict(status="KILL" if kill else "CONTINUE_NO_PASS", kill=kill, n=n, g=givebacks, qualifying=qualifying,
                unknown=sum(reasons.values()), reasons=dict(reasons), unconditional=givebacks / n if n else None,
                conditional=givebacks / qualifying if qualifying else None, looks=looks,
                scope="COMPLETED_BATCH_ORIGINAL_ORDER_UNIT_ONLY")


def falsekill_probability():
    """Exact rational absorbing DP, p=1/5, these four looks, one pick only."""
    alive = {0: Fraction(1)}
    killed = Fraction(0)
    for n in range(1, 201):
        next_alive = {}
        for count, probability in alive.items():
            for delta, p in ((0, Fraction(4, 5)), (1, Fraction(1, 5))):
                new = count + delta
                mass = probability * p
                if n in LOOKS and new >= LOOKS[n]:
                    killed += mass
                else:
                    next_alive[new] = next_alive.get(new, Fraction(0)) + mass
        alive = next_alive
    return dict(probability=float(killed), scope="iid p=.20, four looks, one pick only")


def executable_l1_mtm(data):
    """Qty-fitting single-level unit probe, NOT REALISTIC_V1 depth validation."""
    if not data.get("final"):
        return dict(known=False, reason="OPEN_TAIL")
    rows = data["l1"]
    if not rows:
        return dict(known=False, reason="MISSING_MTM")
    side = data["side"]
    if side not in ("LONG", "SHORT"):
        raise ValueError("INVALID_SIDE")
    quote, size = ("bid", "bid_size") if side == "LONG" else ("ask", "ask_size")
    previous_sequence = None
    marks = []
    basis = Fraction(str(data["entry"]))
    for row in rows:
        age = row["decision_ms"] - row["event_ms"]
        if age < 0:
            return dict(known=False, reason="CLOCK")
        if previous_sequence is not None and row["sequence"] != previous_sequence + 1:
            return dict(known=False, reason="SEQ")
        previous_sequence = row["sequence"]
        if row.get(size, 0) < data["qty"]:
            return dict(known=False, reason="DEPTH")
        if row.get(quote) is None or not math.isfinite(row[quote]) or row[quote] <= 0:
            return dict(known=False, reason="MISSING_MTM")
        if age > data["synthetic_age_limit_ms"]:
            return dict(known=False, reason="STALE_BBO")
        marks.append((1 if side == "LONG" else -1) * (Fraction(str(row[quote])) - basis) / basis * 10000)
    # Preserve exact rationals through selection; serialize only the declared bp fields.
    mfe, final = max([Fraction(0), *marks]), marks[-1]
    return dict(known=True, record="i",
                mfe_bp=mfe.numerator if mfe.denominator == 1 else float(mfe),
                final_bp=final.numerator if final.denominator == 1 else float(final), scope="L1_UNIT_ONLY")
