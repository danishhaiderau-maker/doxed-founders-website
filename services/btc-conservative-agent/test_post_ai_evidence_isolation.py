"""Regressions for the production post-AI replay-lock stall."""

from __future__ import annotations

import ast
import threading
import time
from pathlib import Path

from bounded_evidence_worker import BoundedEvidenceWorker


BOT = Path(__file__).with_name("bot.py")
SOURCE = BOT.read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)


def _function(name: str) -> ast.FunctionDef:
    return next(
        node
        for node in TREE.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )


def _source(name: str) -> str:
    result = ast.get_source_segment(SOURCE, _function(name))
    assert result is not None
    return result


def test_authoritative_ai_path_only_enqueues_optional_post_ai_studies() -> None:
    body = _source("evaluate_signal_with_ai")
    assert "enqueue_post_ai_research_hooks(ctx, ai_result, research_lane)" in body
    assert "start_reversal_study_replay(ctx, ai_result, research_lane)" not in body
    assert "log_ai_reason_research(ctx, ai_result, research_lane)" not in body


def test_replay_buffer_lock_acquisition_is_time_bounded() -> None:
    body = _source("start_replay_buffer")
    assert "replay_lock.acquire(timeout=2.0)" in body
    assert "REPLAY_BUFFER" in body
    assert "lock timeout" in body


def test_health_exposes_scheduler_stack_replay_lock_and_post_ai_workers() -> None:
    body = _source("_strategy_progress_health_snapshot")
    assert '"stack_tail"' in body
    assert '"stage_age_sec"' in body
    assert '"replay_lock_diagnostics"' in body
    assert '"post_ai_evidence"' in body


def test_a_blocked_optional_worker_does_not_block_caller_or_other_worker() -> None:
    blocked = threading.Event()
    release = threading.Event()
    completed = threading.Event()

    def blocked_handler(_job: dict) -> None:
        blocked.set()
        release.wait(2.0)

    def healthy_handler(_job: dict) -> None:
        completed.set()

    reversal = BoundedEvidenceWorker(blocked_handler, max_queue=2, max_retries=0)
    reason = BoundedEvidenceWorker(healthy_handler, max_queue=2, max_retries=0)
    started = time.monotonic()
    assert reversal.submit("reversal:one", {"hook": "reversal_study"}) is True
    assert reason.submit("reason:one", {"hook": "ai_reason"}) is True
    assert time.monotonic() - started < 0.25
    assert blocked.wait(0.5)
    assert completed.wait(0.5)
    release.set()
    assert reversal.shutdown(drain_timeout=1.0)
    assert reason.shutdown(drain_timeout=1.0)

