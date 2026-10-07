"""Tests for the signed, hash-chained order-action audit log."""
from __future__ import annotations

import json

import pytest

from order_action_audit import (
    OrderActionAudit,
    ACTION_ORDER_PLACED,
    ACTION_ORDER_FILLED,
    ACTION_ORDER_REJECTED,
    ACTION_BALANCE_SNAPSHOT,
    GENESIS_HASH,
)


KEY = b"test-hmac-secret"


def test_record_is_monotonic_and_chained(tmp_path):
    a = OrderActionAudit(tmp_path / "audit.jsonl", key=KEY)
    r1 = a.record(action_type=ACTION_ORDER_PLACED, trade_id="T1", order_id="O1")
    r2 = a.record(action_type=ACTION_ORDER_FILLED, trade_id="T1", order_id="O1")
    assert r1["seq"] == 1
    assert r2["seq"] == 2
    assert r1["prev_hash"] == GENESIS_HASH
    assert r2["prev_hash"] == r1["hash"]
    assert r1["sig"]


def test_verify_passes_when_intact(tmp_path):
    a = OrderActionAudit(tmp_path / "audit.jsonl", key=KEY)
    a.record(action_type=ACTION_ORDER_PLACED, trade_id="T1")
    a.record(action_type=ACTION_ORDER_FILLED, trade_id="T1")
    assert a.verify()["ok"] is True


def test_verify_detects_tamper(tmp_path):
    path = tmp_path / "audit.jsonl"
    a = OrderActionAudit(path, key=KEY)
    a.record(action_type=ACTION_ORDER_PLACED, trade_id="T1", qty=1.0)
    # Tamper with the persisted file directly (change qty).
    rows = path.read_text(encoding="utf-8").splitlines()
    row = json.loads(rows[0])
    row["qty"] = 999.0
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")
    b = OrderActionAudit(path, key=KEY)
    v = b.verify()
    assert v["ok"] is False
    assert v["reason"] in ("HASH_CHAIN_BREAK", "SIGNATURE_MISMATCH")


def test_verify_detects_deleted_row(tmp_path):
    path = tmp_path / "audit.jsonl"
    a = OrderActionAudit(path, key=KEY)
    a.record(action_type=ACTION_ORDER_PLACED, trade_id="T1")
    a.record(action_type=ACTION_ORDER_FILLED, trade_id="T1")
    # Drop the first line.
    lines = path.read_text(encoding="utf-8").splitlines()
    path.write_text(lines[1] + "\n", encoding="utf-8")
    b = OrderActionAudit(path, key=KEY)
    v = b.verify()
    assert v["ok"] is False


def test_unsigned_audit_has_no_sig(tmp_path):
    a = OrderActionAudit(tmp_path / "audit.jsonl", key=None)
    r = a.record(action_type=ACTION_BALANCE_SNAPSHOT)
    assert r["sig"] == ""
    assert a.status()["signed"] is False


def test_query_filters(tmp_path):
    a = OrderActionAudit(tmp_path / "audit.jsonl", key=KEY)
    a.record(action_type=ACTION_ORDER_PLACED, trade_id="T1", lane="X")
    a.record(action_type=ACTION_ORDER_FILLED, trade_id="T2", lane="Y")
    a.record(action_type=ACTION_ORDER_REJECTED, trade_id="T1", lane="X")
    assert len(a.query(trade_id="T1")) == 2
    assert len(a.query(action_type=ACTION_ORDER_FILLED)) == 1
    assert len(a.query(lane="Y")) == 1
    assert len(a.query(since_seq=1)) == 2


def test_unknown_action_rejected(tmp_path):
    a = OrderActionAudit(tmp_path / "audit.jsonl", key=KEY)
    with pytest.raises(ValueError):
        a.record(action_type="NOT_AN_ACTION")


def test_status_reports_verify(tmp_path):
    a = OrderActionAudit(tmp_path / "audit.jsonl", key=KEY)
    a.record(action_type=ACTION_ORDER_PLACED)
    st = a.status()
    assert st["rows"] == 1
    assert st["verify"]["ok"] is True
