"""One bound collection epoch shared by session, identity cache, WAL and inventory.

Regression for the start-time alias: a normal restart rewrote
research_session.json without the epoch keys, so collectors minted a
per-process epoch-v22-* that never matched the identity cache and inventory
could never become CURRENT.
"""

import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("FORCE_PAPER_MODE", "1")
os.environ.setdefault("RESEARCH_DATA_COLLECTION", "1")
os.environ.setdefault("SKIP_EXCHANGE_MARKET_LOAD", "1")

import bot  # noqa: E402

BOUND = "epoch-v22-0123456789abcdef0123"


@pytest.fixture
def session(tmp_path, monkeypatch):
    path = tmp_path / "research_session.json"
    monkeypatch.setattr(bot, "RESEARCH_SESSION_FILE", str(path))
    with bot.state_lock:
        previous_fcm = bot.state.get("fresh_collection_mode")
        bot.state["fresh_collection_mode"] = False
    bot._update_data_sync_identity_epoch_cache(collection_epoch_id=None)
    yield path
    with bot.state_lock:
        bot.state["fresh_collection_mode"] = previous_fcm
    bot._update_data_sync_identity_epoch_cache(collection_epoch_id=None)


def _cache_epoch():
    with bot._data_sync_identity_cache_lock:
        return bot._data_sync_identity_epoch_cache.get("collection_epoch_id")


def _write(path, payload):
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_restart_session_rewrite_keeps_bound_epoch(session):
    _write(session, {"collector_v22_epoch_id": BOUND, "collector_v22_epoch_ts": 1.5})
    bot._write_research_session(1_790_000_000.0)
    saved = json.loads(session.read_text(encoding="utf-8"))
    assert saved["collector_v22_epoch_id"] == BOUND
    assert saved["collector_v22_epoch_ts"] == 1.5
    assert bot._bound_collection_epoch_id() == BOUND
    assert bot._collector_v22_epoch_id() == BOUND
    assert _cache_epoch() == BOUND


def test_unbound_session_binds_once_and_survives_restart(session):
    _write(session, {})
    assert bot._bound_collection_epoch_id() == ""
    first = bot._collector_v22_epoch_id()
    assert first.startswith("epoch-v22-")
    assert bot._collector_v22_epoch_id() == first
    bot._write_research_session(1_790_000_100.0)
    assert bot._bound_collection_epoch_id() == first
    assert _cache_epoch() == first


def test_unbound_fails_fast_and_bind_write_failure_raises(session, monkeypatch):
    _write(session, {})
    with pytest.raises(bot.CollectionEpochUnbound) as exc:
        bot._require_bound_collection_epoch_id()
    assert exc.value.code == "INVENTORY_EPOCH_UNBOUND"

    def refuse(_payload):
        raise OSError("read-only volume")

    monkeypatch.setattr(bot, "_write_research_session_payload", refuse)
    with pytest.raises(bot.CollectionEpochUnbound):
        bot._ensure_collector_v22_epoch()
    assert bot._bound_collection_epoch_id() == ""


def test_identity_cache_clears_and_rebinds(session):
    bot._update_data_sync_identity_epoch_cache(collection_epoch_id="epoch-a")
    assert _cache_epoch() == "epoch-a"
    bot._update_data_sync_identity_epoch_cache(fresh_collection_signal_ts=5.0)
    assert _cache_epoch() == "epoch-a"
    bot._update_data_sync_identity_epoch_cache(collection_epoch_id="")
    assert _cache_epoch() is None
    bot._update_data_sync_identity_epoch_cache(collection_epoch_id="epoch-b")
    assert _cache_epoch() == "epoch-b"


def test_fresh_reset_replaces_epoch_and_rebinds_cache(session):
    _write(session, {"collector_v22_epoch_id": BOUND, "collector_v22_epoch_ts": 1.0})
    bot._prime_data_sync_identity_epoch_cache()
    with bot.state_lock:
        bot.state["fresh_collection_mode"] = True
    bot._write_research_session(1_790_000_200.0, fresh_collection_reset=True)
    fresh = bot._bound_collection_epoch_id()
    assert fresh and fresh != BOUND
    assert _cache_epoch() == fresh


def test_epoch_parity_reports_match_mismatch_and_unbound(session):
    _write(session, {"collector_v22_epoch_id": BOUND})
    bot._prime_data_sync_identity_epoch_cache()
    wal = {"emergency_wal": {"identity": {"epoch_id": BOUND}}}
    parity = bot._collection_epoch_parity(wal)
    assert parity["status"] == "MATCH" and parity["match"] is True
    assert parity["session_epoch"] == parity["identity_cache_epoch"] == parity["lifecycle_wal_epoch"] == BOUND
    alias = {"emergency_wal": {"identity": {"epoch_id": "epoch-v22-startalias000000000"}}}
    assert bot._collection_epoch_parity(alias)["status"] == "MISMATCH"
    _write(session, {})
    assert bot._collection_epoch_parity(wal)["status"] == "UNBOUND"


