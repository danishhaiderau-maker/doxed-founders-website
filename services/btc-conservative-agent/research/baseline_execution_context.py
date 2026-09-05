"""Build baseline context only from explicit, generation-pinned evidence.

Analyzer-only helper: no exchange calls, inferred costs, or activation.
Source pins must come from the caller's verified canonical generation, not from
the envelopes themselves. An envelope supplies source_id, raw_bytes, row and
row_sha256; membership and both hashes are checked here. Large JSONL ledgers
use a temporary disk-backed row index after streaming full-source verification;
the context builder itself has no filesystem side effects.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_DOWN
from dataclasses import dataclass, field
import hashlib
import json
import math
from pathlib import Path
import shutil
import sqlite3
import tempfile
from typing import Any, Mapping, Sequence

from research.policy_evidence_schema import canonical_json, stable_hash
from research.quantity_execution import validate_signed_quantity_constraints

SCHEMA = "baseline_execution_model_context_v1"
GENERATION_FIELDS = ("manifest_entry_hash", "epoch_id", "source_revision", "deployed_revision",
                     "tile_config_signature", "analyzer_revision", "evaluator_version", "generation_key")
IDENTITY_FIELDS = ("epoch_id", "opportunity_id", "episode_id", "shared_ai_call_id", "event_id")
MAX_SOURCE_BYTES = 16 * 1024 * 1024
MAX_SOURCE_ROWS = 50_000
MAX_LEDGER_ROW_BYTES = 2 * 1024 * 1024
_ROW_PROOF_AUTHORITY = object()


@dataclass(frozen=True)
class _VerifiedLedgerRowProof:
    """Process-local attestation issued only after a full source SHA matches.

    This is not a serializable signature or a substitute for source pins. The
    verifier accepts the exact private type/token, checks its source SHA
    against the generation pin, and independently hashes the selected row.
    """
    source_id: str
    source_sha256: str
    row_sha256: str
    byte_offset: int
    line_number: int
    authority: object = field(repr=False)


class VerifiedLedgerRowIndex:
    """Temporary disk-backed row membership, bounded by one row + 2 MiB cache.

    Every byte of each pinned ledger is streamed exactly once. Rows are made
    queryable only after the full SHA/size succeeds. Source files are never
    modified. The disposable index is removed on success and exception alike.
    """
    def __init__(self):
        self._temporary = tempfile.TemporaryDirectory(prefix="btc-baseline-row-proof-")
        self._connection = sqlite3.connect(str(Path(self._temporary.name) / "rows.sqlite"))
        self._connection.execute("PRAGMA cache_size=-2048")
        self._connection.execute("PRAGMA temp_store=FILE")
        self._connection.execute("""CREATE TABLE evidence_rows (
            source_id TEXT, row_sha TEXT, payload TEXT, byte_offset INTEGER, line_number INTEGER,
            epoch TEXT, opportunity TEXT, episode TEXT, shared TEXT, event TEXT, schema_name TEXT)""")
        self._connection.execute("CREATE INDEX by_row ON evidence_rows(source_id,row_sha)")
        self._connection.execute("CREATE INDEX by_identity ON evidence_rows(epoch,opportunity,episode,shared,event)")
        self._connection.execute("CREATE INDEX by_episode_event ON evidence_rows(epoch,episode,event)")
        self.sources: dict[str, str] = {}
        self._source_snapshots: dict[str, tuple[Path, tuple]] = {}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self._connection.close()
        self._temporary.cleanup()

    @staticmethod
    def _file_identity(path: Path) -> tuple:
        observed = path.stat()
        return (observed.st_dev, observed.st_ino, observed.st_size,
                observed.st_mtime_ns, observed.st_ctime_ns)

    def assert_sources_unchanged(self) -> None:
        for path, expected in self._source_snapshots.values():
            if self._file_identity(path) != expected:
                raise ValueError("BASELINE_CONTEXT_SOURCE_CHANGED_DURING_REPLAY")

    def add_source(self, root: Path, relative: str, *, expected_sha: str, expected_size: int,
                   stage_only: bool = False) -> int:
        path = root / relative
        path.resolve().relative_to(root.resolve())
        if not _hash(expected_sha) or isinstance(expected_size, bool) or not isinstance(expected_size, int) or expected_size < 0:
            raise ValueError("SOURCE_PIN_INVALID")
        if any(part.is_symlink() or (getattr(part.stat(), "st_file_attributes", 0) & 0x400)
               for part in (path, *path.parents)):
            raise ValueError("SOURCE_LINK_FORBIDDEN")
        source_identity = self._file_identity(path)
        if source_identity[2] != expected_size:
            raise ValueError("SOURCE_SIZE_MISMATCH")
        if relative in self.sources:
            raise ValueError("SOURCE_ALREADY_INDEXED")
        digest = hashlib.sha256()
        consumed = count = line_number = 0
        next_space_check = 0
        try:
            self._connection.execute("BEGIN")
            with path.open("rb") as handle:
                while True:
                    raw = handle.readline(MAX_LEDGER_ROW_BYTES + 1)
                    if not raw:
                        break
                    if len(raw) > MAX_LEDGER_ROW_BYTES:
                        raise ValueError("SOURCE_ROW_SIZE_LIMIT")
                    offset = consumed
                    consumed += len(raw)
                    if consumed > expected_size:
                        raise ValueError("SOURCE_GREW_DURING_VERIFICATION")
                    digest.update(raw)
                    line_number += 1
                    if consumed >= next_space_check:
                        if shutil.disk_usage(self._temporary.name).free < 64 * 1024 * 1024:
                            raise ValueError("SOURCE_ROW_INDEX_DISK_HEADROOM")
                        next_space_check = consumed + 8 * 1024 * 1024
                    if not raw.strip():
                        continue
                    row = json.loads(raw.decode("utf-8-sig"))
                    if not isinstance(row, Mapping):
                        raise ValueError("SOURCE_ROW_INVALID")
                    if stage_only and not (row.get("schema") == "compressed_chase_shadow_v1"
                            and row.get("event") == "STAGE" and type(row.get("stage_index")) is int
                            and row["stage_index"] == 0):
                        continue
                    payload = canonical_json(row)
                    if len(payload.encode()) > MAX_LEDGER_ROW_BYTES:
                        raise ValueError("SOURCE_CANONICAL_ROW_SIZE_LIMIT")
                    values = (relative, hashlib.sha256(payload.encode()).hexdigest(), payload, offset, line_number,
                        *(str(row.get(key) or "") for key in IDENTITY_FIELDS), str(row.get("schema") or ""))
                    self._connection.execute("INSERT INTO evidence_rows VALUES (?,?,?,?,?,?,?,?,?,?,?)", values)
                    count += 1
            if consumed != expected_size or digest.hexdigest() != expected_sha:
                raise ValueError("SOURCE_HASH_MISMATCH")
            if self._file_identity(path) != source_identity:
                raise ValueError("SOURCE_CHANGED_DURING_VERIFICATION")
            self._connection.commit()
        except sqlite3.DatabaseError as exc:
            self._connection.rollback()
            raise ValueError("SOURCE_ROW_INDEX_STORAGE_ERROR") from exc
        except Exception:
            self._connection.rollback()
            raise
        self.sources[relative] = expected_sha
        self._source_snapshots[relative] = (path, source_identity)
        return count

    def _envelope(self, record) -> dict:
        source, row_sha, payload, offset, line = record
        if source not in self.sources or hashlib.sha256(payload.encode()).hexdigest() != row_sha:
            raise ValueError("SOURCE_ROW_INDEX_INTEGRITY_FAILED")
        row = json.loads(payload)
        return {"source_id": source, "row": row, "row_sha256": row_sha,
            "verified_row_proof": _VerifiedLedgerRowProof(source, self.sources[source], row_sha, offset, line, _ROW_PROOF_AUTHORITY)}

    def envelope(self, source_id: str, row: Mapping) -> dict | None:
        record = self._connection.execute(
            "SELECT source_id,row_sha,payload,byte_offset,line_number FROM evidence_rows WHERE source_id=? AND row_sha=? LIMIT 1",
            (source_id, _sha(row))).fetchone()
        return self._envelope(record) if record else None

    def identity_envelopes(self, identity: tuple[str, ...]) -> list[dict]:
        # Only stage-zero / explicit baseline context rows are candidates.
        cursor = self._connection.execute("""SELECT source_id,row_sha,payload,byte_offset,line_number
            FROM evidence_rows WHERE epoch=? AND opportunity=? AND episode=? AND shared=? AND event=?
            AND schema_name IN ('compressed_chase_shadow_v1','baseline_sizing_authorization_v1','baseline_fill_atr_observation_v1')
            ORDER BY source_id,line_number LIMIT 129""", identity)
        results = []
        selected_bytes = 0
        for row in cursor:
            selected_bytes += len(row[2])  # canonical JSON is ASCII encoded
            if len(results) >= 128 or selected_bytes > 8 * 1024 * 1024:
                raise ValueError("BASELINE_CONTEXT_IDENTITY_CANDIDATE_LIMIT")
            results.append(self._envelope(row))
        return results

    def pre_entry_feature_envelopes(self, epoch: str, opportunity: str, episode: str) -> list[dict]:
        """Return zero/one receipt or an ambiguity witness from a verified source.

        Two matches already prove ambiguity; no caller may treat this bounded
        result as a complete receipt collection or choose one of the duplicates.
        """
        source = "v3/ledgers/pre_entry_features.jsonl"
        if source not in self.sources or not all((epoch, opportunity, episode)):
            return []
        cursor = self._connection.execute("""SELECT source_id,row_sha,payload,byte_offset,line_number
            FROM evidence_rows WHERE source_id=? AND epoch=? AND opportunity=? AND episode=?
            ORDER BY line_number LIMIT 2""", (source, epoch, opportunity, episode))
        return [self._envelope(row) for row in cursor]

    def lifecycle_envelopes(self, epoch: str, episode: str) -> list[dict]:
        """Return all bounded per-event lifecycle rows, never a sampled prefix."""
        cursor = self._connection.execute("""SELECT source_id,row_sha,payload,byte_offset,line_number
            FROM evidence_rows WHERE epoch=? AND episode=?
            AND source_id GLOB 'v3/ledgers/lifecycle.jsonl*'
            ORDER BY event,source_id,line_number LIMIT 129""", (epoch, episode))
        result = []
        size = 0
        for row in cursor:
            size += len(row[2])
            if len(result) >= 128 or size > 8 * 1024 * 1024:
                raise ValueError("SIGNAL_SNAPSHOT_LIFECYCLE_GROUP_LIMIT")
            result.append(self._envelope(row))
        return result

    def read_pinned_object(self, root: Path, relative: str, *, expected_sha: str,
                           expected_size: int, max_bytes: int) -> bytes:
        """Verify one bounded dependency and fence it until report completion."""
        path = root / relative
        path.resolve().relative_to(root.resolve())
        if (not _hash(expected_sha) or type(expected_size) is not int
                or not 0 < expected_size <= max_bytes):
            raise ValueError("SIGNAL_SNAPSHOT_SOURCE_PIN_INVALID")
        if any(part.is_symlink() or (getattr(part.stat(), "st_file_attributes", 0) & 0x400)
               for part in (path, *path.parents)):
            raise ValueError("SOURCE_LINK_FORBIDDEN")
        before = self._file_identity(path)
        with path.open("rb") as handle:
            raw = handle.read(max_bytes + 1)
        if (len(raw) != expected_size or hashlib.sha256(raw).hexdigest() != expected_sha
                or self._file_identity(path) != before):
            raise ValueError("SIGNAL_SNAPSHOT_PINNED_OBJECT_MISMATCH")
        previous = self._source_snapshots.get(relative)
        if previous is not None and previous != (path, before):
            raise ValueError("SIGNAL_SNAPSHOT_SOURCE_CHANGED_DURING_REPLAY")
        self._source_snapshots[relative] = (path, before)
        return raw


def _sha(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def _decimal(value: Any, *, positive=False) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise ValueError("BASELINE_CONTEXT_NUMBER_INVALID")
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("BASELINE_CONTEXT_NUMBER_INVALID") from exc
    if not result.is_finite() or result < 0 or positive and result <= 0:
        raise ValueError("BASELINE_CONTEXT_NUMBER_INVALID")
    return result


def _hash(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def _verified(envelope: Mapping[str, Any], pins: Mapping[str, str]) -> tuple[dict, str]:
    if not isinstance(envelope, Mapping):
        raise ValueError("BASELINE_CONTEXT_SOURCE_MISSING")
    source_id = envelope.get("source_id")
    digest = pins.get(source_id) if isinstance(source_id, str) else None
    proof = envelope.get("verified_row_proof")
    if proof is not None:
        row = envelope.get("row")
        if (type(proof) is not _VerifiedLedgerRowProof or proof.authority is not _ROW_PROOF_AUTHORITY
                or not _hash(digest) or proof.source_id != source_id or proof.source_sha256 != digest
                or not isinstance(row, Mapping) or envelope.get("row_sha256") != proof.row_sha256
                or _sha(row) != proof.row_sha256 or proof.byte_offset < 0 or proof.line_number < 1):
            raise ValueError("BASELINE_CONTEXT_STREAM_ROW_PROOF_INVALID")
        return dict(row), digest
    raw = envelope.get("raw_bytes")
    if not _hash(digest) or not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_SOURCE_BYTES:
        raise ValueError("BASELINE_CONTEXT_SOURCE_PIN_OR_LIMIT_INVALID")
    if hashlib.sha256(raw).hexdigest() != digest:
        raise ValueError("BASELINE_CONTEXT_SOURCE_HASH_MISMATCH")
    row = envelope.get("row")
    if not isinstance(row, Mapping) or envelope.get("row_sha256") != _sha(row):
        raise ValueError("BASELINE_CONTEXT_ROW_HASH_MISMATCH")
    try:
        parsed = json.loads(raw.decode("utf-8-sig"))
        rows = parsed if isinstance(parsed, list) else [parsed]
    except json.JSONDecodeError:
        lines = raw.decode("utf-8-sig").splitlines()
        if len(lines) > MAX_SOURCE_ROWS:
            raise ValueError("BASELINE_CONTEXT_SOURCE_ROW_LIMIT")
        rows = [json.loads(line) for line in lines if line.strip()]
    if len(rows) > MAX_SOURCE_ROWS:
        raise ValueError("BASELINE_CONTEXT_SOURCE_ROW_LIMIT")
    if not any(isinstance(item, Mapping) and _sha(item) == envelope["row_sha256"] for item in rows):
        raise ValueError("BASELINE_CONTEXT_ROW_NOT_IN_PINNED_SOURCE")
    return dict(row), digest


def _identity(row: Mapping[str, Any], identity: Mapping[str, Any], *, baseline=False) -> None:
    fields = IDENTITY_FIELDS + (("baseline_id", "baseline_policy_signature") if baseline else ())
    if any(not isinstance(identity.get(key), str) or not identity[key] or
           row.get(key) != identity[key] for key in fields):
        raise ValueError("BASELINE_CONTEXT_IDENTITY_MISMATCH")


def _source_generation_matches(row: Mapping[str, Any], generation: Mapping[str, Any]) -> bool:
    # A producer cannot know a future analyzer revision. A persisted source
    # identity is sufficient only when every collection identity matches.
    if "generation" in row:
        return row["generation"] == generation
    source = row.get("source_identity")
    return isinstance(source, Mapping) and all(
        source.get(key) == generation.get(key)
        for key in ("epoch_id", "source_revision", "deployed_revision", "tile_config_signature")
    )


def accepted_fill_position(entry_receipt: Mapping[str, Any]) -> dict[str, Any]:
    """Resolve the position from accepted fills, never the last limit price.

    Exact Decimal event quantities must close to the receipt's filled total.
    The normalized VWAP uses the same float operations/order as the terminal
    evaluator, whose venue-lot check converts that price back to Decimal.
    Exact Decimal notional is retained separately rather than presented as a
    distinct entry price. Rejected attempts are not executions.
    """
    filled = _decimal(entry_receipt.get("filled_qty"), positive=True)
    # Terminal validation still requires valid top-level compatibility fields
    # before it replaces their values with the accepted-event aggregate.
    _decimal(entry_receipt.get("fill_price"), positive=True)
    _decimal(entry_receipt.get("trigger_bucket_ts"), positive=True)
    attempts = entry_receipt.get("quantity_attempts")
    if not isinstance(attempts, (list, tuple)) or len(attempts) > 4096:
        raise ValueError("BASELINE_CONTEXT_ACCEPTED_FILL_EVENTS_MISSING_OR_LIMIT")
    events = []
    for attempt in attempts:
        if not isinstance(attempt, Mapping) or attempt.get("accepted") is not True:
            continue
        try:
            quantity = _decimal(attempt.get("rounded_executable_quantity"), positive=True)
            price = _decimal(attempt.get("execution_price"), positive=True)
            timestamp = _decimal(attempt.get("trigger_bucket_ts"), positive=True)
            if timestamp != int(timestamp):
                raise ValueError("NONINTEGER_EVENT_TIMESTAMP")
            if not all(math.isfinite(float(value)) and float(value) > 0 for value in (quantity, price, timestamp)):
                raise ValueError("NONFINITE_EVENT_FLOAT_PROJECTION")
        except (ValueError, ArithmeticError, OverflowError) as exc:
            raise ValueError("BASELINE_CONTEXT_ACCEPTED_FILL_EVENT_INVALID") from exc
        events.append((timestamp, price, quantity))
    if not events:
        raise ValueError("BASELINE_CONTEXT_ACCEPTED_FILL_EVENTS_MISSING_OR_LIMIT")
    if sum((event[2] for event in events), Decimal(0)) != filled:
        raise ValueError("BASELINE_CONTEXT_ACCEPTED_FILL_QUANTITY_MISMATCH")
    quantity_total = sum(float(event[2]) for event in events)
    vwap = sum(float(price) * float(quantity) for _timestamp, price, quantity in events) / quantity_total
    if not math.isfinite(vwap) or vwap <= 0:
        raise ValueError("BASELINE_CONTEXT_ACCEPTED_FILL_EVENT_INVALID")
    return {"filled_qty": filled, "fill_price": Decimal(str(vwap)),
            "completion_ts": max(event[0] for event in events), "first_fill_ts": min(event[0] for event in events),
            "accepted_event_count": len(events),
            "exact_entry_notional": sum((price * quantity for _timestamp, price, quantity in events), Decimal(0)),
            "basis": "ACCEPTED_FILL_EVENTS_TERMINAL_VWAP_AND_COMPLETION"}


def declared_directional_baseline_inputs(capture: Mapping, baseline: Mapping) -> dict:
    """Project only an explicitly captured research scenario, never defaults."""
    from research_v3_contract import canonical_hash
    if (capture.get("capture_signature") != canonical_hash("directional-entry-capture",
            {key: value for key, value in capture.items() if key != "capture_signature"}, length=64)
            or capture.get("direction") not in {"LONG", "SHORT"}):
        raise ValueError("DECLARED_BASELINE_CAPTURE_INVALID")
    declaration = capture.get("research_context_declaration")
    if not isinstance(declaration, Mapping) or declaration.get("schema") != "research_baseline_context_declaration_v1":
        raise ValueError("DECLARED_BASELINE_CONTEXT_DECLARATION_MISSING")
    if (declaration.get("evidence_basis") != "DECLARED_SIMULATION"
            or not str(declaration.get("provenance") or "").strip()):
        raise ValueError("DECLARED_BASELINE_PROVENANCE_REQUIRED")
    signal_ts = _decimal(capture.get("signal_ts"), positive=True)
    if _decimal(declaration.get("declared_at_ts"), positive=True) > signal_ts:
        raise ValueError("DECLARED_BASELINE_POST_SIGNAL_DECLARATION")
    envelope = (capture.get("schedules") or {}).get(baseline.get("baseline_id")) or {}
    schedule = envelope.get("schedule") or []
    if (not schedule or envelope.get("policy_signature") != baseline.get("policy_signature")
            or envelope.get("episode_id") != capture.get("episode_id")):
        raise ValueError("DECLARED_BASELINE_SCHEDULE_MISSING")
    basis_price = _decimal(schedule[0].get("limit_price"), positive=True)
    constraints, defects = validate_signed_quantity_constraints(declaration.get("signed_quantity_constraints"), symbol=capture.get("symbol"))
    if defects or constraints is None or constraints.get("source_revision") != capture.get("source_revision"):
        raise ValueError("DECLARED_BASELINE_QUANTITY_CONSTRAINTS_INVALID")
    from datetime import datetime
    try:
        captured = datetime.fromisoformat(str(constraints["captured_at"]).replace("Z", "+00:00"))
        if captured.tzinfo is None or _decimal(captured.timestamp(), positive=True) > signal_ts:
            raise ValueError("DECLARED_BASELINE_QUANTITY_METADATA_NOT_CAUSAL")
    except (ValueError, TypeError, OverflowError) as exc:
        raise ValueError("DECLARED_BASELINE_QUANTITY_METADATA_NOT_CAUSAL") from exc
    leverage = _decimal(declaration.get("leverage"), positive=True)
    step = Decimal(constraints["quantity_step"])
    mode = declaration.get("sizing_mode")
    if mode == "FIXED_MARGIN":
        margin = _decimal(declaration.get("margin_usd"), positive=True)
        quantity = (margin * leverage / basis_price / step).to_integral_value(rounding=ROUND_DOWN) * step
    elif mode == "FIXED_QUANTITY":
        quantity = _decimal(declaration.get("requested_qty"), positive=True)
        if quantity % step:
            raise ValueError("DECLARED_BASELINE_QUANTITY_STEP_MISMATCH")
        margin = quantity * basis_price / leverage
    else:
        raise ValueError("DECLARED_BASELINE_SIZING_MODE_INVALID")
    if quantity <= 0:
        raise ValueError("DECLARED_BASELINE_QUANTITY_BELOW_STEP")
    latency = _decimal(declaration.get("input_latency_sec"))
    # Existing replay preserves its timestamps; nonzero added latency needs a
    # separate timing treatment and cannot be asserted by a label alone.
    if latency != 0:
        raise ValueError("DECLARED_BASELINE_LATENCY_TREATMENT_UNSUPPORTED")
    fees = _decimal(declaration.get("input_fee_assumption_usd"))
    slippage = declaration.get("slippage_model")
    if slippage != "EXECUTABLE_BBO_PRICES":
        raise ValueError("DECLARED_BASELINE_SLIPPAGE_TREATMENT_UNSUPPORTED")
    atr = declaration.get("atr") or {}
    if (atr.get("basis") != "DECLARED_SIGNAL_ATR_HOLD_CONSTANT"
            or not str(atr.get("provenance") or "").strip()
            or _decimal(atr.get("observed_ts"), positive=True) > signal_ts
            or _decimal(atr.get("observed_ts"), positive=True) > _decimal(atr.get("available_at_ts"), positive=True)
            or _decimal(atr.get("available_at_ts"), positive=True) > signal_ts):
        raise ValueError("DECLARED_BASELINE_SIGNAL_ATR_NOT_CAUSAL")
    _decimal(atr.get("atr_pct"), positive=True)
    coverage = declaration.get("coverage_policy") or {}
    interval = _decimal(coverage.get("sampling_interval_sec"), positive=True)
    offset = _decimal(coverage.get("first_sample_offset_sec"), positive=True)
    horizon = _decimal(coverage.get("required_horizon_sec"), positive=True)
    if interval not in (1, 2) or offset != int(offset) or offset > interval or horizon > MAX_SOURCE_ROWS * interval:
        raise ValueError("DECLARED_BASELINE_COVERAGE_POLICY_INVALID")
    return {"requested_qty": float(quantity), "signed_quantity_constraints": declaration["signed_quantity_constraints"],
            "latency_sec": float(latency), "fees_usd": float(fees), "slippage_model": slippage,
            "declaration": dict(declaration), "requested_margin_usd": str(margin),
            "required_horizon_end_ts": float(signal_ts + horizon),
            "quantity_basis_price": str(basis_price), "declaration_sha256": _sha(declaration)}


def build_declared_directional_baseline_context(*, generation: Mapping, identity: Mapping,
        entry_receipt: Mapping, capture: Mapping, baseline: Mapping, pinned_sources: Mapping,
        opportunity_binding: Mapping, coverage_evidence: Mapping, coverage_binding: Mapping) -> dict:
    """Verified public-tape position under a labelled signal-ATR scenario."""
    try:
        if any(not isinstance(generation.get(key), str) or not generation[key] for key in GENERATION_FIELDS):
            raise ValueError("BASELINE_CONTEXT_GENERATION_MISSING")
        inputs = declared_directional_baseline_inputs(capture, baseline)
        declaration = inputs["declaration"]
        if (any(capture.get(key) != generation.get(key) for key in
                ("epoch_id", "source_revision", "deployed_revision", "tile_config_signature"))
                or identity.get("epoch_id") != generation["epoch_id"]
                or identity.get("direction") != capture.get("direction")
                or identity.get("symbol") != capture.get("symbol")
                or identity.get("baseline_id") != baseline.get("baseline_id")
                or identity.get("baseline_policy_signature") != baseline.get("policy_signature")):
            raise ValueError("DECLARED_BASELINE_GENERATION_IDENTITY_MISMATCH")
        opportunity, opportunity_hash = _verified(opportunity_binding, pinned_sources)
        if any(opportunity.get(key) != generation.get(key) for key in
               ("epoch_id", "source_revision", "deployed_revision", "tile_config_signature")):
            raise ValueError("DECLARED_BASELINE_SOURCE_GENERATION_MISMATCH")
        stored = ((opportunity.get("baseline_schedule_snapshot") or {}).get("directional_schedules") or {}).get(identity.get("direction"))
        if (stored != capture or capture.get("source_episode_id") != opportunity.get("episode_id")
                or capture.get("opportunity_id") != (opportunity.get("opportunity_id") or opportunity.get("record_id"))
                or capture.get("episode_id") != identity.get("episode_id")
                or capture.get("direction") != entry_receipt.get("direction")
                or capture.get("symbol") != entry_receipt.get("symbol")
                or opportunity.get("research_baseline_context_declaration") != declaration):
            raise ValueError("DECLARED_BASELINE_SOURCE_CAPTURE_MISMATCH")
        if entry_receipt.get("supported") is not True or entry_receipt.get("final_classification") not in {"FULL_FILL", "PARTIAL_FILL"}:
            raise ValueError("BASELINE_CONTEXT_SUPPORTED_FILL_REQUIRED")
        from research.conservative_limit_fill import _normalise_schedule
        schedule = capture["schedules"][baseline["baseline_id"]]["schedule"]
        if _normalise_schedule(schedule)[1] != entry_receipt.get("schedule_sha256"):
            raise ValueError("DECLARED_BASELINE_ENTRY_SCHEDULE_MISMATCH")
        constraints, defects = validate_signed_quantity_constraints(entry_receipt.get("quantity_constraints"), symbol=capture.get("symbol"))
        expected_constraints, _ = validate_signed_quantity_constraints(inputs["signed_quantity_constraints"], symbol=capture.get("symbol"))
        if defects or constraints != expected_constraints or _decimal(entry_receipt.get("requested_qty")) != _decimal(inputs["requested_qty"]):
            raise ValueError("DECLARED_BASELINE_ENTRY_QUANTITY_MISMATCH")
        position = accepted_fill_position(entry_receipt)
        fill_ts, filled, fill_price = position["completion_ts"], position["filled_qty"], position["fill_price"]
        if fill_ts < _decimal(capture["signal_ts"]) or filled > _decimal(inputs["requested_qty"]):
            raise ValueError("DECLARED_BASELINE_FILL_INVALID")
        segment, segment_hash = _verified(coverage_evidence, pinned_sources)
        binding, binding_hash = _verified(coverage_binding, pinned_sources)
        parent_identity = {key: opportunity.get(key) for key in IDENTITY_FIELDS}
        _identity(binding, parent_identity)
        reference = binding.get("segment_ref") or {}
        if (segment.get("schema") != "market_segment_v3" or segment.get("symbol") != capture.get("symbol")
                or reference.get("sha256") != segment_hash
                or reference.get("relative_path") != coverage_evidence.get("source_id")):
            raise ValueError("DECLARED_BASELINE_COVERAGE_BINDING_MISMATCH")
        policy = declaration["coverage_policy"]
        interval, offset = int(policy["sampling_interval_sec"]), int(policy["first_sample_offset_sec"])
        horizon = _decimal(inputs["required_horizon_end_ts"])
        if horizon <= fill_ts:
            raise ValueError("DECLARED_BASELINE_HORIZON_BEFORE_FILL")
        rows = segment.get("rows")
        if not isinstance(rows, list) or not 0 < len(rows) <= MAX_SOURCE_ROWS:
            raise ValueError("BASELINE_CONTEXT_COVERAGE_ROWS_INVALID")
        selected = {}
        for row in rows:
            ts = _decimal(row.get("bucket_ts"))
            if not fill_ts + offset <= ts <= horizon:
                continue
            if (ts in selected or row.get("schema") != "market_microstructure_1s_v1"
                    or row.get("symbol") != capture.get("symbol") or row.get("fresh") is not True
                    or row.get("valid_bbo") is not True or _decimal(row.get("bid"), positive=True) > _decimal(row.get("ask"), positive=True)
                    or _decimal(row.get("bid_qty"), positive=True) <= 0 or _decimal(row.get("ask_qty"), positive=True) <= 0):
                raise ValueError("DECLARED_BASELINE_COVERAGE_ROW_INVALID")
            for field in ("buy_qty", "sell_qty", "trade_count"):
                _decimal(row.get(field))
            selected[ts] = row
        expected_times = list(range(int(fill_ts + offset), int(horizon) + 1, interval))
        if fill_ts != int(fill_ts) or horizon != int(horizon) or sorted(selected) != [Decimal(ts) for ts in expected_times]:
            raise ValueError("DECLARED_BASELINE_COVERAGE_GAP")
        leverage = _decimal(declaration["leverage"], positive=True)
        # Use the same accepted VWAP representation that the terminal quantity
        # fence consumes; preserve exact event notional separately for audit.
        margin = filled * fill_price / leverage
        step = Decimal(constraints["quantity_step"])
        if (margin * leverage / fill_price / step).to_integral_value(rounding=ROUND_DOWN) * step != filled:
            raise ValueError("BASELINE_CONTEXT_FILLED_MARGIN_PRECISION_MISMATCH")
        atr = declaration["atr"]
        body = {"schema": SCHEMA, "generation": dict(generation), **dict(identity),
                "source_episode_id": capture["source_episode_id"],
                "entry_receipt_sha256": _sha(entry_receipt),
                "position_context_id": stable_hash("declared-baseline-position", {
                    "entry": _sha(entry_receipt), "capture": capture["capture_signature"], "baseline": baseline["policy_signature"]}),
                "requested_qty": str(inputs["requested_qty"]), "filled_qty": str(filled),
                "requested_margin_usd": inputs["requested_margin_usd"], "margin_usd": str(margin),
                "accepted_fill_exact_notional": str(position["exact_entry_notional"]),
                "leverage": str(leverage), "sizing_mode": declaration["sizing_mode"],
                "sizing_provenance": "EXPLICIT_DECLARED_RESEARCH:" + inputs["declaration_sha256"],
                "atr_pct_at_fill": str(atr["atr_pct"]), "atr_basis": "DECLARED_SIGNAL_ATR_HOLD_CONSTANT",
                "atr_provenance": str(atr["provenance"]), "atr_observed_ts": atr["observed_ts"],
                "atr_available_at_ts": atr["available_at_ts"], "measured_fill_atr": None,
                "research_context_declaration_sha256": inputs["declaration_sha256"],
                "directional_capture_signature": capture["capture_signature"],
                "context_evidence_basis": "DECLARED_SIMULATION",
                "timing_basis": "BASELINE_EXECUTION_TIMESTAMPS_UNCHANGED",
                "latency_provenance": "DECLARED_ZERO_ADDITIONAL_LATENCY_PRESERVED_BASELINE_TIMING",
                "sampling_interval_sec": interval, "first_sample_offset_sec": offset,
                "required_horizon_end_ts": float(horizon), "path_start_basis": "FIRST_COMPLETE_SAMPLE_AFTER_ENTRY_FILL",
                "path_end_basis": "DECLARED_REQUIRED_HORIZON", "row_schema": "market_microstructure_1s_v1",
                "source_segment_schema": "market_segment_v3", "require_fresh_bbo": True, "require_trade_fields": True,
                "coverage_provenance": "VERIFIED_TIMESTAMP_CONTINUITY:" + segment_hash,
                "source_evidence_sha256": sorted({opportunity_hash, binding_hash, segment_hash}),
                "live_arming_authorized": False, "qualification_eligible": False}
        return {"status": "SUPPORTED", "context": {**body, "signature": stable_hash("baseline-execution-model-context", body)},
                "reason_codes": [], "live_arming_authorized": False}
    except (ValueError, TypeError, KeyError, ArithmeticError, AttributeError) as exc:
        return {"status": "UNKNOWN", "context": None, "reason_codes": [str(exc) if isinstance(exc, ValueError) else "DECLARED_BASELINE_CONTEXT_INVALID"]}


def build_baseline_execution_context(
    *, generation: Mapping[str, Any], identity: Mapping[str, Any],
    entry_receipt: Mapping[str, Any], pinned_sources: Mapping[str, str],
    stage_zero_evidence: Sequence[Mapping[str, Any]], sizing_authorization: Mapping[str, Any],
    atr_evidence: Mapping[str, Any], coverage_evidence: Mapping[str, Any],
    coverage_binding: Mapping[str, Any] | None = None,
    opportunity_binding: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return SUPPORTED context or explicit UNKNOWN; input data stays untouched.

    Sizing authorization schema baseline_sizing_authorization_v1 explicitly
    binds source-stage policy to target baseline and declares FIXED_QUANTITY or
    FIXED_MARGIN. Its coverage_policy declares the required replay horizon.
    ATR must be baseline_fill_atr_observation_v1, available at the exact fill.
    Coverage is a source-pinned market_segment_v3 containing actual 1s rows.
    """
    try:
        if not isinstance(generation, Mapping) or any(
            not isinstance(generation.get(key), str) or not generation[key] for key in GENERATION_FIELDS
        ):
            raise ValueError("BASELINE_CONTEXT_GENERATION_MISSING")
        if not isinstance(identity, Mapping) or identity.get("epoch_id") != generation["epoch_id"]:
            raise ValueError("BASELINE_CONTEXT_GENERATION_IDENTITY_MISMATCH")
        if not isinstance(entry_receipt, Mapping) or entry_receipt.get("supported") is not True or (
            entry_receipt.get("final_classification") not in {"FULL_FILL", "PARTIAL_FILL"}
        ):
            raise ValueError("BASELINE_CONTEXT_SUPPORTED_FILL_REQUIRED")
        requested = _decimal(entry_receipt.get("requested_qty"), positive=True)
        accepted_position = accepted_fill_position(entry_receipt)
        filled = accepted_position["filled_qty"]
        fill_ts = accepted_position["completion_ts"]
        fill_price = accepted_position["fill_price"]
        if filled > requested:
            raise ValueError("BASELINE_CONTEXT_QUANTITY_INCONSISTENT")
        if (identity.get("direction") not in {"LONG", "SHORT"}
                or entry_receipt.get("direction") != identity.get("direction")
                or entry_receipt.get("symbol") != identity.get("symbol")):
            raise ValueError("BASELINE_CONTEXT_ENTRY_MARKET_MISMATCH")
        constraints, reasons = validate_signed_quantity_constraints(
            entry_receipt.get("quantity_constraints"), symbol=identity.get("symbol"),
        )
        if reasons or constraints is None:
            raise ValueError("BASELINE_CONTEXT_QUANTITY_CONSTRAINTS_INVALID")
        if constraints.get("source_revision") != generation["source_revision"]:
            raise ValueError("BASELINE_CONTEXT_QUANTITY_CONSTRAINT_REVISION_MISMATCH")
        if len(stage_zero_evidence) > 32 or not stage_zero_evidence:
            raise ValueError("BASELINE_CONTEXT_STAGE_ZERO_COUNT_INVALID")
        stage_versions = {}
        evidence_hashes = set()
        if opportunity_binding is not None:
            opportunity, opportunity_hash = _verified(opportunity_binding, pinned_sources)
            _identity(opportunity, identity)
            if any(opportunity.get(key) != generation.get(key) for key in
                   ("source_revision", "deployed_revision", "tile_config_signature")):
                raise ValueError("BASELINE_CONTEXT_OPPORTUNITY_GENERATION_MISMATCH")
            evidence_hashes.add(opportunity_hash)
        for source in stage_zero_evidence:
            row, source_hash = _verified(source, pinned_sources)
            _identity(row, identity)
            if (row.get("schema") != "compressed_chase_shadow_v1" or row.get("event") != "STAGE"
                    or type(row.get("stage_index")) is not int or row["stage_index"] != 0
                    or row.get("identity_complete") is not True or row.get("missing_identity_fields")
                    or row.get("event_source_revision") != generation["source_revision"]
                    or row.get("event_config_signature") != generation["tile_config_signature"]
                    or row.get("direction") != identity.get("direction")):
                raise ValueError("BASELINE_CONTEXT_STAGE_ZERO_INVALID")
            stage_versions[_sha(row)] = row
            evidence_hashes.add(source_hash)
        if len(stage_versions) != 1:
            raise ValueError("BASELINE_CONTEXT_STAGE_ZERO_CONFLICT")
        stage_hash, stage = next(iter(stage_versions.items()))
        if stage.get("signed_quantity_constraints") != entry_receipt.get("quantity_constraints"):
            # Normalization is allowed only if the independently valid signed
            # constraint payloads are equal, never from a different venue lot.
            stage_constraints, defects = validate_signed_quantity_constraints(
                stage.get("signed_quantity_constraints"), symbol=identity.get("symbol"))
            if defects or stage_constraints != constraints:
                raise ValueError("BASELINE_CONTEXT_QUANTITY_CONSTRAINT_CONFLICT")
        sizing, source_hash = _verified(sizing_authorization, pinned_sources)
        evidence_hashes.add(source_hash)
        _identity(sizing, identity, baseline=True)
        if (sizing.get("schema") != "baseline_sizing_authorization_v1"
                or not _source_generation_matches(sizing, generation)
                or sizing.get("source_stage_zero_row_sha256") != stage_hash
                or sizing.get("source_policy_signature") != stage.get("policy_signature")
                or not str(stage.get("policy_signature") or "")):
            raise ValueError("BASELINE_CONTEXT_SIZING_AUTHORIZATION_INVALID")
        signal_ts = _decimal(stage.get("signal_ts"), positive=True)
        if (_decimal(sizing.get("declared_at_ts")) > signal_ts
                or signal_ts > accepted_position["first_fill_ts"]):
            raise ValueError("BASELINE_CONTEXT_SIZING_NOT_CAUSAL")
        leverage = _decimal(stage.get("leverage"), positive=True)
        original_margin = _decimal(stage.get("requested_margin_usd"), positive=True)
        if requested != _decimal(stage.get("requested_qty"), positive=True):
            raise ValueError("BASELINE_CONTEXT_CROSS_POLICY_QUANTITY_FORBIDDEN")
        mode = sizing.get("sizing_mode")
        if mode == "FIXED_MARGIN":
            # FIXED_MARGIN must authorize the target baseline's sizing price
            # and exact schedule. A source policy's limit alone is not a
            # baseline sizing declaration (unlike explicit FIXED_QUANTITY).
            basis_price = _decimal(sizing.get("quantity_basis_price"), positive=True)
            if (not entry_receipt.get("schedule_sha256")
                    or sizing.get("baseline_schedule_sha256") != entry_receipt["schedule_sha256"]):
                raise ValueError("BASELINE_CONTEXT_FIXED_MARGIN_SCHEDULE_UNBOUND")
            step = Decimal(constraints["quantity_step"])
            calculated = (original_margin * leverage / basis_price / step).to_integral_value(rounding=ROUND_DOWN) * step
            expected = (requested / step).to_integral_value(rounding=ROUND_DOWN) * step
            if expected <= 0 or calculated != expected:
                raise ValueError("BASELINE_CONTEXT_FIXED_MARGIN_QUANTITY_MISMATCH")
        elif mode != "FIXED_QUANTITY":
            raise ValueError("BASELINE_CONTEXT_SIZING_MODE_UNSUPPORTED")
        atr, source_hash = _verified(atr_evidence, pinned_sources)
        evidence_hashes.add(source_hash)
        _identity(atr, identity)
        if (atr.get("schema") != "baseline_fill_atr_observation_v1"
                or not _source_generation_matches(atr, generation)
                or atr.get("symbol") != identity.get("symbol")
                or atr.get("atr_basis") != "EXPLICIT_AT_FILL_OBSERVATION"
                or not str(atr.get("provenance") or "")):
            raise ValueError("BASELINE_CONTEXT_OBSERVED_ATR_REQUIRED")
        if (_decimal(atr.get("observed_ts"), positive=True) != fill_ts
                or _decimal(atr.get("available_at_ts"), positive=True) > fill_ts):
            raise ValueError("BASELINE_CONTEXT_ATR_NOT_EXACT_CAUSAL_FILL")
        atr_pct = _decimal(atr.get("atr_pct"), positive=True)
        segment, source_hash = _verified(coverage_evidence, pinned_sources)
        evidence_hashes.add(source_hash)
        if segment.get("schema") != "market_segment_v3":
            raise ValueError("BASELINE_CONTEXT_COVERAGE_SOURCE_INVALID")
        if coverage_binding is None:
            _identity(segment, identity)
        else:
            binding, binding_hash = _verified(coverage_binding, pinned_sources)
            _identity(binding, identity)
            reference = binding.get("segment_ref")
            if (not isinstance(reference, Mapping) or reference.get("sha256") != source_hash
                    or reference.get("relative_path") != coverage_evidence.get("source_id")
                    or segment.get("symbol") != identity.get("symbol")):
                raise ValueError("BASELINE_CONTEXT_COVERAGE_BINDING_MISMATCH")
            evidence_hashes.add(binding_hash)
        policy = sizing.get("coverage_policy") or {}
        interval = _decimal(policy.get("sampling_interval_sec"), positive=True)
        offset = _decimal(policy.get("first_sample_offset_sec"), positive=True)
        horizon = _decimal(policy.get("required_horizon_end_ts"), positive=True)
        if interval not in (1, 2) or offset != int(offset) or offset > interval or horizon <= fill_ts:
            raise ValueError("BASELINE_CONTEXT_COVERAGE_POLICY_INVALID")
        rows = segment.get("rows")
        if not isinstance(rows, list) or not 0 < len(rows) <= MAX_SOURCE_ROWS:
            raise ValueError("BASELINE_CONTEXT_COVERAGE_ROWS_INVALID")
        selected = []
        seen = set()
        for row in rows:
            timestamp = _decimal(row.get("bucket_ts"))
            if not fill_ts + offset <= timestamp <= horizon:
                continue
            if timestamp in seen:
                raise ValueError("BASELINE_CONTEXT_COVERAGE_DUPLICATE_TIMESTAMP")
            seen.add(timestamp)
            if (row.get("schema") != "market_microstructure_1s_v1" or row.get("symbol") != identity.get("symbol")
                    or row.get("fresh") is not True or row.get("valid_bbo") is not True
                    or _decimal(row.get("bid"), positive=True) > _decimal(row.get("ask"), positive=True)
                    or _decimal(row.get("bid_qty"), positive=True) <= 0
                    or _decimal(row.get("ask_qty"), positive=True) <= 0
                    or any(key not in row for key in ("buy_qty", "sell_qty", "buy_vwap", "sell_vwap", "trade_count"))):
                raise ValueError("BASELINE_CONTEXT_COVERAGE_MARKET_FIELDS_INVALID")
            for key in ("buy_qty", "sell_qty", "trade_count"):
                _decimal(row[key])
            selected.append(timestamp)
        if (not selected or selected[0] != fill_ts + offset or selected[-1] != horizon
                or any(right - left != interval for left, right in zip(selected, selected[1:]))):
            raise ValueError("BASELINE_CONTEXT_REQUIRED_HORIZON_INCOMPLETE")
        # Position margin describes only executed quantity. Original requested
        # quantity and target margin remain separate immutable audit fields.
        filled_margin = filled * fill_price / leverage
        step = Decimal(constraints["quantity_step"])
        reconstructed = (filled_margin * leverage / fill_price / step).to_integral_value(rounding=ROUND_DOWN) * step
        if reconstructed != filled:
            raise ValueError("BASELINE_CONTEXT_FILLED_MARGIN_PRECISION_MISMATCH")
        ledger_membership = []
        for envelope in [*stage_zero_evidence, sizing_authorization, atr_evidence,
                         coverage_evidence, coverage_binding, opportunity_binding]:
            proof = envelope.get("verified_row_proof") if isinstance(envelope, Mapping) else None
            if type(proof) is _VerifiedLedgerRowProof:
                ledger_membership.append({"source_id": proof.source_id, "source_sha256": proof.source_sha256,
                    "row_sha256": proof.row_sha256, "byte_offset": proof.byte_offset, "line_number": proof.line_number,
                    "verification_basis": "VERIFIED_FULL_LEDGER_SHA256_STREAM_V1"})
        body = {"schema": SCHEMA, "generation": dict(generation), **dict(identity),
                "entry_receipt_sha256": _sha(entry_receipt),
                "position_context_id": stable_hash("baseline-position-context", {
                    "identity": dict(identity), "entry_receipt_sha256": _sha(entry_receipt), "sizing_sha256": _sha(sizing)}),
                "requested_qty": str(requested), "filled_qty": str(filled),
                "requested_margin_usd": str(original_margin), "margin_usd": str(filled_margin),
                "leverage": str(leverage), "sizing_mode": mode,
                "sizing_provenance": "EXPLICIT_BASELINE_AUTHORIZATION:" + _sha(sizing),
                "atr_pct_at_fill": str(atr_pct), "atr_basis": "EXPLICIT_AT_FILL_OBSERVATION",
                "atr_provenance": str(atr["provenance"]) + ":" + _sha(atr),
                "timing_basis": "BASELINE_EXECUTION_TIMESTAMPS_UNCHANGED",
                "latency_provenance": "CONSERVATIVE_RECEIPT_TRIGGER_BUCKET_AND_SCHEDULE_UNCHANGED",
                "entry_trigger_bucket_ts": str(fill_ts),
                "accepted_fill_vwap": str(fill_price),
                "accepted_fill_event_count": accepted_position["accepted_event_count"],
                "accepted_fill_exact_notional": str(accepted_position["exact_entry_notional"]),
                "accepted_fill_position_basis": accepted_position["basis"],
                "sampling_interval_sec": int(interval), "first_sample_offset_sec": int(offset),
                "required_horizon_end_ts": float(horizon),
                "path_start_basis": "FIRST_COMPLETE_SAMPLE_AFTER_ENTRY_FILL",
                "path_end_basis": "DECLARED_REQUIRED_HORIZON",
                "row_schema": "market_microstructure_1s_v1", "source_segment_schema": "market_segment_v3",
                "require_fresh_bbo": True, "require_trade_fields": True,
                "coverage_provenance": "VERIFIED_TIMESTAMP_CONTINUITY:" + source_hash,
                "source_evidence_sha256": sorted(evidence_hashes),
                "source_stage_zero_row_sha256": stage_hash,
                "sizing_authorization_row_sha256": _sha(sizing),
                "atr_observation_row_sha256": _sha(atr)}
        if ledger_membership:
            body["verified_ledger_row_membership"] = sorted(ledger_membership,
                key=lambda item: (item["source_id"], item["line_number"], item["row_sha256"]))
        return {"status": "SUPPORTED", "context": {**body, "signature": stable_hash("baseline-execution-model-context", body)},
                "reason_codes": [], "live_arming_authorized": False}
    except (ValueError, TypeError, KeyError, UnicodeError, ArithmeticError, AttributeError, RecursionError) as exc:
        return {"status": "UNKNOWN", "context": None,
                "reason_codes": [str(exc) if isinstance(exc, ValueError) else "BASELINE_CONTEXT_INPUT_INVALID"],
                "live_arming_authorized": False}
