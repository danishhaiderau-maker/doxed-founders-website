import importlib.util
import json
import os
import time
from pathlib import Path


AGENT = Path(__file__).resolve().parent


def _load_analyzer():
    spec = importlib.util.spec_from_file_location(
        "compact_summary_provenance_analyzer",
        AGENT / "analyzer_research_engine_v62.py",
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _identity(**overrides):
    row = {
        "generation_revision": "rev-current",
        "analyzer_revision": "rev-current",
        "session_scope": "FRESH-COLLECTION",
        "fresh_epoch_id": "epoch-current",
        "source_data_revision": "snapshot-current",
        "source_revision": "source-current",
        "deployed_revision": "deployed-current",
        "dataset_epoch": "dataset-current",
        "config_signature": "config-current",
    }
    row.update(overrides)
    return row


def _report(identity=None, **values):
    identity = identity or _identity()
    return {
        "generation_revision": identity["generation_revision"],
        "analyzer_revision": identity["analyzer_revision"],
        "session_scope": identity["session_scope"],
        "analysis_provenance": {
            "fresh_epoch_id": identity["fresh_epoch_id"],
            "source_data_revision": identity["source_data_revision"],
            "source_revision": identity["source_revision"],
            "deployed_revision": identity["deployed_revision"],
            "dataset_epoch": identity["dataset_epoch"],
            "config_signature": identity["config_signature"],
        },
        **values,
    }


def test_matching_auxiliary_report_is_included(tmp_path):
    analyzer = _load_analyzer()
    path = tmp_path / "aux.json"
    path.write_text(json.dumps(_report(value=7)), encoding="utf-8")

    payload, receipt = analyzer._load_current_auxiliary_report(path, _identity())

    assert payload["value"] == 7
    assert receipt["included"] is True
    assert receipt["exclusion_reasons"] == []


def test_stale_revision_epoch_or_snapshot_is_excluded_with_receipt(tmp_path):
    analyzer = _load_analyzer()
    path = tmp_path / "stale.json"
    stale = _identity(
        generation_revision="rev-old",
        fresh_epoch_id="epoch-old",
        source_data_revision="snapshot-old",
    )
    path.write_text(json.dumps(_report(stale, value=99)), encoding="utf-8")

    payload, receipt = analyzer._load_current_auxiliary_report(path, _identity())

    assert payload == {}
    assert receipt["included"] is False
    assert receipt["exclusion_reasons"] == [
        "GENERATION_REVISION_MISMATCH",
        "FRESH_EPOCH_ID_MISMATCH",
        "SOURCE_DATA_REVISION_MISMATCH",
    ]
    assert receipt["observed_identity"]["generation_revision"] == "rev-old"


def test_missing_session_identity_fails_closed(tmp_path):
    analyzer = _load_analyzer()
    path = tmp_path / "unscoped.json"
    report = _report(value=3)
    report.pop("session_scope")
    path.write_text(json.dumps(report), encoding="utf-8")

    payload, receipt = analyzer._load_current_auxiliary_report(path, _identity())

    assert payload == {}
    assert receipt["exclusion_reasons"] == ["SESSION_SCOPE_MISSING"]


def test_current_iteration_file_is_stamped_before_strict_ingestion(tmp_path):
    analyzer = _load_analyzer()
    path = tmp_path / "fresh.json"
    analyzer._CURRENT_ANALYZER_GENERATION_STARTED_AT = time.time() - 1
    path.write_text(json.dumps({"value": 11}), encoding="utf-8")

    assert analyzer._stamp_current_iteration_auxiliary_report(path, _identity()) is True
    payload, receipt = analyzer._load_current_auxiliary_report(path, _identity())

    assert payload["value"] == 11
    assert receipt["included"] is True


def test_pre_iteration_file_is_not_restamped(tmp_path):
    analyzer = _load_analyzer()
    path = tmp_path / "old.json"
    path.write_text(json.dumps(_report(_identity(generation_revision="old"))), encoding="utf-8")
    old = time.time() - 60
    os.utime(path, (old, old))
    analyzer._CURRENT_ANALYZER_GENERATION_STARTED_AT = time.time()

    assert analyzer._stamp_current_iteration_auxiliary_report(path, _identity()) is False
    payload, receipt = analyzer._load_current_auxiliary_report(path, _identity())

    assert payload == {}
    assert "GENERATION_REVISION_MISMATCH" in receipt["exclusion_reasons"]
