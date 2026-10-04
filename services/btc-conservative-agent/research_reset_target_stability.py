"""Prove reset deletion targets are not written while the reset holds its barriers.

A target that changes between two samples taken under the held barriers has a
writer the barriers do not exclude; its fingerprint would then diverge between
the executor plan and the deleter (EXPECTED_SHA256_MISMATCH) after the reset
pointer is already durable. Sampling before that pointer exists turns the same
defect into a clean, retryable abort.
"""
from __future__ import annotations

import hashlib

from research_exact_deletion import ResearchDeletionRejected
from research_reset_inventory import plan_research_reset

REPORTED_PATH_HASHES = 5


def sample_targets(root, proof, scope_names) -> dict:
    rows = {}
    for name in scope_names:
        plan = plan_research_reset(str(root), proof=proof, allow_fly_runtime_aliases=True, scope_name=name)
        if plan.get("complete") is not True or plan.get("errors"):
            raise ResearchDeletionRejected("RESET_INVENTORY_INCOMPLETE")
        for row in plan["targets"]:
            rows[row["absolute_path"]] = (row["path"], int(row["size_bytes"]), int(row["mtime_ns"]),
                                          int(row["inode"]))
    return rows


def unstable_targets(first: dict, second: dict) -> list[dict]:
    changed = []
    for path in sorted(set(first) | set(second)):
        before, after = first.get(path), second.get(path)
        if before != after:
            changed.append({"absolute_path": path, "path": (before or after)[0],
                            "before": None if before is None else list(before[1:]),
                            "after": None if after is None else list(after[1:])})
    return changed


def assert_targets_stable(first: dict, second: dict) -> dict:
    changed = unstable_targets(first, second)
    if changed:
        error = ResearchDeletionRejected("RESET_TARGETS_CHANGED_UNDER_BARRIERS")
        error.unstable_targets = changed
        error.target_stability = {
            "unstable_target_count": len(changed),
            "unstable_target_path_sha256": [hashlib.sha256(row["absolute_path"].encode("utf-8")).hexdigest()
                                            for row in changed[:REPORTED_PATH_HASHES]],
        }
        raise error
    return {"status": "STABLE", "target_count": len(first)}
