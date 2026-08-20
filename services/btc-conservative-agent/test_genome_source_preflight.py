import json
import hashlib
import sqlite3
import sys
from pathlib import Path


AGENT_ROOT = Path(__file__).resolve().parent
if str(AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(AGENT_ROOT))

from research.genome.run_analyzer import run_genome_analyzer


def memory_only_db(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript("""
            CREATE TABLE genome_library (genome_id TEXT PRIMARY KEY);
            CREATE TABLE genome_discovery_memory (discovery_id TEXT PRIMARY KEY);
            CREATE TABLE genome_evidence_ledger (id INTEGER PRIMARY KEY);
        """)


def complete_empty_source_db(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript("""
            CREATE TABLE environment_genome (environment_id TEXT PRIMARY KEY, ts TEXT, payload_json TEXT);
            CREATE TABLE market_genome (market_genome_id TEXT PRIMARY KEY, environment_id TEXT, ts TEXT, payload_json TEXT);
            CREATE TABLE decision_genome (decision_id TEXT PRIMARY KEY, market_genome_id TEXT, ts TEXT, payload_json TEXT);
            CREATE TABLE execution_genome (execution_id TEXT PRIMARY KEY, decision_id TEXT, ts TEXT, payload_json TEXT);
            CREATE TABLE lifecycle_genome (lifecycle_id TEXT PRIMARY KEY, execution_id TEXT, ts TEXT, payload_json TEXT);
            CREATE TABLE trade_genome (trade_id TEXT PRIMARY KEY, decision_id TEXT, ts TEXT, payload_json TEXT);
        """)


def test_memory_only_db_returns_structured_unavailable_without_analysis_artifacts(tmp_path):
    source = tmp_path / "research.db"
    memory_only_db(source)
    out = tmp_path / "reports"
    payload = run_genome_analyzer(db_path=str(source), out_dir=str(out), publish_root_artifacts=False)
    assert payload["status"] == "GENOME_SOURCE_UNAVAILABLE"
    assert payload["reason"] == "REQUIRED_SOURCE_TABLES_MISSING"
    assert "environment_genome" in payload["missing_tables"]
    assert payload["execution_affected"] is False
    assert (out / "genome_source_status.json").exists()
    assert not (out / "genome_analysis_report.json").exists()
    assert not (out / "genome_memory.db").exists()


def test_missing_source_preserves_prior_valid_genome_artifact(tmp_path):
    out = tmp_path / "reports"
    out.mkdir()
    prior = {"schema": "trading_genome_analysis_v1", "generated_at": "prior-valid", "genome_library": [1]}
    artifact = out / "genome_analysis_report.json"
    artifact.write_text(json.dumps(prior), encoding="utf-8")
    before = artifact.read_bytes()
    payload = run_genome_analyzer(
        db_path=str(tmp_path / "missing.db"), out_dir=str(out), publish_root_artifacts=False,
    )
    assert payload["status"] == "GENOME_SOURCE_UNAVAILABLE"
    assert payload["reason"] == "SOURCE_DB_MISSING"
    assert artifact.read_bytes() == before
    status = json.loads((out / "genome_source_status.json").read_text(encoding="utf-8"))
    assert status["status"] == "GENOME_SOURCE_UNAVAILABLE"


def test_unavailable_genome_does_not_modify_other_research_page_artifacts(tmp_path):
    out = tmp_path / "reports"
    out.mkdir()
    page_files = {}
    for name in ("policy_candidate_oos_report.json", "best_policy_research_report.json",
                 "paused_shadow_research_report.json"):
        path = out / name
        path.write_text(json.dumps({"schema": name, "sentinel": "unchanged"}), encoding="utf-8")
        page_files[path] = path.read_bytes()
    payload = run_genome_analyzer(
        db_path=str(tmp_path / "missing.db"), out_dir=str(out), publish_root_artifacts=False,
    )
    assert payload["other_research_pages_affected"] is False
    assert all(path.read_bytes() == before for path, before in page_files.items())


def test_available_source_remains_immutable_and_derived_memory_is_separate(tmp_path):
    source = tmp_path / "source.db"
    complete_empty_source_db(source)
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    out = tmp_path / "reports"
    payload = run_genome_analyzer(
        db_path=str(source), out_dir=str(out), publish_root_artifacts=False,
    )
    assert payload["source_status"]["status"] == "AVAILABLE"
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
    assert (out / "genome_memory.db").exists()
    assert (out / "genome_analysis_report.json").exists()
