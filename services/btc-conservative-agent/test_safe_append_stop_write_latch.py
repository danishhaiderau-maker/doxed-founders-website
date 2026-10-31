"""R0c: _safe_append_jsonl must refuse when retention_stop_write latches the family.

Motivating stream: chase_offset_touch_grid.jsonl (ROTATION_CAP_STOP_WRITE). Shared
helper covers all CAP_ALLOW families (R4).

HM CARRY 6160f135121c / RK 40cca6bcaa7f: proofs must hit REAL latch gates in bot.py
(no fake of the latch check). Mutants:
  M1 pre-rotate_log check disabled
  M2 post-rotate_log check disabled
  M3 latch-check error allowing the write
"""
from __future__ import annotations

import os
from pathlib import Path

import retention_stop_write


def _admit_ok(monkeypatch, bot, *, rotate_log=None):
    monkeypatch.setattr(bot, "emergency_admission", lambda **kw: {
        "allowed": True, "reason": "OK", "threshold": 0.0,
    })
    monkeypatch.setattr(bot, "_validate_or_quarantine_jsonl", lambda *a, **k: None)
    monkeypatch.setattr(bot, "_csv_write_fallback", lambda *a, **k: None)
    if rotate_log is None:
        monkeypatch.setattr(bot, "rotate_log", lambda *a, **k: None)
    else:
        monkeypatch.setattr(bot, "rotate_log", rotate_log)


def test_safe_append_refuses_when_rotation_family_latched(tmp_path, monkeypatch):
    """Already-latched family: refuse before rotate (kills M1 pre-check disabled)."""
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

    rotate_calls: list[str] = []

    def _track_rotate(p, *a, **k):
        rotate_calls.append(str(p))

    _admit_ok(monkeypatch, bot, rotate_log=_track_rotate)
    monkeypatch.chdir(tmp_path)

    outcome = {}
    ok = bot._safe_append_jsonl(str(path), {"schema": "probe"}, label="TOUCH_GRID", outcome=outcome)
    assert ok is False
    assert outcome.get("status") == "STOP_WRITE_LATCHED"
    assert path.read_bytes() == before
    # Pre-gate must short-circuit: rotate_log must never run when already latched.
    assert rotate_calls == []
    retention_stop_write.clear_latches()


def test_safe_append_writes_when_not_latched(tmp_path, monkeypatch):
    import bot

    path = tmp_path / "other_research.jsonl"
    retention_stop_write.clear_latches(f"rotation:{path.name}")
    _admit_ok(monkeypatch, bot)
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
    _admit_ok(monkeypatch, bot)
    monkeypatch.setattr(bot, "_persist_jsonl_validation_receipt", lambda *a, **k: None)
    monkeypatch.setattr(bot, "_jsonl_validation_signature", lambda *a, **k: {"ok": True})
    monkeypatch.chdir(tmp_path)

    outcome = {}
    ok = bot._safe_append_jsonl(str(path), {"schema": "mut"}, label="TOUCH_GRID", outcome=outcome)
    assert ok is True  # proves the gate is what refuses; broken gate → write lands
    assert b"mut" in path.read_bytes()
    retention_stop_write.clear_latches()


def test_hm_a_rotate_log_sets_latch_then_append_refused(tmp_path, monkeypatch):
    """HM (a): rotate_log sets the latch; chase append refused via REAL is_stopped (kills M2)."""
    import bot

    path = tmp_path / "chase_offset_touch_grid.jsonl"
    path.write_text('{"ok":1}\n', encoding="utf-8")
    before = path.read_bytes()
    family = f"rotation:{path.name}"
    retention_stop_write.clear_latches()
    assert retention_stop_write.is_stopped(family) is False

    def _rotate_sets_latch(p, *a, **k):
        # Simulate enforce_cap hard_over inside rotate_log (not a fake of is_stopped).
        retention_stop_write.latch(
            f"rotation:{os.path.basename(p)}",
            reason="ROTATION_CAP_STOP_WRITE",
            used=21_000_000,
            cap=20_000_000,
        )
        assert retention_stop_write.is_stopped(f"rotation:{os.path.basename(p)}") is True

    _admit_ok(monkeypatch, bot, rotate_log=_rotate_sets_latch)
    monkeypatch.chdir(tmp_path)

    outcome = {}
    ok = bot._safe_append_jsonl(
        str(path), {"schema": "post_rotate"}, label="TOUCH_GRID", outcome=outcome,
    )
    assert ok is False
    assert outcome.get("status") == "STOP_WRITE_LATCHED"
    assert path.read_bytes() == before
    assert retention_stop_write.is_stopped(family) is True
    retention_stop_write.clear_latches()


def test_hm_b_latch_check_raises_then_append_refused(tmp_path, monkeypatch):
    """HM (b): is_stopped raises → _stop_write_latched fail-closed refuses append (kills M3)."""
    import bot

    path = tmp_path / "chase_offset_touch_grid.jsonl"
    path.write_text('{"ok":1}\n', encoding="utf-8")
    before = path.read_bytes()
    retention_stop_write.clear_latches()

    def _boom(_family):
        raise RuntimeError("latch-check boom")

    # Induce raise on the real check path; do not stub _stop_write_latched / fail-closed.
    monkeypatch.setattr(retention_stop_write, "is_stopped", _boom)
    _admit_ok(monkeypatch, bot)
    monkeypatch.chdir(tmp_path)

    outcome = {}
    ok = bot._safe_append_jsonl(
        str(path), {"schema": "boom"}, label="TOUCH_GRID", outcome=outcome,
    )
    assert ok is False
    assert outcome.get("status") == "STOP_WRITE_LATCHED"
    assert path.read_bytes() == before
    retention_stop_write.clear_latches()
