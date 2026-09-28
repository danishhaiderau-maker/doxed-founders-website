import json
import os
import sys

import pytest


sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("FORCE_PAPER_MODE", "1")
os.environ.setdefault("RESEARCH_DATA_COLLECTION", "1")
os.environ.setdefault("SKIP_EXCHANGE_MARKET_LOAD", "1")

import bot


RESET_ID = "a" * 24


@pytest.fixture
def resume_runtime(monkeypatch, tmp_path):
    calls = {"readiness": 0, "save": 0, "resume": 0, "cache": 0}

    def readiness():
        calls["readiness"] += 1
        return {"system_ready": True, "ws_transport_ready": True, "readiness_reasons": []}

    def save():
        calls["save"] += 1

    def clear_pause(reason):
        assert reason == ""
        calls["resume"] += 1
        with bot.state_lock:
            bot.state["execution_paused"] = False
            bot.state["execution_reason"] = ""

    def patch_cache(**fields):
        calls["cache"] += 1

    monkeypatch.setattr(bot, "_data_sync_runtime_root", lambda: tmp_path)
    monkeypatch.setattr(bot, "_recompute_system_readiness", readiness)
    monkeypatch.setattr(bot, "save_persistent_config", save)
    monkeypatch.setattr(bot, "set_execution_paused", clear_pause)
    monkeypatch.setattr(bot, "_patch_api_state_cache_fields", patch_cache)
    monkeypatch.setattr(bot, "_DASHBOARD_BOOTSTRAP_COMPLETE", True)
    monkeypatch.setattr(bot, "_BOT_ADMIN_TOKEN", "")
    with bot.state_lock:
        bot.state["execution_paused"] = True
        bot.state["execution_reason"] = "ADMIN_MANUAL"
        bot.state["manual_admin_pause"] = True
    yield tmp_path, calls
    assert not bot._fresh_collection_lock.locked()


def _write_reset_receipts(root, *, stage="PAYLOAD_DELETION", pointer=None):
    receipts = root / "research_reset_receipts"
    operation = receipts / RESET_ID / "operation.json"
    operation.parent.mkdir(parents=True)
    (receipts / "ACTIVE_RESET.json").write_text(
        json.dumps(pointer or {"reset_id": RESET_ID, "binding_sha256": "b" * 64}),
        encoding="utf-8",
    )
    operation.write_text(json.dumps({"stage": stage}), encoding="utf-8")


def _assert_no_resume_mutation(calls):
    assert calls == {"readiness": 0, "save": 0, "resume": 0, "cache": 0}
    with bot.state_lock:
        assert bot.state["execution_paused"] is True
        assert bot.state["execution_reason"] == "ADMIN_MANUAL"
        assert bot.state["manual_admin_pause"] is True


def test_resume_lock_contention_blocks_before_readiness_or_mutation(resume_runtime):
    _, calls = resume_runtime
    assert bot._fresh_collection_lock.acquire(blocking=False)
    try:
        with bot.app.test_client() as client:
            response = client.post("/api/resume", environ_base={"REMOTE_ADDR": "127.0.0.1"})
    finally:
        bot._fresh_collection_lock.release()
    assert response.status_code == 409
    assert response.get_json()["reason"] == "FRESH_COLLECTION_RESET_IN_PROGRESS"
    _assert_no_resume_mutation(calls)


def test_nonterminal_active_reset_receipt_blocks_without_mutation(resume_runtime):
    root, calls = resume_runtime
    _write_reset_receipts(root)
    with bot.app.test_client() as client:
        response = client.post("/api/resume", environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert response.status_code == 409
    assert response.get_json()["reason"] == "FRESH_COLLECTION_RESET_IN_PROGRESS"
    _assert_no_resume_mutation(calls)


@pytest.mark.parametrize("pointer_text", ["{", json.dumps({"reset_id": "../escape"})])
def test_unreadable_or_unsafe_active_reset_receipt_fails_closed(
    resume_runtime, pointer_text
):
    root, calls = resume_runtime
    receipts = root / "research_reset_receipts"
    receipts.mkdir(parents=True)
    (receipts / "ACTIVE_RESET.json").write_text(pointer_text, encoding="utf-8")
    with bot.app.test_client() as client:
        response = client.post("/api/resume", environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert response.status_code == 409
    assert response.get_json()["reason"] == "RESET_RECEIPT_UNREADABLE"
    _assert_no_resume_mutation(calls)


@pytest.mark.parametrize("completed_pointer", [False, True])
def test_normal_resume_contract_is_unchanged(resume_runtime, completed_pointer):
    root, calls = resume_runtime
    if completed_pointer:
        _write_reset_receipts(root, stage="COMPLETE")
    with bot.app.test_client() as client:
        response = client.post("/api/resume", environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert response.status_code == 200
    assert response.get_json() == {"status": "resumed", "execution_paused": False}
    assert calls == {"readiness": 1, "save": 1, "resume": 1, "cache": 1}
    with bot.state_lock:
        assert bot.state["execution_paused"] is False
        assert bot.state["execution_reason"] == ""
        assert bot.state["manual_admin_pause"] is False


def test_large_terminal_operation_allows_resume_and_fresh_reset_noop(
    resume_runtime, monkeypatch,
):
    root, calls = resume_runtime
    receipts = root / "research_reset_receipts"
    operation = receipts / RESET_ID / "operation.json"
    operation.parent.mkdir(parents=True)
    (receipts / "ACTIVE_RESET.json").write_text(
        json.dumps({"reset_id": RESET_ID, "binding_sha256": "b" * 64}),
        encoding="utf-8",
    )
    target_size = 36_081_612
    prefix = b'{"padding":"'
    suffix = b'","stage":"COMPLETE"}'
    operation.write_bytes(
        prefix + (b"x" * (target_size - len(prefix) - len(suffix))) + suffix
    )
    monkeypatch.setattr(bot, "_fresh_research_reset_assert_quiesced", lambda: None)

    assert bot._fresh_research_reset_resume() is None
    with bot.app.test_client() as client:
        response = client.post("/api/resume", environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert response.status_code == 200
    assert calls == {"readiness": 1, "save": 1, "resume": 1, "cache": 1}
