import importlib
import json

import collector_storage
from research_v3_store import V3EvidenceStore


def _setup(monkeypatch, root):
    monkeypatch.setenv("BOT_DATA_DIR", str(root))
    monkeypatch.setenv("BTC_AGENT_DATA_DIR", str(root))
    monkeypatch.setattr(collector_storage, "disk_usage_fraction", lambda _path=None: .5)
    return V3EvidenceStore(root, epoch_id="epoch-reader")


def test_analyzer_reads_committed_sealed_then_active_with_exact_dedup(tmp_path, monkeypatch):
    store = _setup(monkeypatch, tmp_path)
    store.initialize_ledger_generation_authority("decision")
    store.append("decision", {"record_id":"decision:sealed","episode_id":"one","timestamp":"2033-01-01T00:00:00Z"})
    store.rotate_ledger("decision")
    store.append("decision", {"record_id":"decision:active","episode_id":"two","timestamp":"2033-01-02T00:00:00Z"})
    analyzer = importlib.import_module("analyzer_research_engine_v62")
    rows = analyzer._v3_ledger_rows("decision")
    assert [row["record_id"] for row in rows] == ["decision:sealed", "decision:active"]


def test_analyzer_excludes_orphan_and_fails_closed_during_interrupted_rotation(tmp_path, monkeypatch):
    store = _setup(monkeypatch, tmp_path)
    store.initialize_ledger_generation_authority("decision")
    store.append("decision", {"record_id":"decision:before-crash","episode_id":"one"})
    (store.ledger_dir / "decision.jsonl.99").write_text('{"record_id":"orphan"}\n', "utf-8")
    analyzer = importlib.import_module("analyzer_research_engine_v62")
    assert [row["record_id"] for row in analyzer._v3_ledger_rows("decision")] == ["decision:before-crash"]
    try:
        store.rotate_ledger("decision", failpoint="AFTER_RENAME")
    except RuntimeError:
        pass
    assert analyzer._v3_ledger_rows("decision") == []


def test_analyzer_preserves_generation_zero_legacy_active_behavior(tmp_path, monkeypatch):
    store = _setup(monkeypatch, tmp_path)
    store.append("decision", {"record_id":"decision:legacy-active","episode_id":"one"})
    analyzer = importlib.import_module("analyzer_research_engine_v62")
    assert [row["record_id"] for row in analyzer._v3_ledger_rows("decision")] == ["decision:legacy-active"]


def test_analyzer_exact_dedup_and_conflicting_duplicate_fail_closed(tmp_path, monkeypatch):
    store = _setup(monkeypatch, tmp_path)
    store.initialize_ledger_generation_authority("decision")
    row = {"record_id": "decision:duplicate", "episode_id": "one"}
    store.append("decision", row)
    rotation = store.rotate_ledger("decision")
    sealed_row = json.loads(
        store.resolve_ledger_generation("decision", rotation["sealed_ref"]).read_text("utf-8")
    )
    active = store.ledger_path("decision")
    active.write_text(json.dumps(sealed_row, sort_keys=True, separators=(",", ":")) + "\n", "utf-8")
    analyzer = importlib.import_module("analyzer_research_engine_v62")
    assert analyzer._v3_ledger_rows("decision") == [sealed_row]
    active.write_text('{"episode_id":"changed","record_id":"decision:duplicate"}\n', "utf-8")
    assert analyzer._v3_ledger_rows("decision") == []


def test_analyzer_missing_identity_fails_closed_and_read_only_open_creates_nothing(tmp_path, monkeypatch):
    root = tmp_path / "mirror"; ledger = root / "v3" / "ledgers" / "decision.jsonl"
    ledger.parent.mkdir(parents=True)
    ledger.write_text('{"episode_id":"missing-record-id"}\n', "utf-8")
    monkeypatch.setenv("BTC_AGENT_DATA_DIR", str(root))
    before = {path.relative_to(root).as_posix() for path in root.rglob("*")}
    analyzer = importlib.import_module("analyzer_research_engine_v62")
    assert analyzer._v3_ledger_rows("decision") == []
    after = {path.relative_to(root).as_posix() for path in root.rglob("*")}
    assert after == before
