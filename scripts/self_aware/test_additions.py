"""Regression tests for the scope-change additions (local + live sources).

Covers the pure, dependency-light pieces that do not require DuckDB or a live
network: the live-source summarizer, the aggregate diagnose reducer, the
durable webhook outbox, and the config defaults for the laptop-bound server.
Run from the repo root with:  python -m pytest scripts/self_aware/test_additions.py
"""
from __future__ import annotations

import json

import pytest

from scripts.self_aware import config, live_sources, webhook_sink


def test_server_stays_localhost_by_default():
    # Task 1: the self-aware layer stays on the laptop at 127.0.0.1:9021.
    assert config.SERVER_PORT == 9021
    # Default must be the loopback host; the env override is the only escape hatch.
    assert config.SERVER_HOST == "127.0.0.1"


def test_live_sources_opt_in_disabled_by_default():
    # Live production probing is opt-in so the daemon never phones out unasked.
    assert config.LIVE_SOURCES_ENABLED in (True, False)
    if "SELF_AWARE_LIVE_SOURCES" not in __import__("os").environ:
        assert config.LIVE_SOURCES_ENABLED is False


def _probe(ok: bool, name: str):
    return {"ok": ok, "error": None if ok else "boom", "elapsed_sec": 0.1, "url": f"https://x/{name}"}


def _doc(ok: bool):
    fly = {k: _probe(ok, f"fly.{k}") for k in ("status", "ready", "state")}
    railway = {k: _probe(ok, f"railway.{k}") for k in ("health", "observatory", "telemetry")}
    return {
        "schema": "self_aware_live_sources_v1",
        "generated_at": "2026-10-07T00:00:00Z",
        "enabled": True,
        "fly": fly,
        "railway": railway,
    }


def test_summarize_all_reachable():
    summary = live_sources.summarize(_doc(True))
    assert summary["verdict"] == "OK"
    assert summary["reachable"] == summary["total"]


def test_summarize_none_reachable():
    summary = live_sources.summarize(_doc(False))
    assert summary["verdict"] == "UNREACHABLE"
    assert summary["reachable"] == 0


def test_diagnose_folds_local_verdict(monkeypatch):
    monkeypatch.setattr(live_sources, "collect_live", lambda now=None: _doc(True))
    out = live_sources.diagnose(local_verdict="RED")
    assert out["verdict"] == "RED"
    ids = {f["id"] for f in out["findings"]}
    assert "live.local-selfaware" in ids
    # Every finding carries the typed fields the report depends on.
    for f in out["findings"]:
        assert {"id", "status", "cause", "runbook", "observed"} <= set(f)


def test_webhook_outbox_durable_roundtrip(tmp_path):
    path = tmp_path / "alerts" / "outbox.jsonl"
    outbox = webhook_sink.WebhookAlertOutbox(path)
    events = [
        {"event": "selfaware.test", "check": "X", "at": 1, "status": "RED"},
        {"event": "selfaware.test", "check": "X", "at": 1, "status": "RED"},  # deduped
    ]
    assert outbox.enqueue(events) == 1
    assert len(outbox.due()) == 1
    due = outbox.due()
    outbox.ack([due[0]["event_id"]])
    assert outbox.due() == []
    # File persisted the row before ack; after ack/compact it is empty.
    persisted = path.read_text(encoding="utf-8").strip()
    assert persisted == "" or json.loads(persisted.splitlines()[0])["event"] == "selfaware.test"


def test_flush_webhook_disabled_without_url(tmp_path):
    out = webhook_sink.flush_webhook(
        [{"event": "selfaware.test", "check": "X", "at": 1, "status": "RED"}],
        outbox_path=tmp_path / "o.jsonl",
        url="",
    )
    assert out["enabled"] is False
    assert out["sent"] == 0


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
