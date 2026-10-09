"""Live-copy control for the Fly signal source (Option 1, fail-closed).

Architecture (owner decision 2026-10-09): Fly is paper-only and is the signal
source. Real orders are placed ONLY by the website/Railway relay executor on
each armed copier account's own keys. Fly's legacy direct Bitfinex trading
path is retired.

A real order needs ALL of:

1. Fly "Live copy output" ON (this module, ``LiveCopyOutput``; dashboard
   switch, persisted on the data volume, default OFF, OFF after restart);
2. the tile's "Bitfinex Live Orders" switch ON (``bitfinex_live_switch``),
   evaluated with its full readiness (allowlist, size, protection evidence);
3. the tile relay-eligible in the canonical registry;
4. the copy intent created at/after both the output-ON time and the tile's
   allow time (never copies historical or already-open paper state);
5. a signed approval (``sign_approval``) that the Railway executor verifies
   before any exchange write, plus the account's own arm state ("Start real
   trading") and the executor's size/allowlist checks.

Anything missing or unknown means no copy intent. This module is pure (stdlib
plus the canonical registry); every mutation is persisted with fsync and an
atomic rename and never contains secrets.
"""
from __future__ import annotations

import collections
import hashlib
import hmac
import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Iterable, Mapping

APPROVAL_SCHEMA = "fly_live_copy_approval_v1"
REPORT_SCHEMA = "railway_live_execution_report_v1"
OUTPUT_SCHEMA = "fly_live_copy_output_v1"
PROTECTION_SCHEMA = "fly_live_copy_protection_evidence_v1"
DECISIONS_SCHEMA = "fly_live_copy_trade_decisions_v1"

APPROVAL_KEY_DOMAIN = b"fly-live-copy-approval-v1"
REPORT_KEY_DOMAIN = b"railway-live-execution-report-v1"

OUTPUT_SIDECAR = "live_copy_output_state.json"
PROTECTION_SIDECAR = "live_copy_protection_evidence.json"
DECISIONS_SIDECAR = "live_copy_trade_decisions.json"
ELIGIBILITY_SIDECAR = "live_copy_tile_eligibility.json"
OUTBOX_SIDECAR = "live_copy_outbox.json"
ELIGIBILITY_SCHEMA = "fly_live_copy_tile_eligibility_v1"
OUTBOX_SCHEMA = "fly_live_copy_outbox_v1"
# Registry capability blocks the operator may waive (research qualification).
# Protection blocks (e.g. partial exits not proven on the exchange) are never waived.
OPERATOR_WAIVABLE_CAPABILITIES = frozenset({"BLOCKED_UNQUALIFIED"})
REPORTS_JOURNAL = "live_execution_reports.jsonl"

RESTART_RESET_REASON = "PROCESS_RESTART_FAIL_CLOSED"
# Danish 2026-10-09: the exchange stop is a CATASTROPHE BACKUP only - placed
# once at entry, reduce-only, beyond the tile's own hard stop, never moved.
# The tile's own paper stop/exit logic drives normal exits.
EXCHANGE_STOP_BUFFER_BP = 25.0
EXCHANGE_STOP_NEVER_MOVED = True
# A Bitfinex stop must sit inside the ~100 bp liquidation distance at 100x.
EXCHANGE_STOP_MAX_BP = 95.0
MAX_MARGIN_USD = 0.25
REQUIRED_LEVERAGE = 100
# Protection evidence (a real, exchange-verified reduce-only stop) expires.
PROTECTION_EVIDENCE_TTL_SEC = 7 * 24 * 3600
# Reports older/newer than this (vs Fly clock) are refused as replays.
REPORT_MAX_SKEW_SEC = 600

# Events that can create or reprice an exchange entry order.
ENTRY_EVENTS = frozenset({"ORDER_PLACED", "LIMIT_UPDATED"})
# Lifecycle continuations: never open new exposure. They are emitted only for
# trades whose entry was approved, and are never withheld by a tile switch
# turned OFF afterwards (withholding an exit would leave real exposure).
CONTINUATION_EVENTS = frozenset({
    "POSITION_OPENED", "POSITION_REDUCED", "POSITION_CLOSED",
    "ORDER_EXPIRED", "ORDER_CANCELLED",
})

# Gate denial codes (stable).
DENY_OUTPUT_OFF = "LIVE_COPY_OUTPUT_OFF"
DENY_OUTPUT_TS_UNKNOWN = "LIVE_COPY_OUTPUT_TS_UNKNOWN"
DENY_INTENT_PRE_OUTPUT = "INTENT_CREATED_BEFORE_OUTPUT_ON"
DENY_TILE_SWITCH_OFF = "TILE_LIVE_SWITCH_OFF"
DENY_TILE_PRE_ARMING = "INTENT_CREATED_BEFORE_TILE_ON"
DENY_TILE_NOT_ELIGIBLE = "TILE_NOT_RELAY_ELIGIBLE"
DENY_TILE_READINESS = "TILE_READINESS_DENIED"
DENY_FORCE_PAPER = "FORCE_PAPER_MODE_ACTIVE"
DENY_SECRET_MISSING = "APPROVAL_SECRET_MISSING"
DENY_TRADE_NOT_APPROVED = "TRADE_ENTRY_NOT_APPROVED"
DENY_UNKNOWN_EVENT = "UNKNOWN_EVENT"
DENY_INTERNAL_ERROR = "GATE_INTERNAL_ERROR"

REPORT_TYPES = frozenset({
    "ORDER_SENT", "ORDER_PLACED", "ORDER_AMENDED", "ORDER_CANCELLED",
    "ORDER_FILLED", "ORDER_REJECTED", "STOP_PLACED", "STOP_CONFIRMED",
    "STOP_FAILED", "POSITION_CLOSED", "INTENT_REJECTED", "ERROR",
})


# ---------------------------------------------------------------------------
# Paths, canonical JSON, keys
# ---------------------------------------------------------------------------
def data_path(name: str, environ: Mapping[str, str] | None = None) -> Path:
    env = os.environ if environ is None else environ
    data_dir = str(env.get("BOT_DATA_DIR") or "").strip()
    if data_dir:
        return Path(data_dir) / name
    return Path(__file__).resolve().parent / name


def canonical(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def derive_key(secret: str, domain: bytes) -> bytes:
    """Domain-separated key from the shared relay secret (never persisted)."""
    secret = str(secret or "").strip()
    if not secret:
        return b""
    return hmac.new(secret.encode("utf-8"), domain, hashlib.sha256).digest()


def _sign(material: Mapping[str, Any], key: bytes) -> str:
    return hmac.new(key, canonical(dict(material)), hashlib.sha256).hexdigest()


def sign_approval(approval: Mapping[str, Any], secret: str) -> dict:
    """Return ``approval`` plus ``signed_body`` + ``signature``.

    The HMAC (approval domain) covers ``signed_body``: the exact canonical
    JSON text of the approval fields. Verifiers in any language check the
    HMAC over that string and then read fields only from it, so float or key
    formatting differences between JSON encoders can never break or bypass
    verification. The visible fields are a convenience copy and must equal it.
    """
    key = derive_key(secret, APPROVAL_KEY_DOMAIN)
    if not key:
        raise ValueError(DENY_SECRET_MISSING)
    body = {k: v for k, v in dict(approval).items() if k not in ("signature", "signed_body")}
    text = canonical(body)
    return {**json.loads(text), "signed_body": text.decode("utf-8"),
            "signature": hmac.new(key, text, hashlib.sha256).hexdigest()}


def verify_approval(approval: Any, secret: str) -> bool:
    if not isinstance(approval, Mapping):
        return False
    key = derive_key(secret, APPROVAL_KEY_DOMAIN)
    sig = approval.get("signature")
    text = approval.get("signed_body")
    if not key or not isinstance(sig, str) or not sig or not isinstance(text, str) or not text:
        return False
    expected = hmac.new(key, text.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig):
        return False
    try:
        signed = json.loads(text)
    except ValueError:
        return False
    visible = {k: v for k, v in dict(approval).items() if k not in ("signature", "signed_body")}
    return isinstance(signed, dict) and signed == visible


def sign_report(report: Mapping[str, Any], secret: str) -> str:
    key = derive_key(secret, REPORT_KEY_DOMAIN)
    if not key:
        raise ValueError(DENY_SECRET_MISSING)
    return hmac.new(key, canonical(dict(report)), hashlib.sha256).hexdigest()


def verify_report_signature(raw_body: bytes, signature: str, secret: str) -> bool:
    key = derive_key(secret, REPORT_KEY_DOMAIN)
    if not key or not isinstance(signature, str) or not signature:
        return False
    sig = signature.split("=", 1)[1] if signature.startswith("sha256=") else signature
    expected = hmac.new(key, raw_body or b"", hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, sig)


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
        with open(tmp, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
        try:
            fd = os.open(str(path.parent), os.O_RDONLY)
        except OSError:
            return True
        try:
            os.fsync(fd)
        except OSError:
            pass
        finally:
            os.close(fd)
        return True
    except OSError:
        return False


def _read_json(path: Path) -> dict | None:
    try:
        if not path.exists():
            return None
        raw = json.loads(path.read_text(encoding="utf-8") or "{}")
        return raw if isinstance(raw, dict) else None
    except (OSError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Fly "Live copy output" switch
# ---------------------------------------------------------------------------
class LiveCopyOutput:
    """Fly's account-independent output switch. Default OFF; OFF after restart.

    The record (who/when/why, last 50 changes) persists on the data volume so
    it survives deploys, but a new process never inherits ON: turning output
    on is a deliberate operator action after every restart (fail closed).
    """

    def __init__(self, path: Path | str | None = None, clock=time.time):
        self.path = Path(path) if path is not None else data_path(OUTPUT_SIDECAR)
        self._clock = clock
        self._lock = threading.Lock()
        self._state: dict = {
            "enabled": False, "enabled_at_ts": None, "last_change_ts": None,
            "last_change_by": None, "last_reason": None, "history": [],
        }
        self.persist_ok = True
        self._load()

    def _load(self) -> None:
        raw = _read_json(self.path)
        if raw and isinstance(raw.get("state"), dict):
            prior = raw["state"]
            self._state.update({k: prior.get(k) for k in self._state if k in prior})
            if not isinstance(self._state.get("history"), list):
                self._state["history"] = []
        if self._state.get("enabled") or self._state.get("enabled_at_ts"):
            self._apply(False, by="system", reason=RESTART_RESET_REASON)
            self._persist()

    def _apply(self, enabled: bool, *, by: str, reason: str) -> None:
        now = float(self._clock())
        self._state["enabled"] = bool(enabled)
        self._state["enabled_at_ts"] = now if enabled else None
        self._state["last_change_ts"] = now
        self._state["last_change_by"] = str(by or "unknown")[:64]
        self._state["last_reason"] = str(reason or "")[:160]
        hist = list(self._state.get("history") or [])
        hist.append({"ts": now, "enabled": bool(enabled), "by": self._state["last_change_by"],
                     "reason": self._state["last_reason"]})
        self._state["history"] = hist[-50:]

    def _persist(self) -> bool:
        ok = _atomic_write_json(self.path, {"schema": OUTPUT_SCHEMA, "updated_ts": self._clock(),
                                            "state": self._state})
        self.persist_ok = ok
        return ok

    def set(self, enabled: bool, *, by: str = "operator", reason: str = "") -> dict:
        """Set output ON/OFF. ON is refused (stays OFF) if it cannot be persisted."""
        with self._lock:
            self._apply(bool(enabled), by=by, reason=reason or ("OPERATOR_ON" if enabled else "OPERATOR_OFF"))
            if not self._persist() and enabled:
                self._apply(False, by="system", reason="OUTPUT_PERSIST_FAILED_FAIL_CLOSED")
                self._persist()
            return self.snapshot_locked()

    def snapshot_locked(self) -> dict:
        s = dict(self._state)
        s["history"] = list(s.get("history") or [])[-10:]
        s["schema"] = OUTPUT_SCHEMA
        s["persist_ok"] = self.persist_ok
        return s

    def snapshot(self) -> dict:
        with self._lock:
            return self.snapshot_locked()

    @property
    def enabled(self) -> bool:
        with self._lock:
            return bool(self._state.get("enabled"))

    @property
    def enabled_at_ts(self) -> float | None:
        with self._lock:
            ts = self._state.get("enabled_at_ts")
        try:
            return float(ts) if ts else None
        except (TypeError, ValueError):
            return None


# ---------------------------------------------------------------------------
# Exchange-protection evidence (set ONLY from real executor confirmations)
# ---------------------------------------------------------------------------
class ProtectionEvidence:
    """``stop_coverage_verified`` / ``reduce_only_supported`` from real reports.

    Each flag is true only while a verified STOP_CONFIRMED report (an exchange
    reduce-only stop order the executor read back from Bitfinex) is younger
    than ``PROTECTION_EVIDENCE_TTL_SEC``. Nothing else can set them.
    """

    def __init__(self, path: Path | str | None = None, clock=time.time):
        self.path = Path(path) if path is not None else data_path(PROTECTION_SIDECAR)
        self._clock = clock
        self._lock = threading.Lock()
        self._evidence: dict = {}
        raw = _read_json(self.path)
        if raw and isinstance(raw.get("evidence"), dict):
            self._evidence = dict(raw["evidence"])

    def record_stop_confirmation(self, report: Mapping[str, Any]) -> bool:
        """Accept a verified STOP_CONFIRMED report; returns True when recorded."""
        if str(report.get("type") or "") != "STOP_CONFIRMED":
            return False
        stop = report.get("stop") if isinstance(report.get("stop"), Mapping) else {}
        try:
            stop_price = float(stop.get("price"))
            qty = abs(float(stop.get("qty")))
        except (TypeError, ValueError):
            return False
        if not (stop.get("reduce_only") is True and stop.get("exchange_order_id")
                and stop.get("verified_on_exchange") is True and stop_price > 0 and qty > 0):
            return False
        now = float(self._clock())
        row = {
            "confirmed_at_ts": now,
            "correlation_id": str(report.get("correlation_id") or "")[:120],
            "lane": str(report.get("lane") or "").upper(),
            "account": str(report.get("account") or "")[:64],
            "exchange_order_id": str(stop.get("exchange_order_id"))[:40],
            "stop_price": stop_price,
            "stop_bp_from_entry": stop.get("bp_from_entry"),
        }
        with self._lock:
            self._evidence["last_stop_confirmation"] = row
            lanes = dict(self._evidence.get("lanes") or {})
            if row["lane"]:
                lanes[row["lane"]] = row
            self._evidence["lanes"] = lanes
            _atomic_write_json(self.path, {"schema": PROTECTION_SCHEMA, "evidence": self._evidence})
        return True

    def flags(self, lane: str | None = None, now: float | None = None) -> dict:
        now = float(self._clock() if now is None else now)
        with self._lock:
            ev = dict(self._evidence)
        last = ev.get("last_stop_confirmation") if isinstance(ev.get("last_stop_confirmation"), dict) else None
        lane_row = (ev.get("lanes") or {}).get(str(lane or "").upper()) if lane else None
        def fresh(row):
            try:
                return bool(row) and now - float(row.get("confirmed_at_ts") or 0) <= PROTECTION_EVIDENCE_TTL_SEC
            except (TypeError, ValueError):
                return False
        account_ok = fresh(last)
        return {
            "schema": PROTECTION_SCHEMA,
            # A reduce-only stop accepted and read back on Bitfinex proves the
            # venue/account supports reduce-only; coverage for a lane needs a
            # confirmation for that lane (or any lane when lane is None).
            "reduce_only_supported": account_ok,
            "stop_coverage_verified": fresh(lane_row) if lane else account_ok,
            "last_stop_confirmation": last,
            "lane_stop_confirmation": lane_row,
            "ttl_sec": PROTECTION_EVIDENCE_TTL_SEC,
        }


# ---------------------------------------------------------------------------
# Per-trade copy decision (first entry event decides; persisted)
# ---------------------------------------------------------------------------
class TradeDecisions:
    """Remembers, per trade id, whether its entry was approved for live copy.

    Approved trades keep emitting their lifecycle (incl. exits) even if a
    switch is turned OFF later; denied/undecided trades never emit anything.
    """

    MAX_ROWS = 5000

    def __init__(self, path: Path | str | None = None, clock=time.time):
        self.path = Path(path) if path is not None else data_path(DECISIONS_SIDECAR)
        self._clock = clock
        self._lock = threading.Lock()
        self._rows: "collections.OrderedDict[str, dict]" = collections.OrderedDict()
        raw = _read_json(self.path)
        if raw and isinstance(raw.get("rows"), dict):
            for k, v in raw["rows"].items():
                if isinstance(v, dict):
                    self._rows[str(k)] = v

    def get(self, trade_id: str) -> dict | None:
        with self._lock:
            row = self._rows.get(str(trade_id or ""))
            return dict(row) if row else None

    def decide(self, trade_id: str, approved: bool, *, lane: str, reasons: Iterable[str],
               approval_ts: float | None = None) -> dict:
        key = str(trade_id or "")
        with self._lock:
            existing = self._rows.get(key)
            if existing is not None:
                return dict(existing)
            row = {"approved": bool(approved), "lane": str(lane or "").upper(),
                   "decided_at_ts": float(self._clock()), "approval_ts": approval_ts,
                   "reasons": list(reasons or [])[:12]}
            self._rows[key] = row
            while len(self._rows) > self.MAX_ROWS:
                self._rows.popitem(last=False)
            ok = _atomic_write_json(self.path, {"schema": DECISIONS_SCHEMA, "rows": self._rows})
            if not ok and approved:
                # Cannot prove the decision durably: fail closed.
                row["approved"] = False
                row["reasons"] = ["DECISION_PERSIST_FAILED"]
            return dict(row)

    def approved_trade_ids(self) -> set[str]:
        with self._lock:
            return {k for k, r in self._rows.items() if r.get("approved")}

    def summary(self, limit: int = 50) -> dict:
        with self._lock:
            rows = list(self._rows.items())[-max(1, int(limit)):]
            approved = sum(1 for _, r in self._rows.items() if r.get("approved"))
            total = len(self._rows)
        return {"schema": DECISIONS_SCHEMA, "total": total, "approved": approved,
                "recent": [{"trade_id": k, **v} for k, v in rows]}


# ---------------------------------------------------------------------------
# Execution-report journal (fills, stops, rejects, errors from the executor)
# ---------------------------------------------------------------------------
class ExecutionJournal:
    """Append-only, fsynced JSONL of verified executor reports + an index."""

    RING = 5000

    def __init__(self, path: Path | str | None = None, clock=time.time):
        self.path = Path(path) if path is not None else data_path(REPORTS_JOURNAL)
        self._clock = clock
        self._lock = threading.Lock()
        self._rows: collections.deque = collections.deque(maxlen=self.RING)
        self._seen: set = set()
        self.write_failures = 0
        self._load()

    def _load(self) -> None:
        try:
            if not self.path.exists():
                return
            with self.path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(row, dict):
                        self._rows.append(row)
                        if row.get("report_id"):
                            self._seen.add(row["report_id"])
        except OSError:
            return

    def append(self, report: Mapping[str, Any]) -> tuple[bool, str]:
        rid = str(report.get("report_id") or "")
        if not rid:
            return False, "REPORT_ID_MISSING"
        rtype = str(report.get("type") or "")
        if rtype not in REPORT_TYPES:
            return False, "REPORT_TYPE_UNKNOWN"
        row = {**dict(report), "fly_received_at_ts": float(self._clock())}
        with self._lock:
            if rid in self._seen:
                return True, "DUPLICATE"
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(row, sort_keys=True, separators=(",", ":"), default=str) + "\n")
                    fh.flush()
                    os.fsync(fh.fileno())
            except OSError:
                self.write_failures += 1
                return False, "JOURNAL_WRITE_FAILED"
            self._rows.append(row)
            self._seen.add(rid)
        return True, "OK"

    def rows(self, since_ts: float | None = None, limit: int | None = None) -> list[dict]:
        with self._lock:
            rows = list(self._rows)
        if since_ts is not None:
            rows = [r for r in rows if float(r.get("fly_received_at_ts") or 0) >= since_ts]
        if limit:
            rows = rows[-int(limit):]
        return rows

    def by_correlation(self) -> dict[str, list[dict]]:
        out: dict[str, list[dict]] = {}
        for row in self.rows():
            out.setdefault(str(row.get("correlation_id") or ""), []).append(row)
        return out


def validate_report(report: Any, now: float) -> tuple[bool, str]:
    """Structural + freshness check for an executor report (after HMAC)."""
    if not isinstance(report, Mapping):
        return False, "REPORT_NOT_OBJECT"
    if report.get("schema") != REPORT_SCHEMA:
        return False, "REPORT_SCHEMA_INVALID"
    for field in ("report_id", "type", "correlation_id", "sent_at_ts"):
        if not report.get(field):
            return False, f"REPORT_{field.upper()}_MISSING"
    if str(report.get("type")) not in REPORT_TYPES:
        return False, "REPORT_TYPE_UNKNOWN"
    try:
        sent = float(report.get("sent_at_ts"))
    except (TypeError, ValueError):
        return False, "REPORT_SENT_AT_INVALID"
    if abs(now - sent) > REPORT_MAX_SKEW_SEC:
        return False, "REPORT_STALE_OR_FUTURE"
    return True, "OK"


# ---------------------------------------------------------------------------
# The gate (pure)
# ---------------------------------------------------------------------------
def exchange_stop_bp_for_spec(spec: Mapping[str, Any] | None) -> float | None:
    """Catastrophe backup: tile hard stop + 25 bp (widest registry hard stop incl. regime profiles)."""
    policy = (spec or {}).get("exit_policy") or {}
    candidates = []
    try:
        if policy.get("hard_stop_bps") is not None:
            candidates.append(float(policy["hard_stop_bps"]))
    except (TypeError, ValueError):
        pass
    for prof in (policy.get("profiles") or {}).values() if isinstance(policy.get("profiles"), dict) else []:
        try:
            if isinstance(prof, dict) and prof.get("hard_bp") is not None:
                candidates.append(float(prof["hard_bp"]))
        except (TypeError, ValueError):
            continue
    if not candidates:
        return None
    bp = max(candidates) + EXCHANGE_STOP_BUFFER_BP
    if bp <= EXCHANGE_STOP_BUFFER_BP or bp > EXCHANGE_STOP_MAX_BP:
        return None
    return round(bp, 4)


def effective_eligibility(spec: Mapping[str, Any] | None, operator_eligible: bool) -> tuple[bool, str | None]:
    """(eligible, denial) from the registry plus the operator's per-tile switch.

    Eligible when the tile is in the active registry AND (the registry marks
    it relay-eligible OR the operator switched live eligibility ON). A
    registry capability block is waived by the operator only for research
    qualification (``BLOCKED_UNQUALIFIED``); protection blocks stay.
    """
    s = spec or {}
    if not s:
        return False, "TILE_NOT_ACTIVE"
    capability = str(s.get("relay_capability") or "")
    registry_ok = bool(s.get("platform_relay_eligible")) and not capability.startswith("BLOCKED")
    if registry_ok:
        return True, None
    if not operator_eligible:
        return False, DENY_TILE_NOT_ELIGIBLE
    if capability.startswith("BLOCKED") and capability not in OPERATOR_WAIVABLE_CAPABILITIES:
        return False, f"CAPABILITY_{capability}"
    return True, None


def entry_gate(
    *,
    lane: str,
    created_at_ts: float | None,
    spec: Mapping[str, Any] | None,
    output: Mapping[str, Any] | None,
    tile_row: Mapping[str, Any] | None,
    tile_eval: Mapping[str, Any] | None,
    force_paper_mode: bool,
    operator_eligible: bool = False,
) -> tuple[bool, list[str]]:
    """Decide whether a NEW copy entry may be emitted. Pure; fail closed.

    ``output``: LiveCopyOutput.snapshot(); ``tile_row``: switch snapshot for
    the lane; ``tile_eval``: BitfinexLiveSwitch.evaluate() for the lane now.
    """
    reasons: list[str] = []
    try:
        created = float(created_at_ts) if created_at_ts is not None else 0.0
    except (TypeError, ValueError):
        created = 0.0
    out = output or {}
    if force_paper_mode:
        reasons.append(DENY_FORCE_PAPER)
    if not out.get("enabled"):
        reasons.append(DENY_OUTPUT_OFF)
    else:
        try:
            out_ts = float(out.get("enabled_at_ts") or 0)
        except (TypeError, ValueError):
            out_ts = 0.0
        if out_ts <= 0:
            reasons.append(DENY_OUTPUT_TS_UNKNOWN)
        elif created <= 0 or created < out_ts:
            reasons.append(DENY_INTENT_PRE_OUTPUT)
    row = tile_row or {}
    if not row.get("bitfinex_live_orders"):
        reasons.append(DENY_TILE_SWITCH_OFF)
    else:
        try:
            allow_ts = float(row.get("last_allow_ts") or 0)
        except (TypeError, ValueError):
            allow_ts = 0.0
        if allow_ts <= 0 or created <= 0 or created < allow_ts:
            reasons.append(DENY_TILE_PRE_ARMING)
    s = spec or {}
    elig_ok, elig_reason = effective_eligibility(s, operator_eligible)
    if not elig_ok:
        reasons.append(elig_reason or DENY_TILE_NOT_ELIGIBLE)
    ev = tile_eval or {}
    if not ev.get("eligible"):
        reasons.append(DENY_TILE_READINESS)
        reasons.extend(f"{DENY_TILE_READINESS}:{d}" for d in (ev.get("denials") or [])[:10])
    if exchange_stop_bp_for_spec(s) is None:
        reasons.append("EXCHANGE_STOP_BP_UNKNOWN")
    return (not reasons), reasons


def build_approval(
    *,
    event: str,
    trade_id: str,
    lane: str,
    spec: Mapping[str, Any] | None,
    output: Mapping[str, Any] | None,
    tile_row: Mapping[str, Any] | None,
    entry_allowed: bool,
    trade_approved_at_ts: float | None,
    created_at_ts: float,
    signal_at_ts: float | None,
    reasons: Iterable[str] = (),
    bot_instance_id: str | None = None,
    operator_eligible: bool = False,
) -> dict:
    s = spec or {}
    out = output or {}
    row = tile_row or {}
    stop_bp = exchange_stop_bp_for_spec(s)
    return {
        "schema": APPROVAL_SCHEMA,
        "correlation_id": str(trade_id),
        "trade_id": str(trade_id),
        "event": str(event),
        "research_lane": str(lane or "").upper(),
        "policy_signature": s.get("policy_signature"),
        "relay_eligible": bool(effective_eligibility(s, operator_eligible)[0]),
        "eligibility_source": ("REGISTRY" if s.get("platform_relay_eligible") else
                               "OPERATOR" if operator_eligible else "NONE"),
        "entry_allowed": bool(entry_allowed),
        "continuation": str(event) in CONTINUATION_EVENTS,
        "output_on": bool(out.get("enabled")),
        "output_on_at_ts": out.get("enabled_at_ts"),
        "tile_live_on": bool(row.get("bitfinex_live_orders")),
        "tile_allow_ts": row.get("last_allow_ts"),
        "trade_approved_at_ts": trade_approved_at_ts,
        "signal_at_ts": signal_at_ts,
        "created_at_ts": float(created_at_ts),
        "max_margin_usd": MAX_MARGIN_USD,
        "leverage": REQUIRED_LEVERAGE,
        "order_type": "LIMIT",
        "hard_stop_bp": (stop_bp - EXCHANGE_STOP_BUFFER_BP) if stop_bp is not None else None,
        "exchange_stop_bp": stop_bp,
        "exchange_stop_role": "CATASTROPHE_BACKUP",
        "exchange_stop_never_moved": EXCHANGE_STOP_NEVER_MOVED,
        "reasons": list(reasons or [])[:12],
        "bot_instance_id": bot_instance_id,
    }


def stamp_or_block(
    *,
    event: str,
    trade_id: str,
    lane: str,
    now: float,
    signal_at_ts: float | None,
    spec: Mapping[str, Any] | None,
    output: Mapping[str, Any] | None,
    tile_row: Mapping[str, Any] | None,
    tile_eval: Mapping[str, Any] | None,
    force_paper_mode: bool,
    decisions: TradeDecisions,
    secret: str,
    bot_instance_id: str | None = None,
    operator_eligible: bool = False,
) -> tuple[dict | None, list[str]]:
    """Return (signed approval, []) when the event may be emitted, else (None, reasons).

    The first entry event of a trade decides (and persists) whether the trade
    is copied. Undecided/denied trades never emit; approved trades emit every
    later lifecycle event with a fresh signed approval (``entry_allowed``
    re-evaluated for entry events, False for continuations).
    """
    try:
        event = str(event or "")
        if event not in ENTRY_EVENTS and event not in CONTINUATION_EVENTS:
            return None, [DENY_UNKNOWN_EVENT]
        if not str(secret or "").strip():
            return None, [DENY_SECRET_MISSING]
        decision = decisions.get(trade_id)
        if decision is None:
            if event not in ENTRY_EVENTS:
                return None, [DENY_TRADE_NOT_APPROVED]
            ok, reasons = entry_gate(
                lane=lane, created_at_ts=now, spec=spec, output=output,
                tile_row=tile_row, tile_eval=tile_eval, force_paper_mode=force_paper_mode,
                operator_eligible=operator_eligible,
            )
            decision = decisions.decide(trade_id, ok, lane=lane, reasons=reasons,
                                        approval_ts=now if ok else None)
            if not decision.get("approved"):
                return None, list(decision.get("reasons") or reasons or [DENY_TRADE_NOT_APPROVED])
        elif not decision.get("approved"):
            return None, [DENY_TRADE_NOT_APPROVED]
        entry_allowed = False
        reasons: list[str] = []
        if event in ENTRY_EVENTS:
            entry_allowed, reasons = entry_gate(
                lane=lane, created_at_ts=now, spec=spec, output=output,
                tile_row=tile_row, tile_eval=tile_eval, force_paper_mode=force_paper_mode,
                operator_eligible=operator_eligible,
            )
            # The trade's first approval is the "intent created after arming"
            # anchor: a later reprice can never be older than it.
        approval = build_approval(
            event=event, trade_id=trade_id, lane=lane, spec=spec, output=output,
            tile_row=tile_row, entry_allowed=entry_allowed,
            trade_approved_at_ts=decision.get("approval_ts"), created_at_ts=now,
            signal_at_ts=signal_at_ts, reasons=reasons, bot_instance_id=bot_instance_id,
            operator_eligible=operator_eligible,
        )
        return sign_approval(approval, secret), []
    except Exception as exc:  # noqa: BLE001 - any error means no copy intent
        return None, [f"{DENY_INTERNAL_ERROR}:{type(exc).__name__}"]


def delivery_check(payload: Mapping[str, Any] | None, *, secret: str, now: float,
                   tile_row: Mapping[str, Any] | None, output: Mapping[str, Any] | None) -> tuple[bool, str | None]:
    """Second layer at delivery time (applies whether output is ON or OFF).

    Every relay record must carry a valid signed approval for its own trade.
    Entry events additionally need output ON and the tile switch ON *now*
    (a switch turned OFF between emit and delivery withholds the entry).
    Continuations of an approved trade always pass (exits must flow).
    """
    p = payload or {}
    approval = p.get("live_copy_approval")
    if not verify_approval(approval, secret):
        return False, "APPROVAL_MISSING_OR_INVALID"
    if str(approval.get("trade_id")) != str(p.get("trade_id")) or str(approval.get("event")) != str(p.get("event")):
        return False, "APPROVAL_IDENTITY_MISMATCH"
    if str(p.get("event")) in ENTRY_EVENTS:
        if not approval.get("entry_allowed"):
            return False, "APPROVAL_ENTRY_NOT_ALLOWED"
        if not (output or {}).get("enabled"):
            return False, DENY_OUTPUT_OFF
        if not (tile_row or {}).get("bitfinex_live_orders"):
            return False, DENY_TILE_SWITCH_OFF
    return True, None


# ---------------------------------------------------------------------------
# Operator per-tile live eligibility (Danish 2026-10-09: every active tile
# selectable). Persisted config, default OFF, never arms anything by itself.
# ---------------------------------------------------------------------------
class LiveEligibility:
    def __init__(self, path: Path | str | None = None, clock=time.time, active_lanes: Iterable[str] | None = None):
        from combo_pathway_config import ACTIVE_TILE_ORDER
        self.path = Path(path) if path is not None else data_path(ELIGIBILITY_SIDECAR)
        self._clock = clock
        self._active = tuple(active_lanes) if active_lanes is not None else tuple(ACTIVE_TILE_ORDER)
        self._lock = threading.Lock()
        self._rows: dict[str, dict] = {}
        self.persist_ok = True
        raw = _read_json(self.path)
        if raw and isinstance(raw.get("rows"), dict):
            for lane, row in raw["rows"].items():
                if isinstance(row, dict) and str(lane).upper() in self._active:
                    self._rows[str(lane).upper()] = dict(row)

    def is_eligible(self, lane: str) -> bool:
        lane = str(lane or "").upper()
        if lane not in self._active:
            return False  # retired / unknown lanes are never eligible
        with self._lock:
            return bool((self._rows.get(lane) or {}).get("live_eligible"))

    def set(self, lane: str, eligible: bool, *, by: str = "dashboard", reason: str = "") -> tuple[bool, dict]:
        lane = str(lane or "").upper()
        if lane not in self._active:
            return False, {"lane": lane, "live_eligible": False, "error": "TILE_NOT_ACTIVE"}
        now = float(self._clock())
        with self._lock:
            prior = dict(self._rows.get(lane) or {})
            row = {"lane": lane, "live_eligible": bool(eligible), "changed_at_ts": now,
                   "changed_by": str(by)[:64], "reason": str(reason or "")[:160]}
            self._rows[lane] = row
            ok = _atomic_write_json(self.path, {"schema": ELIGIBILITY_SCHEMA, "rows": self._rows})
            self.persist_ok = ok
            if not ok and eligible:
                # Cannot persist the operator decision: fail closed.
                if prior:
                    self._rows[lane] = prior
                else:
                    self._rows.pop(lane, None)
                return False, {"lane": lane, "live_eligible": False, "error": "PERSIST_FAILED"}
            return True, dict(row)

    def status(self) -> dict:
        with self._lock:
            rows = {lane: dict(self._rows.get(lane) or {"lane": lane, "live_eligible": False})
                    for lane in self._active}
        return {"schema": ELIGIBILITY_SCHEMA, "rows": rows,
                "eligible_lanes": [l for l, r in rows.items() if r.get("live_eligible")]}


# ---------------------------------------------------------------------------
# Dedicated durable live-copy outbox (separate from the paper relay outbox, so
# paper never waits on the website). Per-trade ordered; removed only on an
# exact durable receipt from the website.
# ---------------------------------------------------------------------------
class LiveCopyOutbox:
    MAX_PENDING = 2000

    def __init__(self, path: Path | str | None = None, clock=time.time):
        self.path = Path(path) if path is not None else data_path(OUTBOX_SIDECAR)
        self._clock = clock
        self._lock = threading.Lock()
        self._pending: dict[str, dict] = {}
        self._seq: dict[str, int] = {}
        self.healthy = True
        self.acked_total = 0
        self.last_ack_ts: float | None = None
        self.last_error: str | None = None
        raw = _read_json(self.path)
        if raw:
            if isinstance(raw.get("pending"), dict):
                self._pending = {k: v for k, v in raw["pending"].items() if isinstance(v, dict)}
            if isinstance(raw.get("seq"), dict):
                self._seq = {str(k): int(v) for k, v in raw["seq"].items() if isinstance(v, int)}

    def _persist_locked(self) -> bool:
        ok = _atomic_write_json(self.path, {"schema": OUTBOX_SCHEMA, "pending": self._pending,
                                            "seq": dict(list(self._seq.items())[-5000:])})
        self.healthy = ok
        return ok

    def enqueue(self, payload: dict) -> dict | None:
        trade_id = str(payload.get("trade_id") or "")
        if not trade_id:
            return None
        with self._lock:
            if len(self._pending) >= self.MAX_PENDING:
                self.last_error = "OUTBOX_FULL"
                return None
            seq = self._seq.get(trade_id, 0) + 1
            self._seq[trade_id] = seq
            payload = dict(payload)
            payload.setdefault("event_seq", seq)
            payload.setdefault("event_id", f"{trade_id}:{payload.get('event')}:{seq}:lc")
            record = {"event_id": payload["event_id"], "trade_id": trade_id, "local_seq": seq,
                      "created_at_unix": float(self._clock()), "attempts": 0,
                      "next_attempt_at_unix": 0.0, "payload": payload}
            self._pending[record["event_id"]] = record
            if not self._persist_locked():
                self._pending.pop(record["event_id"], None)
                return None
            return json.loads(json.dumps(record, default=str))

    def due(self, now: float | None = None, limit: int = 50) -> list[dict]:
        now = float(self._clock() if now is None else now)
        with self._lock:
            heads: dict[str, dict] = {}
            for rec in self._pending.values():
                cur = heads.get(rec["trade_id"])
                if cur is None or rec["local_seq"] < cur["local_seq"]:
                    heads[rec["trade_id"]] = rec
            ready = [r for r in heads.values() if float(r.get("next_attempt_at_unix") or 0) <= now]
            ready.sort(key=lambda r: r["created_at_unix"])
            return json.loads(json.dumps(ready[:limit], default=str))

    def ack(self, event_id: str) -> bool:
        with self._lock:
            if self._pending.pop(event_id, None) is None:
                return False
            self.acked_total += 1
            self.last_ack_ts = float(self._clock())
            self._persist_locked()
            return True

    def fail(self, event_id: str, error: object) -> None:
        now = float(self._clock())
        with self._lock:
            rec = self._pending.get(event_id)
            if rec is None:
                return
            rec["attempts"] = int(rec.get("attempts") or 0) + 1
            rec["next_attempt_at_unix"] = now + min(30.0, 0.5 * (2 ** min(rec["attempts"], 6)))
            rec["last_error"] = str(error)[:240]
            self.last_error = rec["last_error"]
            self._persist_locked()

    def status(self) -> dict:
        now = float(self._clock())
        with self._lock:
            pend = list(self._pending.values())
        oldest = min((r["created_at_unix"] for r in pend), default=None)
        return {"schema": OUTBOX_SCHEMA, "pending": len(pend), "healthy": self.healthy,
                "oldest_pending_age_s": (now - oldest) if oldest else None,
                "acked_total": self.acked_total, "last_ack_ts": self.last_ack_ts,
                "last_error": self.last_error}
