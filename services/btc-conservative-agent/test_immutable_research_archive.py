import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

import pytest

from research import immutable_archive


REPO = Path(__file__).resolve().parents[2]
LOOP = (REPO / "scripts" / "sync-fly-bot-data-loop.ps1").read_text(encoding="utf-8")


def _fixture(root: Path):
    (root / "reports").mkdir()
    (root / "analysis_dashboard.html").write_text("dashboard", encoding="utf-8")
    (root / "executive_summary.txt").write_text("summary", encoding="utf-8")
    report = root / "reports" / "qualified_report.json"
    report.write_text('{"qualified":true}', encoding="utf-8")
    (root / "reports" / "stale_report.json").write_text("stale", encoding="utf-8")
    manifest = {
        "schema": "report_manifest_v1",
        "analyzer_sync_id": "test-run",
        "report_count": 1,
        "text_artifacts": ["analysis_dashboard.html", "executive_summary.txt"],
        "reports": [{"file": report.name, "size_bytes": report.stat().st_size}],
        "analysis_provenance": {
            "generation_revision": "2" * 40,
            "source_data_revision": "3" * 64,
            "cohort_schema": "analysis_cohorts_v1",
        },
        "fresh_epoch": {
            "schema": "fresh_research_epoch_v1",
            "status": "BOUND",
            "epoch_id": "epoch-fixture",
            "cutoff_utc": "2026-08-16T00:00:00+00:00",
            "kind": "NO_BACKFILL_RESEARCH_ACCUMULATOR",
        },
    }
    (root / "report_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (root / "relay_lifecycle_evidence_v1.json").write_text('{"schema":"relay_lifecycle_evidence_v1"}', encoding="utf-8")
    (root / "counterfactual.jsonl").write_text('{"schema":"counterfactual_v2","trade_id":"cont-test"}\n', encoding="utf-8")


def _publisher_accepts(archive: Path, tmp_path: Path) -> bool:
    match = re.search(r"function Test-CompleteAnalyzerArchive \{.*?^\}", LOOP, re.MULTILINE | re.DOTALL)
    assert match
    probe = tmp_path / "probe.ps1"
    escaped = str(archive).replace("'", "''")
    probe.write_text(match.group(0) + f"\nif (Test-CompleteAnalyzerArchive -ArchivePath '{escaped}') {{ exit 0 }} else {{ exit 7 }}\n", encoding="utf-8")
    result = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(probe)], capture_output=True, text=True)
    return result.returncode == 0


def test_archive_v2_is_exact_hash_bound_and_preserves_evidence(tmp_path):
    root = tmp_path / "run"
    archive_root = tmp_path / "archives"
    root.mkdir()
    _fixture(root)
    archive = immutable_archive.create_archive(root, {"generated_at": "2026-08-16T00:00:00Z", "data_scope": "session"}, archive_root)
    manifest = json.loads((archive / "archive_manifest.json").read_text(encoding="utf-8"))
    assert manifest["schema"] == "research_session_archive_v2"
    assert manifest["complete"] is True
    assert manifest["analyzer_revision"] == "2" * 40
    assert manifest["source_data_revision"] == "3" * 64
    assert manifest["fresh_epoch"]["epoch_id"] == "epoch-fixture"
    assert manifest["fresh_epoch"]["cutoff_utc"] == "2026-08-16T00:00:00+00:00"
    paths = {row["path"] for row in manifest["files"]}
    assert "reports/qualified_report.json" in paths
    assert "reports/stale_report.json" not in paths
    assert "evidence/relay_lifecycle_evidence_v1.json" in paths
    assert "evidence/counterfactual.jsonl" in paths
    for row in manifest["files"]:
        member = archive / row["path"]
        assert member.stat().st_size == row["size_bytes"]
        assert hashlib.sha256(member.read_bytes()).hexdigest() == row["sha256"]
    assert _publisher_accepts(archive, tmp_path)
    index = json.loads((root / "research_session_index.json").read_text(encoding="utf-8"))
    assert index["sessions"][0]["session_id"] == archive.name


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("generation_revision", "UNKNOWN", "full analyzer Git revision"),
        ("source_data_revision", "", "complete source-data revision"),
    ],
)
def test_archive_rejects_unqualified_provenance(tmp_path, field, value, message):
    root = tmp_path / "run"
    root.mkdir()
    _fixture(root)
    manifest_path = root / "report_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["analysis_provenance"][field] = value
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        immutable_archive.create_archive(root, {}, tmp_path / "archives")


def test_archive_reads_evidence_from_canonical_data_root(tmp_path):
    root = tmp_path / "run"
    evidence_root = tmp_path / "mirror"
    archive_root = tmp_path / "archives"
    root.mkdir()
    evidence_root.mkdir()
    _fixture(root)
    (root / "relay_lifecycle_evidence_v1.json").unlink()
    (root / "counterfactual.jsonl").unlink()
    (evidence_root / "relay_lifecycle_evidence_v1.json").write_text(
        '{"schema":"relay_lifecycle_evidence_v1"}', encoding="utf-8"
    )
    (evidence_root / "counterfactual.jsonl").write_text(
        '{"trade_id":"cont-evidence-root"}\n', encoding="utf-8"
    )

    archive = immutable_archive.create_archive(
        root,
        {},
        archive_root,
        evidence_root=evidence_root,
    )
    manifest = json.loads((archive / "archive_manifest.json").read_text(encoding="utf-8"))
    evidence = {row["name"]: row for row in manifest["evidence"]}
    assert evidence["relay_lifecycle_evidence_v1.json"]["available"] is True
    assert evidence["counterfactual.jsonl"]["available"] is True
    assert (archive / "evidence" / "relay_lifecycle_evidence_v1.json").is_file()
    assert (archive / "evidence" / "counterfactual.jsonl").is_file()


def test_interrupted_archive_never_exposes_partial_generation(tmp_path, monkeypatch):
    root = tmp_path / "run"
    archive_root = tmp_path / "archives"
    root.mkdir()
    _fixture(root)
    original = immutable_archive._copy
    calls = {"count": 0}

    def interrupted(source, destination):
        calls["count"] += 1
        if calls["count"] == 2:
            raise OSError("injected interruption")
        original(source, destination)

    monkeypatch.setattr(immutable_archive, "_copy", interrupted)
    with pytest.raises(OSError, match="injected"):
        immutable_archive.create_archive(root, {}, archive_root)
    assert not list(archive_root.glob("session_*"))
    assert not list(archive_root.glob(".staging-*"))
    assert not (root / "research_session_index.json").exists()


def test_publisher_rejects_tampered_partial_and_extra_members(tmp_path):
    root = tmp_path / "run"
    archive_root = tmp_path / "archives"
    root.mkdir()
    _fixture(root)
    archive = immutable_archive.create_archive(root, {}, archive_root)
    (archive / "analysis_dashboard.html").write_text("tampered", encoding="utf-8")
    assert not _publisher_accepts(archive, tmp_path)

    partial = tmp_path / "partial"
    partial.mkdir()
    (partial / "archive_manifest.json").write_text(json.dumps({"schema": "research_session_archive_v2", "complete": True, "files": []}), encoding="utf-8")
    assert not _publisher_accepts(partial, tmp_path)

    archive2 = immutable_archive.create_archive(root, {}, archive_root)
    (archive2 / "undeclared.json").write_text("{}", encoding="utf-8")
    assert not _publisher_accepts(archive2, tmp_path)
