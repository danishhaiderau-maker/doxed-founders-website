import gzip
import hashlib
import importlib.util
import json
import os
from pathlib import Path


AGENT = Path(__file__).resolve().parent


def _load(name):
    inherited = os.environ.pop("BTC_AGENT_DATA_DIR", None)
    spec = importlib.util.spec_from_file_location(name, AGENT / "analyzer_research_engine_v62.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    try:
        spec.loader.exec_module(module)
    finally:
        if inherited is not None:
            os.environ["BTC_AGENT_DATA_DIR"] = inherited
    return module


def _gzip_rows(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as raw:
        with gzip.GzipFile(filename=path.name[:-3], mode="wb", fileobj=raw, mtime=0) as zipped:
            for row in rows:
                zipped.write((json.dumps(row, sort_keys=True) + "\n").encode())


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_coverage_report_consumes_same_generation_and_authoritative_ledgers(tmp_path, monkeypatch):
    analyzer = _load("coverage_publication_analyzer")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("BTC_VERIFIED_LEGACY_ARCHIVE_ROOT", raising=False)
    root = tmp_path / "canonical-research-data"
    derived = root / "derived" / "policy-evidence" / "generation-key"
    binding = derived / "binding-index.jsonl.gz"
    results = derived / "conservative-results.jsonl.gz"
    _gzip_rows(binding, [{
        "episode_id": "episode-1", "exact_binding_complete": False,
        "unknown_reason_codes": ["UNKNOWN_MARKET_PATH_MISSING"],
    }])
    _gzip_rows(results, [{
        "episode_id": "episode-1", "classification": "UNKNOWN", "supported": False,
        "unknown_reason_codes": ["UNKNOWN_TERMINAL_OUTCOME_UNSUPPORTED"],
    }])
    for singular, count in {
        "opportunity": 2, "decision": 3, "order_intent": 4,
        "execution": 5, "lifecycle": 6, "market_segment": 7,
    }.items():
        path = root / "v3" / "ledgers" / f"{singular}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps({"n": n}) + "\n" for n in range(count)), encoding="utf-8")
    generation = {"generation_key": "generation-key", "epoch_id": "epoch-1"}
    report, mirror = analyzer._write_evidence_coverage_triage_report(
        root,
        {"generation": generation, "exhaustive_relative_path": binding.relative_to(root).as_posix(),
         "exhaustive_sha256": _sha(binding)},
        {"generation": generation, "relative_path": results.relative_to(root).as_posix(),
         "artifact_sha256": _sha(results)},
    )
    assert report["generation"] == generation
    assert report["totals"]["opportunities"] == 2
    assert report["totals"]["unknown_episodes"] == 1
    assert report["outcome_inference_performed"] is False
    assert mirror == Path(analyzer.REPORTS_DIR) / analyzer.EVIDENCE_COVERAGE_TRIAGE_REPORT_FILE
    assert mirror.is_file()
    assert report["archive_recovery_retention"]["archive_session_count"] == 0


def test_coverage_report_fails_closed_on_generation_mismatch(tmp_path, monkeypatch):
    analyzer = _load("coverage_mismatch_analyzer")
    monkeypatch.chdir(tmp_path)
    root = tmp_path / "canonical-research-data"
    root.mkdir()
    try:
        analyzer._write_evidence_coverage_triage_report(
            root, {"generation": {"generation_key": "a"}},
            {"generation": {"generation_key": "b"}},
        )
    except ValueError as exc:
        assert str(exc) == "EVIDENCE_COVERAGE_GENERATION_MISMATCH"
    else:
        raise AssertionError("mismatched generations must fail closed")


def test_coverage_report_is_required_manifest_inventory():
    source = (AGENT / "analyzer_research_engine_v62.py").read_text(encoding="utf-8")
    assert "EVIDENCE_COVERAGE_TRIAGE_REPORT_FILE: {" in source
    assert '"available_in_generation": any(' in source
    assert '"generation_error": evidence_coverage_triage_error' in source
