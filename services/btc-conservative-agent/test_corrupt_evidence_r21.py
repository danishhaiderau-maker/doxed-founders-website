"""R21 expiry for corrupt_evidence_quarantine — mutation proofs."""
from __future__ import annotations

import json
import time
from pathlib import Path

import corrupt_evidence_r21 as ce


def _aged_dir(root: Path, name: str, age_sec: float, payload: bytes = b"x"):
    d = root / "corrupt_evidence_quarantine" / name
    d.mkdir(parents=True)
    f = d / "blob.bin"
    f.write_bytes(payload)
    past = time.time() - age_sec
    import os
    os.utime(d, (past, past))
    os.utime(f, (past, past))
    return d


def test_plan_lists_only_aged_dirs(tmp_path):
    _aged_dir(tmp_path, "old", 20 * 86400)
    _aged_dir(tmp_path, "fresh", 3600)
    plan = ce.plan_expired(tmp_path, max_age_sec=14 * 86400)
    assert plan["execute"] is False and plan["mode"] == "DRY_RUN"
    assert plan["count"] == 1 and plan["entries"][0]["dir"] == "old"
    assert plan["plan_sha256"]


def test_confirm_refuses_sha_mismatch(tmp_path):
    _aged_dir(tmp_path, "old", 20 * 86400)
    plan = ce.plan_expired(tmp_path, max_age_sec=14 * 86400)
    out = ce.confirm_delete(tmp_path, plan_sha256="0" * 64, max_age_sec=14 * 86400)
    assert out["ok"] is False and out["error"] == "PLAN_SHA_MISMATCH"
    assert (tmp_path / "corrupt_evidence_quarantine" / "old").is_dir()


def test_confirm_deletes_exactly_listed(tmp_path):
    _aged_dir(tmp_path, "old", 20 * 86400, b"abc")
    _aged_dir(tmp_path, "fresh", 100, b"fresh")
    plan = ce.plan_expired(tmp_path, max_age_sec=14 * 86400)
    out = ce.confirm_delete(tmp_path, plan_sha256=plan["plan_sha256"], max_age_sec=14 * 86400)
    assert out["ok"] is True and out["deleted"] == ["old"]
    assert not (tmp_path / "corrupt_evidence_quarantine" / "old").exists()
    assert (tmp_path / "corrupt_evidence_quarantine" / "fresh").is_dir()


def test_mutation_mismatch_or_extra_file_refuses(tmp_path):
    _aged_dir(tmp_path, "old", 20 * 86400, b"abc")
    plan = ce.plan_expired(tmp_path, max_age_sec=14 * 86400)
    # mutate file after plan → confirm re-plans and refuses (PLAN_SHA_MISMATCH)
    (tmp_path / "corrupt_evidence_quarantine" / "old" / "blob.bin").write_bytes(b"mutated")
    out = ce.confirm_delete(tmp_path, plan_sha256=plan["plan_sha256"], max_age_sec=14 * 86400)
    assert out["ok"] is False and out["error"] == "PLAN_SHA_MISMATCH"
    assert (tmp_path / "corrupt_evidence_quarantine" / "old").is_dir()
    # Mutation proof: a pass that ignored plan sha would delete; we require refuse.
    assert out.get("deleted") == []
