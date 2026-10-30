"""R0c: _safe_append_jsonl must refuse when retention_stop_write latches the family.

Motivating stream: chase_offset_touch_grid.jsonl (ROTATION_CAP_STOP_WRITE). Shared
helper covers all CAP_ALLOW families (R4). Mutation: break the is_stopped gate → fail.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import retention_stop_write


def test_safe_append_refuses_when_rotation_family_latched(tmp_path, monkeypatch):
    import bot

    path = tmp_path / "chase_offset_touch_grid.jsonl"
    path.write_text('{"ok":1}\n', encoding="utf-8")
    before = path.read_bytes()
    family = f"rotation:{path.name}"
    retention_stop_write.clear_latches()
    retention_stop_write.latch(
        family, reason="ROTATION_CAP_STOP_WRITE", used=21_000_000, cap=20_000_000,
    )
    assert retention_stop_write.is_stopped(family) is True

    monkeypatch.setattr(bot, "emergency_admission", lambda **kw: {
        "allowed": True, "reason": "OK", "threshold": 0.0,
    })
    monkeypatch.setattr(bot, "_validate_or_quarantine_jsonl", lambda *a, **k: None)
    monkeypatch.setattr(bot, "rotate_log", lambda *a, **k: None)
    monkeypatch.setattr(bot, "_csv_write_fallback", lambda *a, **k: None)
    monkeypatch.chdir(tmp_path)

    outcome = {}
    ok = bot._safe_append_jsonl(str(path), {"schema": "probe"}, label="TOUCH_GRID", outcome=outcome)
    assert ok is False
    assert outcome.get("status") == "STOP_WRITE_LATCHED"
    assert path.read_bytes() == before
    retention_stop_write.clear_latches()


def test_safe_append_writes_when_not_latched(tmp_path, monkeypatch):
    import bot

    path = tmp_path / "other_research.jsonl"
    retention_stop_write.clear_latches(f"rotation:{path.name}")
    monkeypatch.setattr(bot, "emergency_admission", lambda **kw: {
        "allowed": True, "reason": "OK", "threshold": 0.0,
    })
    monkeypatch.setattr(bot, "_validate_or_quarantine_jsonl", lambda *a, **k: None)
    monkeypatch.setattr(bot, "rotate_log", lambda *a, **k: None)
    monkeypatch.setattr(bot, "_persist_jsonl_validation_receipt", lambda *a, **k: None)
    monkeypatch.setattr(bot, "_jsonl_validation_signature", lambda *a, **k: {"ok": True})
    monkeypatch.chdir(tmp_path)

    outcome = {}
    ok = bot._safe_append_jsonl(str(path), {"schema": "probe"}, label="JSONL", outcome=outcome)
    assert ok is True
    assert outcome.get("status") == "WRITTEN"
    assert path.exists() and b"probe" in path.read_bytes()


def test_mutation_breaking_is_stopped_gate_would_allow_write(tmp_path, monkeypatch):
    """Mutation proof: if is_stopped always returns False, latched family would write."""
    import bot

    path = tmp_path / "chase_offset_touch_grid.jsonl"
    path.write_text('{"ok":1}\n', encoding="utf-8")
    family = f"rotation:{path.name}"
    retention_stop_write.clear_latches()
    retention_stop_write.latch(
        family, reason="ROTATION_CAP_STOP_WRITE", used=21_000_000, cap=20_000_000,
    )
    monkeypatch.setattr(retention_stop_write, "is_stopped", lambda _f: False)
    monkeypatch.setattr(bot, "emergency_admission", lambda **kw: {
        "allowed": True, "reason": "OK", "threshold": 0.0,
    })
    monkeypatch.setattr(bot, "_validate_or_quarantine_jsonl", lambda *a, **k: None)
    monkeypatch.setattr(bot, "rotate_log", lambda *a, **k: None)
    monkeypatch.setattr(bot, "_persist_jsonl_validation_receipt", lambda *a, **k: None)
    monkeypatch.setattr(bot, "_jsonl_validation_signature", lambda *a, **k: {"ok": True})
    monkeypatch.chdir(tmp_path)

    outcome = {}
    ok = bot._safe_append_jsonl(str(path), {"schema": "mut"}, label="TOUCH_GRID", outcome=outcome)
    assert ok is True  # proves the gate is what refuses; broken gate → write lands
    assert b"mut" in path.read_bytes()
    retention_stop_write.clear_latches()
