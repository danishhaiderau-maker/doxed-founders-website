import hashlib
import importlib.util
import json
import os
from pathlib import Path
import pytest

from dynamic_policy_analyzer import (
    INPUT_FILE,
    _load_verified_canonical_input,
    build_dynamic_policy_analysis_report,
)


AGENT = Path(__file__).resolve().parent


@pytest.mark.parametrize("fields", ["full", "legacy", "both", "legacy_blank_full"])
def test_dynamic_input_accepts_verified_checkpoint_digest_fields(tmp_path, fields):
    root = tmp_path / "canonical-research-data"
    target = root / INPUT_FILE
    target.parent.mkdir(parents=True)
    raw = b'{"schema":"dynamic_policy_analysis_input_v1","candidates":[]}'
    target.write_bytes(raw)
    digest = hashlib.sha256(raw).hexdigest()
    record = {"size": len(raw), "inode": 1, "mtime_ns": 1}
    if fields in {"full", "both"}:
        record["full_sha256"] = digest
    if fields in {"legacy", "both", "legacy_blank_full"}:
        record["sha256"] = digest.upper()
    if fields == "legacy_blank_full":
        record["full_sha256"] = ""
    (root / ".fly-sync-state.json").write_text(json.dumps({INPUT_FILE: record}), encoding="utf-8")
    payload, receipt = _load_verified_canonical_input(root)
    assert payload == json.loads(raw)
    assert receipt["verification"] == "CHECKSUM_VERIFIED_CANONICAL_MIRROR"
    assert receipt["sha256"] == digest


@pytest.mark.parametrize("defect", ["conflict", "short", "nonhex", "bool", "corrupt_bytes"])
def test_dynamic_input_rejects_bad_full_digest_without_legacy_fallback(tmp_path, defect):
    root = tmp_path / "canonical-research-data"
    target = root / INPUT_FILE
    target.parent.mkdir(parents=True)
    raw = b'{"schema":"dynamic_policy_analysis_input_v1"}'
    digest = hashlib.sha256(raw).hexdigest()
    record = {"full_sha256": digest, "sha256": digest}
    if defect == "conflict":
        record["full_sha256"] = "0" * 64
    elif defect == "short":
        record["full_sha256"] = digest[:12]
    elif defect == "nonhex":
        record["full_sha256"] = "z" * 64
    elif defect == "bool":
        record["full_sha256"] = True
    elif defect == "corrupt_bytes":
        raw += b" "
    target.write_bytes(raw)
    (root / ".fly-sync-state.json").write_text(json.dumps({INPUT_FILE: record}), encoding="utf-8")
    payload, receipt = _load_verified_canonical_input(root)
    assert payload is None
    assert receipt["verification"] == "UNKNOWN"
    expected = ("DYNAMIC_INPUT_MANIFEST_CHECKSUM_CONFLICT" if defect == "conflict"
                else "DYNAMIC_INPUT_CHECKSUM_MISMATCH" if defect == "corrupt_bytes"
                else "DYNAMIC_INPUT_MANIFEST_CHECKSUM_INVALID")
    assert receipt["blocker"] == expected


def _load(name, path):
    inherited = os.environ.pop("BTC_AGENT_DATA_DIR", None)
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    try:
        spec.loader.exec_module(module)
    finally:
        if inherited is not None:
            os.environ["BTC_AGENT_DATA_DIR"] = inherited
    return module


def test_missing_canonical_dynamic_input_publishes_unknown(tmp_path):
    root = tmp_path / "canonical-research-data"
    root.mkdir()
    (root / ".fly-sync-state.json").write_text("{}", encoding="utf-8")
    result = build_dynamic_policy_analysis_report(
        root, generation_revision="rev-1", dataset_epoch="epoch-1",
        source_revision="source-1",
    )
    assert result["status"] == "UNKNOWN"
    assert result["relay_eligible"] is False
    assert result["live_policy_change_allowed"] is False
    assert result["blockers"] == ["DYNAMIC_INPUT_NOT_IN_VERIFIED_MIRROR_MANIFEST"]


def test_checksum_mismatch_never_consumes_dynamic_payload(tmp_path):
    root = tmp_path / "canonical-research-data"
    target = root / INPUT_FILE
    target.parent.mkdir(parents=True)
    target.write_text('{"schema":"dynamic_policy_analysis_input_v1"}', encoding="utf-8")
    (root / ".fly-sync-state.json").write_text(json.dumps({
        INPUT_FILE: {"sha256": "0" * 64, "inode": 1, "size": target.stat().st_size, "mtime_ns": 1},
    }), encoding="utf-8")
    result = build_dynamic_policy_analysis_report(
        root, generation_revision="rev-1", dataset_epoch="epoch-1",
        source_revision="source-1",
    )
    assert result["status"] == "UNKNOWN"
    assert result["blockers"] == ["DYNAMIC_INPUT_CHECKSUM_MISMATCH"]


def test_dynamic_report_is_atomically_manifest_published_and_dashboard_visible(tmp_path, monkeypatch):
    analyzer = _load("dynamic_atomic_analyzer", AGENT / "analyzer_research_engine_v62.py")
    dashboard = _load("dynamic_atomic_dashboard", AGENT / "research" / "research_dashboard.py")
    import research.mirror_coherence as mirror_coherence
    import research.canonical_data_store as canonical_data_store

    monkeypatch.setattr(mirror_coherence, "assert_mirror_coherent", lambda **_kwargs: None)
    monkeypatch.setattr(canonical_data_store, "record_analyzer_completion", lambda *args, **kwargs: {})
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BTC_AGENT_DATA_DIR", str(tmp_path))
    report = {
        "schema": "dynamic_policy_analyzer_orchestration_v1",
        "status": "UNKNOWN", "purpose": "RESEARCH_ONLY_NOT_RELAY_ELIGIBLE",
        "relay_eligible": False, "live_policy_change_allowed": False,
        "generation_revision": "rev-1", "dataset_epoch": "epoch-1",
        "input_receipt": {"verification": "UNKNOWN"},
        "blockers": ["SEALED_HOLDOUT_EVIDENCE_MISSING"],
    }
    Path(analyzer.DYNAMIC_POLICY_ANALYSIS_REPORT_FILE).write_text(
        json.dumps(report), encoding="utf-8"
    )
    manifest = {
        "generation_id": "dynamic-generation",
        "reports": [{"file": analyzer.DYNAMIC_POLICY_ANALYSIS_REPORT_FILE}],
        "text_artifacts": [],
    }
    analyzer._publish_completed_report_generation(manifest)

    published = tmp_path / analyzer.PUBLISHED_REPORTS_DIR
    assert json.loads((published / analyzer.REPORT_MANIFEST_FILE).read_text())["generation_id"] == "dynamic-generation"
    assert json.loads((published / analyzer.DYNAMIC_POLICY_ANALYSIS_REPORT_FILE).read_text())["status"] == "UNKNOWN"
    assert not list(tmp_path.glob(".published_reports.staging-*"))

    monkeypatch.setattr(dashboard, "ROOT", tmp_path)
    monkeypatch.setattr(dashboard, "DATA_ROOT", tmp_path)
    dashboard._API_RESPONSE_CACHE.clear()
    payload = dashboard.app.test_client().get("/api/dynamic-policy-research").get_json()
    assert payload["status"] == "UNKNOWN"
    assert payload["qualification"] == "UNKNOWN"
    assert payload["relay_eligible"] is False
    assert payload["live_policy_change_allowed"] is False
    assert payload["blockers"] == ["SEALED_HOLDOUT_EVIDENCE_MISSING"]
    assert payload["evidence_source"] == analyzer.DYNAMIC_POLICY_ANALYSIS_REPORT_FILE


def test_analyzer_manifest_wires_dynamic_builder_inside_existing_generation():
    source = (AGENT / "analyzer_research_engine_v62.py").read_text(encoding="utf-8")
    assert "build_dynamic_policy_analysis_report(" in source
    assert "os.replace(temporary, target)" in source
    assert "DYNAMIC_POLICY_ANALYSIS_REPORT_FILE" in source
    assert '"available_in_generation": any(' in source
