"""R21 expiry for corrupt_evidence_quarantine — mutation proofs + sha re-check."""
from __future__ import annotations

import os
import time
from pathlib import Path

import corrupt_evidence_r21 as ce


def _aged_dir(root: Path, name: str, age_sec: float, payload: bytes = b"x"):
    d = root / "corrupt_evidence_quarantine" / name
    d.mkdir(parents=True)
    f = d / "blob.bin"
    f.write_bytes(payload)
    past = time.time() - age_sec
    os.utime(d, (past, past))
    os.utime(f, (past, past))
    return d


def test_plan_lists_only_aged_dirs(tmp_path):
    _aged_dir(tmp_path, "old", 3 * 86400)
    _aged_dir(tmp_path, "fresh", 3600)
    plan = ce.plan_expired(tmp_path, max_age_sec=ce.DEFAULT_MAX_AGE_SEC)
    assert plan["execute"] is False and plan["mode"] == "DRY_RUN"
    assert plan["count"] == 1 and plan["entries"][0]["dir"] == "old"
    assert plan["plan_sha256"]
    assert plan["max_age_sec"] == ce.DEFAULT_MAX_AGE_SEC
    assert ce.DEFAULT_MAX_AGE_SEC == 2 * 86400  # short until RK verifies


def test_confirm_refuses_sha_mismatch(tmp_path):
    _aged_dir(tmp_path, "old", 3 * 86400)
    plan = ce.plan_expired(tmp_path, max_age_sec=ce.DEFAULT_MAX_AGE_SEC)
    out = ce.confirm_delete(tmp_path, plan_sha256="0" * 64, max_age_sec=ce.DEFAULT_MAX_AGE_SEC)
    assert out["ok"] is False and out["error"] == "PLAN_SHA_MISMATCH"
    assert out["before_count"] == 1 and out["after_count"] == 1
    assert (tmp_path / "corrupt_evidence_quarantine" / "old").is_dir()


def test_confirm_deletes_exactly_listed(tmp_path):
    _aged_dir(tmp_path, "old", 3 * 86400, b"abc")
    _aged_dir(tmp_path, "fresh", 100, b"fresh")
    plan = ce.plan_expired(tmp_path, max_age_sec=ce.DEFAULT_MAX_AGE_SEC)
    out = ce.confirm_delete(tmp_path, plan_sha256=plan["plan_sha256"], max_age_sec=ce.DEFAULT_MAX_AGE_SEC)
    assert out["ok"] is True and out["deleted"] == ["old"]
    assert out["before_count"] == 2 and out["after_count"] == 1
    assert out["readback_gone"] == ["old"]
    assert not (tmp_path / "corrupt_evidence_quarantine" / "old").exists()
    assert (tmp_path / "corrupt_evidence_quarantine" / "fresh").is_dir()


def test_mutation_mismatch_or_extra_file_refuses(tmp_path):
    _aged_dir(tmp_path, "old", 3 * 86400, b"abc")
    plan = ce.plan_expired(tmp_path, max_age_sec=ce.DEFAULT_MAX_AGE_SEC)
    (tmp_path / "corrupt_evidence_quarantine" / "old" / "blob.bin").write_bytes(b"mutated")
    out = ce.confirm_delete(tmp_path, plan_sha256=plan["plan_sha256"], max_age_sec=ce.DEFAULT_MAX_AGE_SEC)
    assert out["ok"] is False and out["error"] == "PLAN_SHA_MISMATCH"
    assert (tmp_path / "corrupt_evidence_quarantine" / "old").is_dir()
    assert out.get("deleted") == []


def test_confirm_rechecks_per_file_sha_even_when_plan_sha_sticky(tmp_path, monkeypatch):
    """B3: per-file sha re-verify must fire when plan_sha is held constant but a file swaps."""
    _aged_dir(tmp_path, "old", 3 * 86400, b"abc")
    plan = ce.plan_expired(tmp_path, max_age_sec=ce.DEFAULT_MAX_AGE_SEC)

    def sticky_plan(*_a, **_k):
        return dict(plan)

    monkeypatch.setattr(ce, "plan_expired", sticky_plan)
    (tmp_path / "corrupt_evidence_quarantine" / "old" / "blob.bin").write_bytes(b"swapped")
    out = ce.confirm_delete(tmp_path, plan_sha256=plan["plan_sha256"], max_age_sec=ce.DEFAULT_MAX_AGE_SEC)
    assert out["ok"] is False and out["error"] == "FILE_SHA_MISMATCH"
    assert out.get("deleted") == []
    assert (tmp_path / "corrupt_evidence_quarantine" / "old").is_dir()


def test_symlink_child_is_refused(tmp_path):
    d = _aged_dir(tmp_path, "old", 3 * 86400, b"abc")
    os.symlink(d / "blob.bin", d / "link.bin")
    plan = ce.plan_expired(tmp_path, max_age_sec=ce.DEFAULT_MAX_AGE_SEC)
    # Symlinked trees are skipped from the plan (never auto-deleted).
    assert plan["count"] == 0
