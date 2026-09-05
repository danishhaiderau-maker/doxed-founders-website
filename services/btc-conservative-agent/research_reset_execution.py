"""Compose research reset inventory with exact deletion; never authorize a reset.

The authenticated caller owns the reset/reader/writer leases throughout this
call and supplies its verified boundary proof. No authority is inferred here
from file existence, a paused display, or a structurally valid proof alone.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping

from research_exact_deletion import (
    ResearchDeletionRejected, _checked_path, _fingerprint, delete_exact_research_files,
)
from research_reset_inventory import plan_research_reset


def execute_research_reset(*, runtime_root, proof, quiescent: bool,
                           recovery_states: Mapping[str, str], receipt_path,
                           protected_paths=(), expected_plan_sha256=None,
                           max_entries=200000, max_depth=12, max_metadata_bytes=4 * 1024**2,
                           max_files=100000, max_total_bytes=64 * 1024**3) -> dict:
    """Re-plan while quiesced, validate all targets, then unlink exact paths.

    ``proof`` is caller-verified against its authoritative recovery receipt;
    this function validates structure and consistency, not authentication.
    ``recovery_states`` must name the actual recovery owners checked by caller.
    Archive expected hashes are passed into the deleter's own frozen inventory,
    not merely checked once here. Retained paths and hardlink caveats survive.
    """
    if quiescent is not True:
        raise ResearchDeletionRejected("QUIESCENCE_NOT_PROVEN")
    if not isinstance(recovery_states, Mapping) or not recovery_states or any(
            not isinstance(key, str) or not key.strip() or value not in
            {"EMPTY", "REPLAYED", "RECONCILED", "NOT_PRESENT"}
            for key, value in recovery_states.items()):
        raise ResearchDeletionRejected("RECOVERY_NOT_RECONCILED")
    root = Path(os.path.abspath(os.fspath(runtime_root)))
    plan = plan_research_reset(runtime_root, proof=proof, max_entries=max_entries,
                               max_depth=max_depth, max_metadata_bytes=max_metadata_bytes)
    if plan.get("complete") is not True or plan.get("errors"):
        raise ResearchDeletionRejected("RESET_INVENTORY_INCOMPLETE")
    if plan.get("boundary_proof_structurally_valid") is not True:
        raise ResearchDeletionRejected("RESET_BOUNDARY_PROOF_INVALID")
    if expected_plan_sha256 is not None and plan["plan_sha256"] != expected_plan_sha256:
        raise ResearchDeletionRejected("RESET_PLAN_CHANGED")
    targets = plan["targets"]
    if len(targets) > max_files or plan["target_bytes"] > max_total_bytes:
        raise ResearchDeletionRejected("RESET_TARGET_BUDGET_EXCEEDED")
    paths, expected = [], {}
    for row in targets:
        path = _checked_path(row["absolute_path"], root)
        info = path.lstat()
        observed = (info.st_size, info.st_mtime_ns, info.st_dev, info.st_ino, info.st_nlink)
        recorded = tuple(row[key] for key in ("size_bytes", "mtime_ns", "device", "inode", "link_count"))
        if observed != recorded:
            raise ResearchDeletionRejected("RESET_TARGET_CHANGED_AFTER_PLAN")
        actual = _fingerprint(path)
        after = path.lstat()
        if (after.st_size, after.st_mtime_ns, after.st_dev, after.st_ino, after.st_nlink) != recorded:
            raise ResearchDeletionRejected("RESET_TARGET_CHANGED_DURING_VALIDATION")
        digest = row.get("expected_sha256")
        if row["category"] == "ARCHIVE_RESEARCH_PAYLOAD" and not digest:
            raise ResearchDeletionRejected("ARCHIVE_EXPECTED_HASH_MISSING")
        if digest is not None and actual["sha256"] != digest:
            raise ResearchDeletionRejected("ARCHIVE_SHA256_MISMATCH")
        # Bind *all* validated files into the deleter's snapshot; archive rows
        # additionally must agree with their original immutable metadata.
        expected[str(path)] = digest or actual["sha256"]
        paths.append(path)
    receipt = delete_exact_research_files(
        root=root, targets=paths, allowed_paths=paths, receipt_path=receipt_path,
        quiescent=quiescent, recovery_states=recovery_states,
        protected_paths=protected_paths, max_files=max_files, max_total_bytes=max_total_bytes,
        expected_sha256_by_path=expected,
        receipt_context={
            "plan_sha256": plan["plan_sha256"], "proof_sha256": plan["proof_sha256"],
            "retained": [{key: row[key] for key in
                          ("path", "absolute_path", "reason", "category", "size_bytes", "hardlinked") if key in row}
                         for row in plan["retained"]],
            "bytes_basis": plan["bytes_basis"], "hardlinked_target_count": plan["hardlinked_target_count"],
        },
    )
    return {"schema": "research_reset_execution_v1", "status": receipt["status"],
            "plan_sha256": plan["plan_sha256"], "proof_sha256": plan["proof_sha256"],
            "deletion_receipt": receipt, "retained": plan["retained"],
            "hardlinked_target_count": plan["hardlinked_target_count"],
            "bytes_basis": plan["bytes_basis"], "physical_bytes_reclaimed": None,
            "raw_payload_copies_created": False, "retained_count": len(plan["retained"])}
