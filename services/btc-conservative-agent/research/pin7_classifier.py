"""Causal candle-only Pin 7 core; no bot regimes, signals, data access or fills.

All input timestamps are explicit close labels in seconds. Historical close
availability is an assumption, not a reconstruction of receipt evidence.
"""
# PROVISIONAL, pre-freeze, non-evidential. Live OFF. No runtime integration.
PIN7_SPEC_SHA256 = "3b5dba29fdfffe916fef9d209c6095db5ca45fc1a998a9682ea6acc383584fc1"
P8_SPEC_SHA256 = "ca77dbaeed9cf55ac1f14d6e4ecd525c21ccacc4987c64c112c7e1fb5f5d581a"
CT_TABLE_SHA256 = "37603987e6dabdc42ce89aec9482a3366d881f14c5d7c53356307e890c7595f0"
RULES_SHA256 = "4305f12a02d23f5cad689fd388387ffad3ecc50e02e614c883894d7ef3bf94f2"
RK_PROVISIONAL_RULING = "72c250a0eb82"

import copy
import hashlib
import json
import math
import os
from pathlib import Path

HOUR = 3600
DAY = 24 * HOUR
DWELL = 12 * HOUR
SYMBOL = "tBTCF0:USTF0"
LABELS = {"UP", "DOWN", "RANGE", "SHOCK"}


def canonical_sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def candle_features(bars, decision, *, availability_mode="forward", symbol=SYMBOL):
    """Compute V/E/z/B on exactly 1441 complete minute closes, never fill gaps."""
    if symbol != SYMBOL:
        return {"status": "VETO", "reason": "SYMBOL"}
    if not math.isfinite(decision) or decision % HOUR:
        return {"status": "VETO", "reason": "CLOCK"}
    rows = sorted((b for b in bars if decision - DAY <= b["end"] <= decision), key=lambda b: b["end"])
    if len(rows) != 1441:
        return {"status": "VETO", "reason": "COUNT"}
    if [b["end"] for b in rows] != [decision - DAY + i * 60 for i in range(1441)]:
        return {"status": "VETO", "reason": "GRID"}
    historical = availability_mode == "historical_final"
    if availability_mode not in ("historical_final", "forward"):
        return {"status": "VETO", "reason": "AVAILABILITY"}
    previous_sequence = None
    for b in rows:
        values = [b.get(k) for k in ("open", "high", "low", "close")]
        if any(not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v) or v <= 0 for v in values):
            return {"status": "VETO", "reason": "OHLC"}
        o, hi, lo, c = values
        if lo > min(o, c) or hi < max(o, c) or lo > hi:
            return {"status": "VETO", "reason": "OHLC"}
        if not b.get("complete", False) or (not historical and b.get("received", decision + 1) > decision):
            return {"status": "VETO", "reason": "AVAILABILITY"}
        if not historical:
            if any(b.get(k) is None for k in ("received", "event_time", "version", "sequence", "content_sha256", "content_payload_utf8")):
                return {"status": "VETO", "reason": "FORWARD_METADATA"}
            if b["received"] < b["event_time"] or b["event_time"] > decision:
                return {"status": "VETO", "reason": "CLOCK"}
            if previous_sequence is not None and b["sequence"] != previous_sequence + 1:
                return {"status": "VETO", "reason": "SEQUENCE"}
            previous_sequence = b["sequence"]
            payload = b["content_payload_utf8"]
            try:
                content = json.loads(payload)
            except (TypeError, ValueError):
                return {"status": "VETO", "reason": "VERSION"}
            if hashlib.sha256(payload.encode()).hexdigest() != b["content_sha256"] or any(content.get(k) != b[k] for k in ("end", "open", "high", "low", "close")):
                return {"status": "VETO", "reason": "VERSION"}
    closes = [b["close"] for b in rows]
    minute_returns = [math.log(b / a) for a, b in zip(closes, closes[1:])]
    V = math.sqrt(math.fsum(r * r for r in minute_returns))
    hourly = closes[::60]
    R = math.log(hourly[-1] / hourly[0])
    denominator = math.fsum(abs(math.log(b / a)) for a, b in zip(hourly, hourly[1:]))
    E = abs(R) / denominator if denominator else 0.0
    # Minute end labels: the first close is the previous-day anchor, not in the 24h OHLC bars.
    span24 = max(b["high"] for b in rows[1:]) - min(b["low"] for b in rows[1:])
    last6 = rows[-360:]
    span6 = max(b["high"] for b in last6) - min(b["low"] for b in last6)
    result = {"status": "OK", "V": V, "R": R, "E": E, "z": R / V if V else 0.0, "B": span6 / span24 if span24 else 0.0}
    if historical:
        result.update(limitation="HISTORICAL_FINAL_VERSION_LIMITATION", receipt_time=None)
    return result


def versioned_features(data):
    """Choose only complete versions received as of the decision, without rewriting a prefix."""
    if data.get("availability_mode") == "historical_final":
        return candle_features(data["bars"], data["decision"], availability_mode="historical_final", symbol=data.get("symbol", SYMBOL))
    chosen = {}
    for bar in data["bars"]:
        if bar.get("complete") and bar.get("received", float("inf")) <= data["decision"]:
            old = chosen.get(bar["end"])
            if old is not None and old["received"] == bar["received"] and old != bar:
                return {"status": "VETO", "reason": "VERSION"}
            if old is None or bar["received"] > old["received"]:
                chosen[bar["end"]] = bar
    result = candle_features(list(chosen.values()), data["decision"], symbol=data.get("symbol", SYMBOL))
    if result["status"] == "OK":
        result["last_selected_version"] = chosen[data["decision"]]["version"]
    return result


def quantile(values, p):
    values = sorted(values)
    position = (len(values) - 1) * p
    j = math.floor(position)
    return values[j] + (position - j) * (values[min(j + 1, len(values) - 1)] - values[j])


def calibrate(pairs, *, fixed_interval=True, allowlisted=True, complete_minutes=True):
    if not fixed_interval or not allowlisted or not complete_minutes or len(pairs) != 720:
        return {"status": "HOLD"}
    if any(len(p) != 2 or any(not isinstance(x, (int, float)) or not math.isfinite(x) for x in p) or p[0] < 0 or not 0 <= p[1] <= 1 for p in pairs):
        return {"status": "HOLD"}
    V, E = zip(*pairs)
    result = dict(v90=quantile(V, .9), v95=quantile(V, .95), e50=quantile(E, .5), e75=quantile(E, .75))
    if not (0 < result["v90"] < result["v95"] and 0 <= result["e50"] < result["e75"] <= 1):
        return {"status": "HOLD"}
    return {"status": "SEALED_UNIT_ONLY", **result}


def candidate(V, E, z, current, thresholds, *, latch="NONE"):
    if current not in LABELS or any(not math.isfinite(x) for x in (V, E, z)) or V < 0 or not 0 <= E <= 1:
        raise ValueError("INVALID_FEATURE_OR_LABEL")
    v90, v95, e50, e75 = (thresholds[k] for k in ("v90", "v95", "e50", "e75"))
    if not (0 < v90 < v95 and 0 <= e50 < e75 <= 1):
        raise ValueError("INVALID_THRESHOLDS")
    if latch == "WAIT_SHOCK" or (current == "SHOCK" and V > v90) or V >= v95:
        return "SHOCK"
    if current == "UP" and z >= .25 and E >= e50:
        return "UP"
    if current == "DOWN" and z <= -.25 and E >= e50:
        return "DOWN"
    if z >= .5 and E >= e75:
        return "UP"
    if z <= -.5 and E >= e75:
        return "DOWN"
    return "RANGE"


def cap_allows(flips, h):
    return sum(h - DAY < t <= h for t in flips) < 2


def validate_state(state):
    if state.get("corrupt") or state.get("label") not in LABELS or state.get("latch") not in ("NONE", "WAIT_SHOCK", "IN_SHOCK"):
        raise ValueError("STOP_CORRUPT")
    flips = state["flips"]
    if any(not math.isfinite(t) for t in flips) or any(b - a < DWELL for a, b in zip(flips, flips[1:])):
        raise ValueError("STOP_CORRUPT")
    if flips and state["last_flip"] != flips[-1]:
        raise ValueError("STOP_CORRUPT")
    if state.get("pending") not in LABELS | {None} or state.get("count", 0) < 0:
        raise ValueError("STOP_CORRUPT")
    last_valid = state.get("last_valid")
    if last_valid is not None and (not math.isfinite(last_valid) or state["last_flip"] > last_valid):
        raise ValueError("STOP_CORRUPT")
    if state.get("pending") is not None:
        if state.get("first") is None or last_valid is None or state["first"] > last_valid:
            raise ValueError("STOP_CORRUPT")
        if state["count"] != (last_valid - state["first"]) / HOUR + 1:
            raise ValueError("STOP_CORRUPT")


def initial_state(h):
    return dict(label="RANGE", pending=None, first=None, count=0, last_valid=None, last_flip=h, flips=[], latch="NONE", initial_veto_until=h + DWELL)


def step(state, event, thresholds):
    """One scheduled close. Returns a new state and intents, never closes positions itself.

    Exposure/residual/actual-flat are external facts, not inferred from an exit
    request. Independent safety instructions are returned even on STOP.
    """
    s = copy.deepcopy(state)
    h = event["h"]
    try:
        validate_state(s)
    except (KeyError, TypeError, ValueError):
        return s, dict(h=h, label=s.get("label"), accepted=False, entry=False, safety=True, status="STOP_CORRUPT")
    trace = dict(h=h, label=s["label"], accepted=False, safety=True, close_requested=False, denied=[])
    if h % HOUR or s.get("last_event") is not None and h <= s["last_event"]:
        trace.update(entry=False, status="STOP_CLOCK")
        return s, trace
    s["last_event"] = h
    if not event.get("valid", True):
        s.update(pending=None, first=None, count=0, last_valid=None)
        trace.update(entry=False, denied=["MISSING"], status="HOLD")
        return s, trace
    raw_shock = event["V"] >= thresholds["v95"]
    if raw_shock and s["label"] != "SHOCK":
        s["latch"] = "WAIT_SHOCK"
    target = candidate(event["V"], event["E"], event["z"], s["label"], thresholds, latch=s["latch"])
    if target == s["label"]:
        s.update(pending=None, first=None, count=0)
    else:
        if target != s.get("pending") or s.get("last_valid") != h - HOUR:
            s.update(pending=target, first=h, count=1)
        else:
            s["count"] += 1
        confirmed = s["count"] >= 3 and h - s["first"] >= 2 * HOUR
        if confirmed:
            if h - s["last_flip"] < DWELL:
                trace["denied"].append("DWELL")
            if not cap_allows(s["flips"], h):
                trace["denied"].append("CAP")
            if not trace["denied"]:
                s.update(label=target, last_flip=h, pending=None, first=None, count=0)
                s["flips"].append(h)
                if target == "SHOCK":
                    s["latch"] = "IN_SHOCK"
                elif s["latch"] == "IN_SHOCK":
                    s["latch"] = "NONE"
                trace.update(accepted=True, close_requested=bool(event.get("exposure") or event.get("residual")))
    s["last_valid"] = h
    trace.update(label=s["label"], latch=s["latch"], entry=bool(not event.get("warmup") and not event.get("residual") and not event.get("exposure") and not raw_shock and s["label"] != "SHOCK" and s["latch"] == "NONE" and h >= s["initial_veto_until"]))
    return s, trace


def replay(data, thresholds):
    s = copy.deepcopy(data["state"])
    try:
        validate_state(s)
    except (KeyError, TypeError, ValueError):
        return {"status": "STOP_CORRUPT", "trace": []}
    trace = []
    for i, event in enumerate(data["events"]):
        if i in data.get("restart_at", []):
            s = json.loads(json.dumps(s, allow_nan=False))
        s, item = step(s, event, thresholds)
        trace.append(item)
        if item.get("status", "").startswith("STOP"):
            return {"status": item["status"], "trace": trace, "state": s}
    return {"status": "UNIT_TRACE", "trace": trace, "state": s}


class StateJournal:
    """Append/checkpoint pin7-state.jsonl below an explicitly supplied run root.

    Each durable full checkpoint chains to its predecessor. No tail repair,
    truncation or fresh budget on error. Single writer is the caller's duty.
    """
    def __init__(self, run_root, bindings, clock_mapping):
        self.path = Path(run_root) / "state" / "pin7-state.jsonl"
        self.bindings = dict(bindings)
        self.clock_mapping = dict(clock_mapping)

    def load(self):
        previous, last, sequence = None, None, 0
        if not self.path.exists():
            raise ValueError("STOP_MISSING_CHECKPOINT")
        with self.path.open(encoding="utf-8") as fh:
            for line in fh:
                record = json.loads(line)
                sha = record.pop("state_sha256")
                if canonical_sha(record) != sha or record["previous_sha256"] != previous or record["sequence"] != sequence:
                    raise ValueError("STOP_STATE_HASH")
                if record["bindings"] != self.bindings or record["clock_mapping"] != self.clock_mapping:
                    raise ValueError("STOP_BINDINGS_OR_CLOCK")
                validate_state(record["state"])
                if last is not None:
                    old_clock, new_clock = last["state"].get("last_valid"), record["state"].get("last_valid")
                    if record["source_sequence"] <= last["source_sequence"] or old_clock is not None and (new_clock is None or new_clock < old_clock):
                        raise ValueError("STOP_SOURCE_SEQUENCE_OR_REWIND")
                previous, last, sequence = sha, record, sequence + 1
        if last is None:
            raise ValueError("STOP_EMPTY_CHECKPOINT")
        self._last_source_sequence = last["source_sequence"]
        return last["state"], previous, sequence

    def append(self, state, *, input_sha256, version, source_sequence):
        if not isinstance(input_sha256, str) or len(input_sha256) != 64 or set(input_sha256) - set("0123456789abcdef") or not version or type(source_sequence) is not int:
            raise ValueError("STOP_INPUT_PROVENANCE")
        validate_state(state)
        previous, sequence = None, 0
        if self.path.exists():
            old, previous, sequence = self.load()
            if source_sequence <= self._last_source_sequence:
                raise ValueError("STOP_SOURCE_SEQUENCE")
            if old.get("last_valid") is not None and (state.get("last_valid") is None or state["last_valid"] < old["last_valid"]):
                raise ValueError("STOP_STATE_REWIND")
        record = dict(state=state, bindings=self.bindings, clock_mapping=self.clock_mapping, previous_sha256=previous,
                      sequence=sequence, input_sha256=input_sha256, version=version, source_sequence=source_sequence)
        sha = canonical_sha(record)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(dict(record, state_sha256=sha), sort_keys=True, allow_nan=False) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        return sha


class CalibrationSeal:
    """Canonical immutable thresholds reused across jobs, not a forward refit.

    Creation accepts feature-only records, never outcomes; source provenance is
    caller supplied. This object is not an approval to read the source data.
    """
    def __init__(self, payload, expected_sha256):
        if canonical_sha(payload) != expected_sha256:
            raise ValueError("STOP_CALIBRATION_HASH")
        self._payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
        self.sha256 = expected_sha256

    @classmethod
    def create(cls, records, *, a, b, source_sha256, bindings, complete_minutes):
        if b - a != 720 * HOUR or len(records) != 720 or [r["h"] for r in records] != [a+i*HOUR for i in range(1, 721)]:
            raise ValueError("HOLD_CALIBRATION")
        if any(set(r) != {"h", "V", "E"} for r in records):
            raise ValueError("STOP_CALIBRATION_OUTCOME_FIELDS")
        if len(source_sha256) != 64 or not bindings:
            raise ValueError("STOP_CALIBRATION_PROVENANCE")
        result = calibrate([(r["V"], r["E"]) for r in records], complete_minutes=complete_minutes)
        if result.pop("status") != "SEALED_UNIT_ONLY":
            raise ValueError("HOLD_CALIBRATION")
        payload = dict(a=a, b=b, thresholds=result, source_sha256=source_sha256, bindings=dict(bindings))
        return cls(payload, canonical_sha(payload))

    def reuse(self, *, start, a, b, bindings, bridge_present, bridge_allowed, checkpoint):
        payload = json.loads(self._payload_json)
        if canonical_sha(payload) != self.sha256:
            raise ValueError("STOP_CALIBRATION_HASH")
        if payload["a"] != a or payload["b"] != b or payload["bindings"] != bindings:
            raise ValueError("STOP_CALIBRATION_REBIND")
        if b > start:
            raise ValueError("HOLD_PRIOR_ONLY")
        if not bridge_present or not bridge_allowed or not checkpoint:
            raise ValueError("HOLD_BRIDGE")
        return dict(thresholds=payload["thresholds"], calibration_sha256=self.sha256, checkpoint=checkpoint, refit=False)


def ordered_close_intents(*, position_intent_id, safety_exit, accepted_flip, exposure, residual):
    """One effective close per existing position, even on coincident stop/flip.

    This is a request, never a claim that a residual is flat or P&L realised.
    Attribution keeps the caller's entry-position identity in both handlers.
    """
    if not (exposure or residual):
        return []
    if not position_intent_id:
        raise ValueError("STOP_MISSING_POSITION_ID")
    reasons = (["SAFETY"] if safety_exit else []) + (["REGIME_FLIP"] if accepted_flip else [])
    if not reasons:
        return []
    return [dict(position_intent_id=position_intent_id, action="REQUEST_CLOSE", reasons=reasons,
                 priority="SAFETY" if safety_exit else "REGIME_FLIP", actual_flat=False)]
