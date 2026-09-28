import sqlite3
import sys
from pathlib import Path

import pytest


AGENT_ROOT = Path(__file__).resolve().parent
BOT_SOURCE = (AGENT_ROOT / "bot.py").read_text(encoding="utf-8")
if str(AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(AGENT_ROOT))

from research.genome.run_analyzer import _source_preflight
from research_genome.bridge import GenomeBridge
from research_genome.store import ResearchStore


def test_generation_segments_append_within_epoch_and_ingestion_is_append_only(tmp_path):
    store = ResearchStore(str(tmp_path))
    try:
        identity = store.record_generation_identity(
            dataset_epoch="epoch-1",
            deployed_revision="a" * 40,
            tile_config_signature="b" * 64,
            recorded_at="2026-08-29T00:00:00+00:00",
        )
        assert identity["dataset_epoch"] == "epoch-1"
        assert identity["legacy_unbound_total"] == 0
        assert store.record_generation_identity(
            dataset_epoch="epoch-1",
            deployed_revision="a" * 40,
            tile_config_signature="b" * 64,
            recorded_at="later-is-ignored-for-idempotency",
        )["recorded_at"] == "2026-08-29T00:00:00+00:00"
        next_segment = store.record_generation_identity(
            dataset_epoch="epoch-1", deployed_revision="c" * 40,
            tile_config_signature="b" * 64,
        )
        assert next_segment["generation_id"] != identity["generation_id"]
        first = store.record_ingestion_status(
            generation_id=next_segment["generation_id"], status="collecting", row_count=7,
            opportunity_count=2, observed_at="2026-08-29T00:01:00+00:00",
        )
        second = store.record_ingestion_status(
            generation_id=next_segment["generation_id"], status="complete", row_count=11,
            opportunity_count=3, observed_at="2026-08-29T00:02:00+00:00",
            detail={"checksum": "proof"},
        )
        assert second > first
        current = store.generation_identity()
        assert current["generation_id"] == next_segment["generation_id"]
        assert current["last_ingestion"] == {
            "status": "COMPLETE", "observed_at": "2026-08-29T00:02:00+00:00",
            "row_count": 11, "opportunity_count": 3, "detail": {"checksum": "proof"},
        }
    finally:
        store.close()


def test_preflight_exposes_generation_proof_without_changing_evidence_rows(tmp_path):
    store = ResearchStore(str(tmp_path))
    try:
        store.record_generation_identity(
            dataset_epoch="epoch-proof", deployed_revision="d" * 40,
            tile_config_signature="e" * 64,
        )
        identity = store.generation_identity()
        store.record_ingestion_status(
            generation_id=identity["generation_id"], status="complete", row_count=0,
            opportunity_count=0,
        )
    finally:
        store.close()
    before = sqlite3.connect(tmp_path / "research.db").execute(
        "SELECT COUNT(*) FROM research_events"
    ).fetchone()[0]
    status = _source_preflight(str(tmp_path / "research.db"))
    after = sqlite3.connect(tmp_path / "research.db").execute(
        "SELECT COUNT(*) FROM research_events"
    ).fetchone()[0]
    assert status["status"] == "AVAILABLE"
    assert status["generation_identity_status"] == "AVAILABLE"
    assert status["generation_identity"]["dataset_epoch"] == "epoch-proof"
    assert status["generation_identity"]["last_ingestion"]["status"] == "COMPLETE"
    assert before == after == 0


def test_first_segment_leaves_existing_rows_legacy_unbound_and_next_segment_has_boundary(tmp_path):
    store = ResearchStore(str(tmp_path))
    try:
        store.append_event({"event_name": "LEGACY", "ts": "2026-08-27T00:00:00Z"})
        first = store.record_generation_identity(
            dataset_epoch="epoch-shared", deployed_revision="1" * 40,
            tile_config_signature="2" * 64,
        )
        assert first["legacy_unbound_counts"]["research_events"] == 1
        assert first["start_boundaries"]["research_events"]["max_rowid"] == 1
        store.append_event({"event_name": "FIRST_SEGMENT", "ts": "2026-08-29T00:00:00Z"})
        second = store.record_generation_identity(
            dataset_epoch="epoch-shared", deployed_revision="3" * 40,
            tile_config_signature="2" * 64,
        )
        assert second["legacy_unbound_counts"]["research_events"] == 1
        assert second["start_boundaries"]["research_events"] == {
            "row_count": 2, "max_rowid": 2,
        }
    finally:
        store.close()


def test_same_generation_id_with_corrupt_conflicting_fields_fails_closed(tmp_path):
    store = ResearchStore(str(tmp_path))
    try:
        generation_id = store.generation_id(
            dataset_epoch="epoch-x", deployed_revision="4" * 40,
            tile_config_signature="5" * 64,
        )
        with sqlite3.connect(tmp_path / "research.db") as connection:
            connection.execute(
                """INSERT INTO research_generation_segments
                   (generation_id, dataset_epoch, deployed_revision, tile_config_signature,
                    schema_version, recorded_at, start_boundaries_json,
                    legacy_unbound_counts_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (generation_id, "epoch-corrupt", "4" * 40, "5" * 64,
                 "research_generation_segment_v2", "now", "{}", "{}"),
            )
        with pytest.raises(ValueError, match="GENERATION_IDENTITY_CONFLICT"):
            store.record_generation_identity(
                dataset_epoch="epoch-x", deployed_revision="4" * 40,
                tile_config_signature="5" * 64,
            )
    finally:
        store.close()


def test_store_rejects_short_revision_without_bridge(tmp_path):
    store = ResearchStore(str(tmp_path))
    try:
        with pytest.raises(ValueError, match="GENOME_DEPLOYED_REVISION_NOT_EXACT_FULL_SHA"):
            store.record_generation_identity(
                dataset_epoch="epoch-x", deployed_revision="abc1234",
                tile_config_signature="5" * 64,
            )
    finally:
        store.close()


@pytest.mark.parametrize(
    ("column", "bad_value"),
    (("start_boundaries_json", "not-json"),
     ("legacy_unbound_counts_json", '{"research_events":-1}')),
)
def test_preflight_fails_closed_on_malformed_generation_metadata(tmp_path, column, bad_value):
    store = ResearchStore(str(tmp_path))
    try:
        identity = store.record_generation_identity(
            dataset_epoch="epoch-valid", deployed_revision="6" * 40,
            tile_config_signature="7" * 64,
        )
    finally:
        store.close()
    with sqlite3.connect(tmp_path / "research.db") as connection:
        connection.execute(
            f"UPDATE research_generation_segments SET {column} = ? WHERE generation_id = ?",
            (bad_value, identity["generation_id"]),
        )
    status = _source_preflight(str(tmp_path / "research.db"))
    assert status["status"] == "GENOME_SOURCE_UNAVAILABLE"
    assert status["reason"] == "GENERATION_IDENTITY_METADATA_INVALID"
    assert status["generation_identity_status"] == "INVALID"


def test_new_segment_refuses_prior_metadata_corruption_or_boundary_regression(tmp_path):
    store = ResearchStore(str(tmp_path))
    try:
        store.append_event({"event_name": "BOUND", "ts": "2026-08-29T00:00:00Z"})
        first = store.record_generation_identity(
            dataset_epoch="epoch-bound", deployed_revision="8" * 40,
            tile_config_signature="9" * 64,
        )
        with store._lock:
            store._conn.execute("DELETE FROM research_events")
            store._conn.commit()
        with pytest.raises(ValueError, match="GENERATION_IDENTITY_BOUNDARY_REGRESSION"):
            store.record_generation_identity(
                dataset_epoch="epoch-bound", deployed_revision="a" * 40,
                tile_config_signature="9" * 64,
            )
        with store._lock:
            store._conn.execute(
                "UPDATE research_generation_segments SET start_boundaries_json = ? "
                "WHERE generation_id = ?", ("not-json", first["generation_id"]),
            )
            store._conn.commit()
        with pytest.raises(ValueError):
            store.record_generation_identity(
                dataset_epoch="epoch-bound", deployed_revision="b" * 40,
                tile_config_signature="9" * 64,
            )
    finally:
        store.close()


def test_legacy_database_is_explicitly_unverifiable_not_relabelled(tmp_path):
    db = tmp_path / "research.db"
    with sqlite3.connect(db) as connection:
        for table in (
            "environment_genome", "market_genome", "decision_genome",
            "execution_genome", "lifecycle_genome", "trade_genome",
        ):
            connection.execute(f"CREATE TABLE {table} (id TEXT)")
    status = _source_preflight(str(db))
    assert status["status"] == "AVAILABLE"
    assert status["generation_identity_status"] == "IDENTITY_METADATA_MISSING"
    assert status["generation_identity"] is None
    with sqlite3.connect(db) as connection:
        assert "research_generation_segments" not in {
            row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }


def test_bridge_binds_exact_generation_and_truthful_initial_status(tmp_path):
    bridge = GenomeBridge(
        str(tmp_path), dataset_epoch="epoch-live", deployed_revision="f" * 40,
        tile_config_signature="a" * 64,
    )
    try:
        identity = bridge.stats()["generation_identity"]
        assert identity["dataset_epoch"] == "epoch-live"
        assert identity["deployed_revision"] == "f" * 40
        assert identity["last_ingestion"]["status"] == "BRIDGE_INITIALIZED"
        assert identity["last_ingestion"]["opportunity_count"] is None
        assert identity["last_ingestion"]["detail"]["opportunity_count_status"] == (
            "UNAVAILABLE_IN_GENOME_STORE"
        )
    finally:
        bridge.store.close()


def test_bridge_rejects_short_revision_and_appends_new_revision_segment(tmp_path):
    with pytest.raises(ValueError, match="GENOME_DEPLOYED_REVISION_NOT_EXACT_FULL_SHA"):
        GenomeBridge(
            str(tmp_path), dataset_epoch="epoch-live", deployed_revision="abc1234",
            tile_config_signature="a" * 64,
        )
    bridge = GenomeBridge(
        str(tmp_path), dataset_epoch="epoch-live", deployed_revision="b" * 40,
        tile_config_signature="a" * 64,
    )
    try:
        first = bridge.stats()["generation_identity"]["generation_id"]
        second = bridge.bind_generation_identity(
            dataset_epoch="epoch-live", deployed_revision="c" * 40,
            tile_config_signature="a" * 64,
        )
        assert second["generation_id"] != first
    finally:
        bridge.store.close()


def test_fresh_reset_requires_new_generation_binding(tmp_path):
    bridge = GenomeBridge(
        str(tmp_path), dataset_epoch="epoch-one", deployed_revision="d" * 40,
        tile_config_signature="e" * 64,
    )
    try:
        bridge.reset_research_store()
        assert bridge.stats()["generation_identity"] is None
        rebound = bridge.bind_generation_identity(
            dataset_epoch="epoch-two", deployed_revision="d" * 40,
            tile_config_signature="e" * 64,
        )
        assert rebound["dataset_epoch"] == "epoch-two"
    finally:
        bridge.store.close()


def test_production_startup_and_fresh_reset_wire_exact_identity_inputs():
    assert 'deployed_revision=os.getenv("SOURCE_GIT_REV")' in BOT_SOURCE
    assert "dataset_epoch=_collector_v22_epoch_id()" in BOT_SOURCE
    assert "tile_config_signature=active_tile_registry_signature()" in BOT_SOURCE
    assert "bridge.bind_generation_identity(" in BOT_SOURCE
    assert '"GENOME_IDENTITY_INVALID": 1000' in BOT_SOURCE
    assert BOT_SOURCE.count('set_execution_paused("GENOME_IDENTITY_INVALID")') == 2
    assert "bridge identity init failed closed" in BOT_SOURCE
    assert 'if active_reason == "GENOME_IDENTITY_INVALID":' in BOT_SOURCE
    assert '"remediation": "restart with valid exact generation identity metadata"' in BOT_SOURCE
    assert 'state.get("execution_reason") == "GENOME_IDENTITY_INVALID"' in BOT_SOURCE
    assert "GENOME_IDENTITY_INVALID requires a valid restart" in BOT_SOURCE
