import hashlib, json
from pathlib import Path
import pytest
from research.epoch_quarantine import quarantine_epoch
from research_genome.store import ResearchStore

def test_quarantine_moves_without_deleting_and_seals_exact_cutoff(tmp_path):
    (tmp_path / "reports").mkdir()
    (tmp_path / "raw.jsonl").write_text("one\n", encoding="utf-8")
    (tmp_path / "reports" / "report.json").write_text("{}", encoding="utf-8")
    result = quarantine_epoch(tmp_path, ["raw.jsonl", "reports/report.json"], cutoff="2026-08-16T10:30:00+00:00")
    archive = Path(result["path"])
    manifest = json.loads((archive / "quarantine_manifest.json").read_text(encoding="utf-8"))
    assert manifest["complete"] is True
    assert manifest["cutoff_utc"] == "2026-08-16T10:30:00+00:00"
    assert manifest["file_count"] == 2
    assert not (tmp_path / "raw.jsonl").exists()
    for row in manifest["files"]:
        preserved = archive / "files" / row["path"]
        assert preserved.exists()
        assert preserved.stat().st_size == row["size_bytes"]
        assert hashlib.sha256(preserved.read_bytes()).hexdigest() == row["sha256"]

def test_quarantine_rejects_paths_outside_root(tmp_path):
    outside = tmp_path.parent / "outside-evidence.txt"
    outside.write_text("keep", encoding="utf-8")
    result = quarantine_epoch(tmp_path, [str(outside)], cutoff="2026-08-16T10:30:00+00:00")
    assert result["moved"] == []
    assert outside.read_text(encoding="utf-8") == "keep"

def test_genome_reset_preserves_prior_database_with_manifest(tmp_path):
    store = ResearchStore(str(tmp_path))
    old_size = Path(store.db_path).stat().st_size
    store.reset()
    archives = list((tmp_path / "epoch_quarantine").glob("epoch_*"))
    assert len(archives) == 1
    manifest = json.loads((archives[0] / "quarantine_manifest.json").read_text())
    database = next(row for row in manifest["files"] if row["path"] == "research.db")
    assert database["size_bytes"] == old_size
    assert (archives[0] / "research.db").exists()
    assert Path(store.db_path).exists()
    store.close()
