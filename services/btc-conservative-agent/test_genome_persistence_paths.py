from pathlib import Path

import analyzer_research_engine_v62 as analyzer
from research_genome import bridge


def test_bridge_uses_canonical_bot_runtime_volume(monkeypatch, tmp_path):
    monkeypatch.setenv("BOT_DATA_DIR", str(tmp_path))

    instance = bridge.init_genome_bridge()
    try:
        expected = (tmp_path / "runtime" / "research.db").resolve()
        assert Path(instance.store.db_path).resolve() == expected
        assert expected.is_file()
    finally:
        if instance.store._conn is not None:
            instance.store._conn.close()
        bridge._bridge = None


def test_explicit_bridge_root_is_not_reparented(monkeypatch, tmp_path):
    monkeypatch.setenv("BOT_DATA_DIR", str(tmp_path / "ignored-volume"))
    explicit = tmp_path / "explicit-runtime"

    instance = bridge.init_genome_bridge(str(explicit))
    try:
        assert Path(instance.store.db_path).resolve() == (
            explicit / "research.db"
        ).resolve()
    finally:
        if instance.store._conn is not None:
            instance.store._conn.close()
        bridge._bridge = None


def test_analyzer_genome_source_is_always_configured_mirror(monkeypatch, tmp_path):
    mirror = tmp_path / "mirror"
    monkeypatch.setenv("BTC_AGENT_DATA_DIR", str(mirror))

    resolved = analyzer._canonical_genome_source_db_path()

    assert Path(resolved) == mirror.resolve() / "research.db"
    assert "OneDrive" not in resolved
