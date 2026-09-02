"""Deterministic contracts for the bounded execution-funnel repair."""

import hashlib
import json
import os
import sys

import pytest


sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("FORCE_PAPER_MODE", "1")
os.environ.setdefault("RESEARCH_DATA_COLLECTION", "1")
os.environ.setdefault("SKIP_EXCHANGE_MARKET_LOAD", "1")

import bot
import execution_funnel


def _fixture_bytes():
    return (
        b'{"stage":"APPROVE","trade_id":"one"}\n'
        b'{"stage":"BROKEN",oops}\n'
        b'{"stage":"CLOSED","trade_id":"two"}\n'
    )


def _sha(payload):
    return hashlib.sha256(payload).hexdigest()


def _bad_line_sha(payload):
    return _sha(payload.splitlines(keepends=True)[1])


def test_interior_corruption_preserves_raw_and_every_valid_line(tmp_path):
    target = tmp_path / "execution_funnel.jsonl"
    raw = _fixture_bytes()
    target.write_bytes(raw)

    receipt = bot._repair_execution_funnel_jsonl(
        str(target), expected_sha256=_sha(raw), expected_invalid_line=2,
        expected_invalid_line_sha256=_bad_line_sha(raw),
    )

    expected = raw.splitlines(keepends=True)[0] + raw.splitlines(keepends=True)[2]
    assert target.read_bytes() == expected
    assert receipt["valid_line_count"] == 2
    assert receipt["excluded_line_count"] == 1
    assert receipt["rebuilt_sha256"] == _sha(expected)
    quarantine = next((tmp_path / "corrupt_evidence_quarantine").iterdir())
    assert (quarantine / "execution_funnel.jsonl").read_bytes() == raw
    manifest = json.loads((quarantine / "quarantine_manifest.json").read_text())
    excluded = json.loads((quarantine / "excluded_lines_unknown.json").read_text())
    assert manifest["source_sha256"] == _sha(raw)
    assert manifest["invalid_line_numbers"] == [2]
    assert excluded["qualification"] == "UNKNOWN"
    assert excluded["ranking_eligible"] is False
    assert excluded["lines"][0]["raw_sha256"] == _sha(raw.splitlines(keepends=True)[1])
    validation = json.loads((tmp_path / "execution_funnel.jsonl.validation.json").read_text())
    assert validation["complete"] is True


def test_hash_or_invalid_line_mismatch_refuses_without_active_data_loss(tmp_path):
    target = tmp_path / "execution_funnel.jsonl"
    raw = _fixture_bytes()
    target.write_bytes(raw)
    with pytest.raises(RuntimeError, match="SOURCE_SHA256_MISMATCH"):
        bot._repair_execution_funnel_jsonl(
            str(target), expected_sha256="0" * 64, expected_invalid_line=2,
            expected_invalid_line_sha256=_bad_line_sha(raw),
        )
    assert target.read_bytes() == raw
    assert not (tmp_path / "corrupt_evidence_quarantine").exists()

    with pytest.raises(RuntimeError, match="INVALID_LINE_PRECONDITION_MISMATCH"):
        bot._repair_execution_funnel_jsonl(
            str(target), expected_sha256=_sha(raw), expected_invalid_line=3,
            expected_invalid_line_sha256=_bad_line_sha(raw),
        )
    assert target.read_bytes() == raw


def test_wrong_invalid_line_hash_refuses_before_quarantine(tmp_path):
    target = tmp_path / "execution_funnel.jsonl"
    raw = _fixture_bytes()
    target.write_bytes(raw)
    with pytest.raises(RuntimeError, match="INVALID_LINE_SHA256_MISMATCH"):
        bot._repair_execution_funnel_jsonl(
            str(target), expected_sha256=_sha(raw), expected_invalid_line=2,
            expected_invalid_line_sha256="f" * 64,
        )
    assert target.read_bytes() == raw
    assert not (tmp_path / "corrupt_evidence_quarantine").exists()


def test_concurrent_external_change_is_not_overwritten(tmp_path, monkeypatch):
    target = tmp_path / "execution_funnel.jsonl"
    raw = _fixture_bytes()
    concurrent = raw + b'{"stage":"ORDER","trade_id":"concurrent"}\n'
    target.write_bytes(raw)
    real_sha256_file = bot._sha256_file
    calls = 0

    def mutate_before_replace(path):
        nonlocal calls
        calls += 1
        # 1=initial source hash, 2=quarantine verification, 3=pre-replace.
        if calls == 3:
            target.write_bytes(concurrent)
        return real_sha256_file(path)

    monkeypatch.setattr(bot, "_sha256_file", mutate_before_replace)
    with pytest.raises(RuntimeError, match="SOURCE_CHANGED_DURING_REPAIR"):
        bot._repair_execution_funnel_jsonl(
            str(target), expected_sha256=_sha(raw), expected_invalid_line=2,
            expected_invalid_line_sha256=_bad_line_sha(raw),
        )
    assert target.read_bytes() == concurrent
    assert not (tmp_path / "execution_funnel.jsonl.repair.tmp").exists()


def test_idempotent_replay_returns_same_completed_receipt(tmp_path):
    target = tmp_path / "execution_funnel.jsonl"
    raw = _fixture_bytes()
    target.write_bytes(raw)
    first = bot._repair_execution_funnel_jsonl(
        str(target), expected_sha256=_sha(raw), expected_invalid_line=2,
        expected_invalid_line_sha256=_bad_line_sha(raw),
    )
    second = bot._repair_execution_funnel_jsonl(
        str(target), expected_sha256=_sha(raw), expected_invalid_line=2,
        expected_invalid_line_sha256=_bad_line_sha(raw),
    )
    assert second["status"] == "already_repaired"
    assert second["idempotent"] is True
    assert second["rebuilt_sha256"] == first["rebuilt_sha256"]
    assert len(list((tmp_path / "corrupt_evidence_quarantine").iterdir())) == 1


def test_endpoint_is_admin_paper_only_and_uses_exact_funnel_path(tmp_path, monkeypatch):
    target = tmp_path / "execution_funnel.jsonl"
    raw = _fixture_bytes()
    target.write_bytes(raw)
    monkeypatch.setattr(execution_funnel, "FUNNEL_FILE", str(target))
    monkeypatch.setattr(bot, "_DASHBOARD_BOOTSTRAP_COMPLETE", True)
    monkeypatch.setattr(bot, "_force_paper_mode_active", lambda: True)
    with bot.state_lock:
        saved_live = bot.state.get("live_armed")
        saved_bfx = bot.state.get("bitfinex_live_enabled")
        bot.state["live_armed"] = False
        bot.state["bitfinex_live_enabled"] = False
    try:
        with bot.app.test_client() as client:
            response = client.post(
                "/api/research/repair-execution-funnel",
                json={
                    "expected_sha256": _sha(raw), "expected_invalid_line": 2,
                    "expected_invalid_line_sha256": _bad_line_sha(raw),
                },
                environ_base={"REMOTE_ADDR": "127.0.0.1"},
            )
        assert response.status_code == 200
        assert response.get_json()["status"] == "repaired"
    finally:
        with bot.state_lock:
            bot.state["live_armed"] = saved_live
            bot.state["bitfinex_live_enabled"] = saved_bfx


def test_endpoint_refuses_when_live_is_armed(tmp_path, monkeypatch):
    target = tmp_path / "execution_funnel.jsonl"
    raw = _fixture_bytes()
    target.write_bytes(raw)
    monkeypatch.setattr(execution_funnel, "FUNNEL_FILE", str(target))
    monkeypatch.setattr(bot, "_DASHBOARD_BOOTSTRAP_COMPLETE", True)
    monkeypatch.setattr(bot, "_force_paper_mode_active", lambda: True)
    with bot.state_lock:
        saved_live = bot.state.get("live_armed")
        bot.state["live_armed"] = True
    try:
        with bot.app.test_client() as client:
            response = client.post(
                "/api/research/repair-execution-funnel",
                json={
                    "expected_sha256": _sha(raw), "expected_invalid_line": 2,
                    "expected_invalid_line_sha256": _bad_line_sha(raw),
                },
                environ_base={"REMOTE_ADDR": "127.0.0.1"},
            )
        assert response.status_code == 409
        assert target.read_bytes() == raw
    finally:
        with bot.state_lock:
            bot.state["live_armed"] = saved_live


def test_endpoint_requires_exact_invalid_line_sha256(tmp_path, monkeypatch):
    target = tmp_path / "execution_funnel.jsonl"
    raw = _fixture_bytes()
    target.write_bytes(raw)
    monkeypatch.setattr(execution_funnel, "FUNNEL_FILE", str(target))
    monkeypatch.setattr(bot, "_DASHBOARD_BOOTSTRAP_COMPLETE", True)
    monkeypatch.setattr(bot, "_force_paper_mode_active", lambda: True)
    with bot.state_lock:
        saved_live = bot.state.get("live_armed")
        saved_bfx = bot.state.get("bitfinex_live_enabled")
        bot.state["live_armed"] = False
        bot.state["bitfinex_live_enabled"] = False
    try:
        with bot.app.test_client() as client:
            missing = client.post(
                "/api/research/repair-execution-funnel",
                json={"expected_sha256": _sha(raw), "expected_invalid_line": 2},
                environ_base={"REMOTE_ADDR": "127.0.0.1"},
            )
            malformed = client.post(
                "/api/research/repair-execution-funnel",
                json={
                    "expected_sha256": _sha(raw), "expected_invalid_line": 2,
                    "expected_invalid_line_sha256": "ABC",
                },
                environ_base={"REMOTE_ADDR": "127.0.0.1"},
            )
        assert missing.status_code == 400
        assert malformed.status_code == 400
        assert target.read_bytes() == raw
        assert not (tmp_path / "corrupt_evidence_quarantine").exists()
    finally:
        with bot.state_lock:
            bot.state["live_armed"] = saved_live
            bot.state["bitfinex_live_enabled"] = saved_bfx
