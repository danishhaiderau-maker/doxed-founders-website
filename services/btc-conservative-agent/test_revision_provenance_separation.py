import importlib.util
import json
from pathlib import Path


AGENT = Path(__file__).resolve().parent
REPO = AGENT.parents[1]


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_historical_heartbeat_does_not_infer_deployed_revision():
    migration = _load(
        REPO / "scripts" / "migrate_canonical_research_store.py",
        "revision_provenance_migration",
    )

    assert migration._deployed_revision({"sourceRevision": "a" * 40}) == "UNKNOWN"
    assert migration._deployed_revision({"deployedRevision": "b" * 40}) == "b" * 40


def test_analyzer_reads_independent_canonical_identities_and_keeps_missing_unknown(
    tmp_path, monkeypatch
):
    analyzer = _load(
        AGENT / "analyzer_research_engine_v62.py", "revision_provenance_analyzer"
    )
    monkeypatch.setenv("BTC_AGENT_DATA_DIR", str(tmp_path))
    manifest = {
        "source_revision": "a" * 40,
        "deployed_revision": "b" * 40,
        "dataset_epoch": "epoch-independent",
        "tile_config_signature": "c" * 64,
    }
    (tmp_path / "canonical_dataset_current.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )

    provenance = analyzer._report_source_evidence_provenance()

    assert provenance["source_revision"] == "a" * 40
    assert provenance["deployed_revision"] == "b" * 40
    assert provenance["dataset_epoch"] == "epoch-independent"
    assert provenance["config_signature"] == "c" * 64

    manifest.pop("deployed_revision")
    (tmp_path / "canonical_dataset_current.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    assert analyzer._report_source_evidence_provenance()["deployed_revision"] == "UNKNOWN"


def test_each_json_report_is_stamped_with_separate_revision_roles(tmp_path):
    analyzer = _load(
        AGENT / "analyzer_research_engine_v62.py", "revision_report_stamp_analyzer"
    )
    target = tmp_path / "report.json"
    target.write_text("{}", encoding="utf-8")
    provenance = {
        "cohort_schema": "analysis_cohorts_v1",
        "generation_revision": "d" * 40,
        "analyzer_revision": "d" * 40,
        "source_revision": "a" * 40,
        "deployed_revision": "b" * 40,
        "dataset_epoch": "epoch-one",
        "config_signature": "c" * 64,
        "source_data_revision": "e" * 64,
        "fresh_epoch_id": "fresh-one",
        "policy_comparability_key": None,
        "cohorts": {
            "SHOWCASE_STRATEGY": {
                "included_row_count": 0,
                "evidence_row_count": 0,
                "exclusion_reason_counts": {},
            }
        },
    }

    analyzer._stamp_report_analysis_provenance(target, provenance)
    report = json.loads(target.read_text(encoding="utf-8"))

    assert report["source_revision"] == "a" * 40
    assert report["deployed_revision"] == "b" * 40
    assert report["analyzer_revision"] == "d" * 40
    assert report["dataset_epoch"] == "epoch-one"
    assert report["config_signature"] == "c" * 64


def test_sync_heartbeat_records_authenticated_fly_revision_as_deployed_revision():
    source = (REPO / "scripts" / "sync-fly-bot-data.ps1").read_text(encoding="utf-8")

    assert "deployedRevision = $(if ($observedRevision)" in source
    assert 'heartbeat.get("deployedRevision")' in (
        REPO / "scripts" / "migrate_canonical_research_store.py"
    ).read_text(encoding="utf-8")
