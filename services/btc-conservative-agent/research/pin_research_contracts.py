"""Pure admission/mask/selection helpers; metadata is not execution permission.

No actual B2/Stage-D bytes are read. B3 guard arguments are proposed-read
metadata / synthetic rows; the guard never opens the indicated file.
"""
# PROVISIONAL, pre-freeze, non-evidential. Live OFF. No runtime integration.
PIN7_SPEC_SHA256 = "dc0cc45f24f5afee9a69718fcc120fc27450f80b6e35de1058f0ed0e68eedf79"
P8_SPEC_SHA256 = "28315aeb4423e88d4e3dadcf3e42005b29ddbba1f2e054467aeab7827135641e"
CT_TABLE_SHA256 = "37603987e6dabdc42ce89aec9482a3366d881f14c5d7c53356307e890c7595f0"
RULES_SHA256 = "4305f12a02d23f5cad689fd388387ffad3ecc50e02e614c883894d7ef3bf94f2"
RK_PROVISIONAL_RULING = "72c250a0eb82"

import csv
import hashlib
import io
import math
import json
from pathlib import Path
from .pin7_classifier import calibrate

CT_CODE_SHA256 = "73f6a1b27987fa754189cc5f865003df06658f0a9112b39ac57d1e8860ac0f3d"
GRAMMAR_FIELDS = {"tuple_id", "family", "mode", "base_regime", "sides", "entry", "exit", "risk", "complete"}
FOLLOW = {"H01", "H05", "H06", "H08", "H09", "H10", "H11"}
FADE = {"H03", "H04", "H07", "H12"}
PRIMARY_N = 836  # R59(6) LOWER BOUND; draft implementation is not freeze evidence.
PRIMARY_N_STATUS = "LOWER_BOUND_PROVISIONAL_NOT_FREEZE_EVIDENCE"


def contains_performance(value):
    """Reject prohibited columns by name, including null-valued/nested columns."""
    if isinstance(value, dict):
        for key, child in value.items():
            name = "".join(ch for ch in str(key).lower() if ch.isalnum())
            if name in {"pl", "winrate", "tradecount", "expectancy"} or any(token in name for token in ("pnl", "sharpe", "return", "score", "performance", "ranking")):
                return True
            if contains_performance(child):
                return True
    return isinstance(value, list) and any(contains_performance(v) for v in value)


def select_ids(grammar, *, input_context=None):
    """WHOLE-input rejection precedes selection: never sanitize/salvage IDs."""
    if contains_performance(input_context if input_context is not None else grammar):
        return dict(status="STOP", alarm=True, IDs=None)
    if any(set(row) - GRAMMAR_FIELDS for row in grammar):
        return {"status": "STOP_UNAPPROVED_COLUMNS", "alarm": True, "IDs": None}
    ids = [r.get("tuple_id") for r in grammar]
    if any(type(x) is not int for x in ids) or len(set(ids)) != len(ids):
        return {"status": "HOLD_GRAMMAR"}
    if any(not {"tuple_id", "family", "mode", "base_regime", "sides", "complete"} <= r.keys() for r in grammar):
        return {"status": "HOLD_GRAMMAR"}
    if any(not set(r["sides"]) <= {"LONG", "SHORT"} for r in grammar):
        return {"status": "HOLD_GRAMMAR"}
    rows = [r for r in grammar if r["complete"] and r["base_regime"] == "NONE" and r["sides"] and all(r.get(k) for k in ("entry", "exit", "risk"))]
    def pick(predicate):
        return min((r["tuple_id"] for r in rows if predicate(r)), default=None)
    result = dict(U=pick(lambda r: r["family"] in FOLLOW and r["mode"] == "FOLLOW" and "LONG" in r["sides"]),
                  D=pick(lambda r: r["family"] in FOLLOW and r["mode"] == "FOLLOW" and "SHORT" in r["sides"]),
                  Q=pick(lambda r: r["family"] in FADE and r["mode"] == "FADE" and set(r["sides"]) == {"LONG", "SHORT"}),
                  A=pick(lambda r: set(r["sides"]) == {"LONG", "SHORT"}))
    missing = [k for k, v in result.items() if v is None]
    return dict(status="HOLD_BRANCH" if missing else "SYNTHETIC_IDS_ONLY", IDs=result, missing=missing,
                rankings_used=False, reserved_variants=4, A_available=result["A"] is not None)


def prior_calibration(data):
    a, b, start, end = (data[k] for k in ("a", "b", "start", "end"))
    if data.get("frozen_a", a) != a or data.get("frozen_b", b) != b:
        return {"status": "HOLD_IMMUTABLE_C0"}
    if b > start or b - a != 720 * 3600 or end <= start:
        return {"status": "HOLD_PRIOR_ONLY"}
    if not data.get("bridge_present", True) or not data.get("bridge_allowed", True) or not data.get("checkpoint"):
        return {"status": "HOLD_BRIDGE"}
    rows = sorted((r for r in data["records"] if a < r["h"] <= b), key=lambda r: r["h"])
    if len(rows) != 720 or [r["h"] for r in rows] != [a + i * 3600 for i in range(1, 721)]:
        return {"status": "HOLD_CALIBRATION"}
    calibration = calibrate([(r["V"], r["E"]) for r in rows], complete_minutes=data.get("complete_minutes", True))
    if calibration["status"] != "SEALED_UNIT_ONLY":
        return {"status": "HOLD_CALIBRATION"}
    thresholds = {k: calibration[k] for k in ("v90", "v95", "e50", "e75")}
    research = [r for r in data.get("outcomes", []) if max(start, b) < r["h"] < end]
    floor = [r["id"] for r in research if start + 18 * 86400 <= r["h"] < end]
    return dict(status="PRIOR_C0_REUSED_UNIT_ONLY", thresholds=thresholds, calibration_count=720, calibration_first=a + 3600,
                calibration_last=b, earliest_research_hour=start + 3600, end_owned_by_calibration_only=True,
                research=[r["id"] for r in research], floor=floor, calendar_days=(end - start) / 86400,
                OOS_days=(end - start) / 86400 - 18, refit=False, state_checkpoint=data["checkpoint"])


def role_mask(data):
    result = dict(F=16, calendar_days=244, OOS_days=172, min_positive=12, empty_fold_in_denominator=True,
                  expected_trade_counts="UNKNOWN")
    if data.get("missing") or data.get("partial"):
        return dict(result, status="HOLD")
    included = [r["id"] for r in data["events"] if r["job"] in (1, 3, 4, 5) and r.get("role", "OOS") == "OOS" and 18 * 86400 <= r["offset_s"] < 61 * 86400]
    return dict(result, masks={k: list(included) for k in ("A", "U", "D", "Q", "composite")}, status="COUNTS_UNKNOWN_HOLD")


def runtime_gate(data):
    for key in ("code", "table", "state"):
        if not data.get(key + "_expected") or data.get(key + "_actual") != data[key + "_expected"]:
            return dict(status="STOP", reason=key.upper() + "_HASH")
    if data.get("refit"):
        return dict(status="STOP", reason="REFIT")
    required = ("GPU", "CPU_pins", "GPU_pins", "dependency_pins", "data_pins", "parity_pins", "numeric_wall", "R60_PASS")
    missing = [key for key in required if not data.get(key)]
    if data.get("numeric_wall") and (type(data["numeric_wall"]) not in (int, float) or not math.isfinite(data["numeric_wall"]) or data["numeric_wall"] <= 0):
        missing.append("numeric_wall")
    return dict(status="HOLD" if missing else "UNIT_PINS_PRESENT_NOT_LAUNCH", missing=missing, CPU_fallback=False, extra_PASS=False)


def ct_lookup(table_utf8, compute_code_utf8, elapsed_days, expected_sha256, expected_code_sha256):
    # Verify both FROZEN identities, not just an arbitrary caller-provided hash.
    if expected_sha256 != CT_TABLE_SHA256 or hashlib.sha256(table_utf8.encode()).hexdigest() != CT_TABLE_SHA256:
        return {"status": "STOP_TABLE_SHA"}
    if expected_code_sha256 != CT_CODE_SHA256 or hashlib.sha256(compute_code_utf8.encode()).hexdigest() != CT_CODE_SHA256:
        return {"status": "STOP_CODE_SHA"}
    d = math.floor(elapsed_days)
    if d < 28:
        return dict(status="NO_DECISION", T=None, c_T=None)
    if d > 364:
        return dict(status="ROW_ONLY_NOT_PASS", T=365, c_T=22.958)
    rows = list(csv.DictReader(io.StringIO("\n".join(line for line in table_utf8.splitlines() if not line.startswith("#")))))
    row = max((row for row in rows if int(row["T_days"]) <= d), key=lambda row: int(row["T_days"]))
    return dict(status="ROW_ONLY_NOT_PASS", T=int(row["T_days"]), c_T=float(row["c_T"]))


def b3_guard(data, *, run_root=None):
    """Synthetic supplied payload only. Terminal latch never follows file metadata.

    Optional checkpoints are unit serialization below RUNROOT, not real restarts.
    Clearing requires a separately approved new dated context, not an input flag.
    """
    lower, upper = 1743120000, 1743724800
    latched, trace = bool(data.get("latched", False)), []
    discarded = latched
    for index, event in enumerate(data["events"]):
        if index in data.get("restart_at", []):
            if run_root is None:
                raise ValueError("RUNROOT_REQUIRED_FOR_GUARD_CHECKPOINT")
            checkpoint = Path(run_root) / "b3-guard-state.json"
            checkpoint.write_text(json.dumps(dict(latched=latched, discarded=discarded), allow_nan=False))
            restored = json.loads(checkpoint.read_text())
            latched, discarded = restored["latched"], restored["discarded"]
        item = dict(read_allowed=False, entries=False, state_may_advance=False, trades=False, PnL=False, score=False, alarm=False, B3_scored=False)
        if latched:
            item["status"] = "STOP_LATCHED"
        elif event["kind"] != "CANDLES":
            latched = True
            item.update(status="STOP_OUTCOME_ARTEFACT", alarm=True)
        else:
            rows = list(csv.reader(io.StringIO(event["rows_utf8"])))
            times = [int(r[1]) for r in rows]
            if not times or any(not lower <= t < upper for t in [event["close_epoch_s"], *times]):
                latched = True
                item["status"] = "STOP_SUFFIX"
            else:
                log = event.get("log")
                valid_log = isinstance(log, dict) and event.get("log_readable", True) is True and all(k in log for k in ("file", "row_range", "sha256"))
                valid_log = valid_log and log["file"] == event["file"] and log["row_range"] == event["row_range"] and log["sha256"] == hashlib.sha256(event["rows_utf8"].encode()).hexdigest()
                if not valid_log:
                    latched = True
                    item.update(status="STOP", alarm=True)
                else:
                    item.update(status="ALLOWED_WARMUP_ONLY", read_allowed=True, state_may_advance=True, log=dict(log))
        discarded = discarded or latched
        if discarded:
            item["warmup_state"] = "DISCARDED"
        trace.append(dict(item, latched=latched))
    return dict(trace=trace, latched=latched, auto_resume=False, B3_scored=False, warmup_state="DISCARDED" if discarded else data.get("warmup_state"))


def frozen_ids(data):
    # These are two caller supplied ID bindings, NOT a fixture golden envelope.
    return dict(status="FROZEN_IDS_MATCH_UNIT_ONLY" if data["actual"] == data["expected"] else "STOP_FROZEN_IDS")


def selection_eligibility(data):
    """Synthetic what-if N arithmetic; operative PRIMARY_N never changes here."""
    N = PRIMARY_N + data.get("other_variants", 0) + int(data.get("extra_variant", False))
    status = "UNIT_BOUND"
    if not all(data.get(k, True) for k in ("whitelist", "blind", "frozen", "no_refit", "data_match", "state_match")):
        status = "STOP"
    elif data.get("extra_variant"):
        status = "STOP_AMEND_N"
    return dict(status=status, N=N, primary_N=PRIMARY_N, synthetic_what_if_only=True)
