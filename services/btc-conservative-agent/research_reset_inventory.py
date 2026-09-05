"""Read-only research reset inventory. This module never deletes or imports bot.

The caller must authenticate and freeze its runtime before supplying boundary
proof. A structurally valid proof here is NOT authentication or an authorization
to execute the plan. A deleter must revalidate proof, file identities and paths
under its exclusive reset lock. Archive targets carry expected_sha256 from the
bounded original manifest; the executor must check that digest before unlinking.
Unknown paths are deliberately retained.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat

from research_v3_contract import LEDGER_NAMES

SCHEMA = "research_reset_inventory_v1"
PROOF_SCHEMA = "research_reset_boundary_proof_v1"

# Explicit default writer names, not filename-extension or v3-wide permission.
RESEARCH_FILES = frozenset("""
decisions_3factor.csv trades_3factor.csv expired_orders_3factor.csv
blocked_signals_3factor.csv ai_tranche_log.csv setup_log_3factor.csv
candles_3factor.csv pipeline_events_3factor.csv ai_errors_3factor.csv
signal_snapshot.jsonl signal_replay.jsonl trade_outcome.jsonl shadow_outcome.jsonl
type_b_adx_v3_shadow_decisions.jsonl type_b_research_v2.jsonl path_replay.jsonl
post_exit_replay.jsonl research_events_v22.jsonl research_events_v22.provisional.json
cycle_3m_universe.jsonl chase_offset_touch_grid.jsonl order_multiverse.jsonl
opportunity_capture.jsonl source_order_market_evidence.jsonl market_microstructure_1s.jsonl
counterfactual.jsonl approved_but_rejected.jsonl near_miss.jsonl soft_reject_shadow.jsonl
golden_stack_rejections.jsonl trend_health.csv reversal_study.jsonl
ai_reason_research.jsonl ai_confidence_calibration.jsonl trade_lifecycle.jsonl
ai_input_log.jsonl edge_census.jsonl fill_quality.jsonl shadow_vs_live_entry.jsonl
execution_funnel.jsonl lane_opportunity_capture.jsonl ai_edge_disagreement.jsonl
shadow_runner_study.jsonl shadow_lane_outcome.jsonl duplicate_intent_audit.jsonl
pathway_scorecard.json fill_quality_report.json shadow_fill_outcome_report.json
benchmark_vs_lanes_report.json shadow_vs_live_entry_report.json
execution_funnel_summary.json approval_ev_report.json confidence_calibration_report.json
feature_drift_report.json profitable_reject_report.json profitable_reject_features.json
lane_opportunity_capture.json
""".split())
DERIVED_INDEXES = frozenset({
    "research_events_v22.index.json", "research_events_v22.index.sqlite3",
    "v3/qualification_horizon_index.sqlite3",
})
PAST_FILES = frozenset("""
executive_summary.txt research_highlights.txt research_findings.txt research_coverage.txt
research_deep_dive_index.txt analysis_dashboard.html analyzer_run.log
research_compact_summary.json report_manifest.json analyzer_integrity_report.json
research_retention_status.json historical_trade_cohort_report.json
paused_shadow_research_report.json lane_definition_report.json lane_retirement_report.json
manifest.json past_analysis_manifest.json
""".split())
ARCHIVE_PREFIXES = (
    "research_archive", "research_session_archives", "research_retention/daily",
    "research_epoch_quarantine", "epoch_quarantine", "research/genome/epoch_quarantine",
)
ESSENTIAL_NAMES = frozenset({
    "trades_3factor.csv", "expired_orders_3factor.csv",
    "open_positions.json", "paper_lifecycle_v1.json", "positions.json", "orders.json",
    "pending_orders.json", "paper_state.json", "state.json", "research_session.json",
    "policy.json", "spread-gate.json", "pathway_lane_specs.json", "lane_pnl_ledger.json",
    "lane_lab_pnl_ledger.json", "execution_settings_history.jsonl",
    "csv_write_fallback.jsonl", "relay_lifecycle_evidence_v1.json",
})


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def _safe_relative(value):
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        return False
    parts = value.split("/")
    return not value.startswith("/") and all(p not in {"", ".", ".."} for p in parts)


def _link(st):
    return stat.S_ISLNK(st.st_mode) or bool(getattr(st, "st_file_attributes", 0) & 0x400)


def _managed_fly_alias(root, path):
    """Recognize exactly fly-entrypoint.sh's three top-level mount aliases.

    Return the independently safe sibling directory; never traverse the alias.
    The caller opts in only for the Fly runtime layout. A nested, redirected,
    missing or link-backed destination is not a managed alias.
    """
    if (root.name != "runtime" or path.parent != root
            or path.name not in {"research", "research_accumulator", "research_archive"}):
        return None
    expected = root.parent / path.name
    try:
        raw_target = Path(os.readlink(path))
        lexical = Path(os.path.abspath(raw_target if raw_target.is_absolute() else path.parent / raw_target))
        if lexical != expected:
            return None
        for ancestor in (expected, *expected.parents):
            info = ancestor.lstat()
            if _link(info):
                return None
        if not expected.is_dir() or path.resolve(strict=True) != expected:
            return None
        return expected
    except (OSError, ValueError):
        return None


def _essential(relative):
    parts = PurePosixPath(relative.lower()).parts
    name = parts[-1]
    # Archive payload filenames have a numeric sequence prefix.
    name = re.sub(r"^\d{6}_", "", name)
    if name in ESSENTIAL_NAMES:
        return "ESSENTIAL_ORDER_PAPER_OR_ACCOUNTING_STATE"
    if (name.startswith(".env") or any(any(x in p for x in ("credential", "secret", "token", "config")) for p in parts)
            or name.endswith((".pem", ".key", ".pfx", ".p12"))):
        return "ESSENTIAL_CONFIG_OR_CREDENTIAL"
    if (any(p in {".locks", "locks", "emergency_evidence_wal_v2", "emergency_wal_release_acks",
                  "append_heads", "ledger_generations_v1"} for p in parts)
            or name.endswith((".lock", ".pid", "-wal", "-shm"))
            or any(any(x in p for x in ("owner", "recovery", "emergency")) for p in parts)
            or "pending" in name):
        return "ESSENTIAL_RECOVERY_OR_OWNER_STATE"
    return None


def _base_class(relative):
    if relative in DERIVED_INDEXES:
        return "RETIRED_EPOCH_DERIVED_INDEX"
    if relative in RESEARCH_FILES:
        return "RETIRED_EPOCH_RESEARCH_PAYLOAD"
    base, dot, suffix = relative.rpartition(".")
    if dot and suffix.isdigit() and base in RESEARCH_FILES and base.endswith(".jsonl"):
        return "RETIRED_EPOCH_ROTATED_RESEARCH_PAYLOAD"
    parts = PurePosixPath(relative).parts
    if len(parts) == 3 and parts[:2] == ("v3", "ledgers"):
        if any(re.fullmatch(re.escape(n) + r"\.jsonl(?:\.\d+)?", parts[-1]) for n in LEDGER_NAMES):
            return "RETIRED_EPOCH_V3_LEDGER"
    if (len(parts) == 4 and parts[:2] == ("v3", "market_segments")
            and re.fullmatch(r"[0-9a-f]{64}\.json", parts[-1])
            and parts[2] == parts[-1][:2]):
        return "RETIRED_EPOCH_MARKET_SEGMENT"
    # Emergency idempotency receipts remain protected even after retirement;
    # retiring them needs an explicit recovery-specific path, not this planner.
    if (len(parts) == 4 and parts[:3] == ("v3", "receipts", "lifecycle_membership_v1")
            and (parts[3] == "current.json" or re.fullmatch(r"[0-9a-f]{64}\.json", parts[3]))):
        return "RETIRED_EPOCH_DERIVED_INDEX"
    return None


def _proof_valid(root, proof):
    if not isinstance(proof, dict) or proof.get("schema") != PROOF_SCHEMA:
        return False
    if proof.get("runtime_root") != str(root):
        return False
    for key in ("retired_epoch_id", "new_epoch_id", "source_revision", "recovery_receipt_sha256"):
        if not isinstance(proof.get(key), str) or proof[key].strip().upper() in {"", "UNKNOWN", "UNAVAILABLE"}:
            return False
    if proof["retired_epoch_id"] == proof["new_epoch_id"]:
        return False
    if not re.fullmatch(r"[0-9a-f]{64}", proof["recovery_receipt_sha256"]):
        return False
    if any(proof.get(k) is not True for k in ("writers_quiesced", "paper_only", "live_disarmed", "epoch_retired")):
        return False
    return all(type(proof.get(k)) is int and proof[k] == 0 for k in
               ("pending_paper_orders", "open_paper_positions", "pending_wal_records", "pending_recovery_records"))


def plan_research_reset(runtime_root, *, proof=None, max_entries=200_000, max_depth=12,
                        max_metadata_bytes=4 * 1024 * 1024, allow_fly_runtime_aliases=False):
    """Inventory only; no side effects. Unsafe/incomplete scans return no targets.

    Proof is a caller assertion tied to an external recovery receipt digest, not
    an independently verified receipt. Never pass these paths to blanket rmtree.
    Directory removal is intentionally absent; retained children survive.
    """
    for value, ceiling in ((max_entries, 1_000_000), (max_depth, 32), (max_metadata_bytes, 16 * 1024 * 1024)):
        if type(value) is not int or not 0 < value <= ceiling:
            raise ValueError("invalid bounded inventory limit")
    if type(allow_fly_runtime_aliases) is not bool:
        raise ValueError("managed Fly aliases require explicit boolean")
    root = Path(os.path.abspath(os.fspath(runtime_root)))
    if not Path(runtime_root).is_absolute() or root == Path(root.anchor) or root == Path.home():
        raise ValueError("explicit non-broad runtime root required")
    for parent in (root, *root.parents):
        st = parent.lstat()
        if _link(st):
            raise ValueError("symlink/reparse runtime root or ancestor")
    if not root.is_dir():
        raise ValueError("runtime root must exist")
    proof_ok = _proof_valid(root, proof)
    records, errors, metadata, scanned = [], [], {}, 0
    stack = [(root, 0)]
    while stack and not errors:
        directory, depth = stack.pop()
        try:
            with os.scandir(directory) as items:
                for item in items:
                    scanned += 1
                    if scanned > max_entries:
                        errors.append("ENTRY_BUDGET_EXCEEDED")
                        break
                    path = Path(item.path)
                    relative = path.relative_to(root).as_posix()
                    # Windows DirEntry.stat may omit inode/link counts. lstat
                    # gives the identity needed by the exact-path handoff.
                    st = path.lstat()
                    record = {"path": relative, "absolute_path": str(path), "size_bytes": st.st_size,
                              "mtime_ns": st.st_mtime_ns, "device": st.st_dev, "inode": st.st_ino,
                              "link_count": st.st_nlink, "hardlinked": st.st_nlink > 1}
                    if _link(st):
                        managed = _managed_fly_alias(root, path) if allow_fly_runtime_aliases else None
                        if managed is not None:
                            record["verified_sibling_target"] = str(managed)
                            records.append((record, "RETAINED_MANAGED_FLY_ALIAS_NOT_TRAVERSED"))
                            continue
                        records.append((record, "UNSAFE_SYMLINK_OR_REPARSE"))
                        errors.append("UNSAFE_LINK_PRESENT")
                        break
                    if stat.S_ISDIR(st.st_mode):
                        if depth >= max_depth:
                            errors.append("DEPTH_BUDGET_EXCEEDED")
                            break
                        stack.append((path, depth + 1))
                    elif not stat.S_ISREG(st.st_mode):
                        records.append((record, "UNSAFE_SPECIAL_FILE"))
                    else:
                        records.append((record, None))
                        if (relative.startswith("research_archive/") and path.name == "archive_meta.json"
                                and st.st_size <= max_metadata_bytes):
                            metadata[relative] = record
        except OSError as exc:
            errors.append("INVENTORY_IO_ERROR:" + type(exc).__name__)

    # Archive payloads are flattened: recover exact original scope from bounded
    # metadata; never infer scope from a digest filename or numeric prefix alone.
    origins = {}
    metadata_remaining = max_metadata_bytes
    for relative, record in metadata.items():
        try:
            path = root / relative
            before = path.lstat()
            if (_link(before) or before.st_size > metadata_remaining
                    or before.st_ino != record["inode"] or before.st_mtime_ns != record["mtime_ns"]):
                continue
            with path.open("rb") as handle:
                opened = os.fstat(handle.fileno())
                if opened.st_ino != before.st_ino or opened.st_dev != before.st_dev:
                    continue
                raw = handle.read(metadata_remaining + 1)
            metadata_remaining -= len(raw)
            after = path.lstat()
            if metadata_remaining < 0 or _link(after) or after.st_ino != before.st_ino or after.st_mtime_ns != record["mtime_ns"]:
                continue
            meta = json.loads(raw)
            if meta.get("schema") != "research_archive_receipt_v2":
                continue
            rows = meta.get("source_inventory")
            if not isinstance(rows, list) or len(rows) > max_entries:
                continue
            for row in rows:
                if not isinstance(row, dict):
                    continue
                source, preserved = row.get("path"), row.get("preserved_path")
                if not _safe_relative(source) or not _safe_relative(preserved) or not preserved.startswith("payload/"):
                    continue
                key = (PurePosixPath(relative).parent / preserved).as_posix()
                binding = (source, row.get("preserved_sha256"), row.get("preserved_bytes"))
                origins[key] = binding if key not in origins else None
        except (OSError, ValueError, TypeError):
            continue
    targets, retained = [], []
    for record, unsafe in records:
        relative = record["path"]
        reason = unsafe or _essential(relative)
        category = None
        if not reason and relative in origins:
            binding = origins[relative]
            if binding:
                origin, digest, size = binding
                reason = _essential(origin)
                if (_base_class(origin) and not reason and type(size) is int and size == record["size_bytes"]
                        and isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest)):
                    category = "ARCHIVE_RESEARCH_PAYLOAD"
                    record.update(original_path=origin, expected_sha256=digest)
        if not reason and not category:
            category = _base_class(relative)
            parts = PurePosixPath(relative).parts
            if len(parts) >= 3 and parts[0] == "past_analysis" and parts[-1] in PAST_FILES:
                category = "PAST_ANALYSIS_DERIVED"
            for prefix in ARCHIVE_PREFIXES:
                if relative.startswith(prefix + "/") and "/files/" in relative[len(prefix) + 1:]:
                    origin = relative.split("/files/", 1)[1]
                    reason = _essential(origin)
                    if not reason and _base_class(origin):
                        category = "QUARANTINED_RESEARCH_PAYLOAD"
                        record["original_path"] = origin
        if reason or not category:
            retained.append(dict(record, reason=reason or "UNCLASSIFIED_NOT_ALLOWLISTED"))
        elif not proof_ok:
            retained.append(dict(record, reason="EPOCH_RECOVERY_BOUNDARY_PROOF_REQUIRED", category=category))
        else:
            targets.append(dict(record, category=category))
    if errors:
        retained.extend(dict(r, reason="INCOMPLETE_INVENTORY_NO_TARGETS") for r in targets)
        targets = []
    targets.sort(key=lambda r: r["path"])
    retained.sort(key=lambda r: r["path"])
    result = {"schema": SCHEMA, "runtime_root": str(root), "read_only": True,
              "allow_fly_runtime_aliases": allow_fly_runtime_aliases,
              "complete": not errors, "errors": errors, "scanned_entries": scanned,
              "boundary_proof_structurally_valid": proof_ok,
              "proof_authentication": "CALLER_MUST_VERIFY_AUTHORITATIVE_RECEIPT",
              "proof_sha256": _digest(proof) if proof_ok else None,
              "requires_exclusive_reset_lock_and_revalidation": True,
              "targets": targets, "retained": retained,
              "target_count": len(targets), "target_bytes": sum(r["size_bytes"] for r in targets),
              "bytes_basis": "LOGICAL_PATH_BYTES_NOT_PHYSICAL_SPACE_RECLAIMED",
              "physical_bytes_reclaimed": None,
              "hardlinked_target_count": sum(r["hardlinked"] for r in targets)}
    result["plan_sha256"] = _digest(result)
    return result
