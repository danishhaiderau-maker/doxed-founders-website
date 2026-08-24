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


def test_slow_optional_hook_is_time_bounded_and_dead_lettered() -> None:
    release = threading.Event()
    dead = threading.Event()
    receipts = []

    def slow_handler(_job: dict) -> None:
        release.wait(2.0)

    worker = BoundedEvidenceWorker(
        slow_handler,
        max_queue=2,
        max_retries=0,
        handler_timeout_sec=0.05,
        on_dead_letter=lambda row: (receipts.append(row), dead.set()),
    )
    assert worker.submit("slow:one", {"hook": "reversal_study"}) is True
    assert dead.wait(0.5)
    assert receipts[0]["reason"] == "retries_exhausted"
    assert "exceeded" in receipts[0]["error"]
    release.set()
    assert worker.shutdown(drain_timeout=1.0)


def test_repeated_jobs_do_not_spawn_more_helpers_while_timed_out_handler_lives() -> None:
    release = threading.Event()
    calls = []
    dead_letters = []

    def wedged_handler(job: dict) -> None:
        calls.append(job["key"])
        release.wait(2.0)

    worker = BoundedEvidenceWorker(
        wedged_handler,
        max_queue=4,
        max_retries=0,
        handler_timeout_sec=0.05,
        on_dead_letter=dead_letters.append,
    )
    assert worker.submit("wedged:first", {"hook": "reversal_study"})
    deadline = time.monotonic() + 0.5
    while len(dead_letters) < 1 and time.monotonic() < deadline:
        time.sleep(0.005)
    assert worker.snapshot()["timed_out_handler_alive"] is True

    assert worker.submit("wedged:second", {"hook": "reversal_study"})
    deadline = time.monotonic() + 0.5
    while len(dead_letters) < 2 and time.monotonic() < deadline:
        time.sleep(0.005)
    assert calls == ["wedged:first"]
    assert "previous timed-out" in dead_letters[-1]["error"]

    release.set()
    assert worker.shutdown(drain_timeout=1.0)


def test_exception_in_each_post_ai_hook_is_isolated_and_next_jobs_run() -> None:
    receipts = []
    completed = []

    def handler(job: dict) -> None:
        hook = job["payload"]["hook"]
        if job["payload"].get("fail"):
            raise RuntimeError(f"{hook} failed")
        completed.append(job["key"])

    for hook in ("reversal_study", "ai_reason"):
        worker = BoundedEvidenceWorker(
            handler,
            max_queue=4,
            max_retries=0,
            handler_timeout_sec=0.25,
            on_dead_letter=receipts.append,
        )
        assert worker.submit(f"{hook}:failed", {"hook": hook, "fail": True})
        deadline = time.monotonic() + 0.5
        while len(receipts) < 1 and time.monotonic() < deadline:
            time.sleep(0.005)
        assert receipts[-1]["reason"] == "retries_exhausted"
        assert worker.submit(f"{hook}:next", {"hook": hook, "fail": False})
        deadline = time.monotonic() + 0.5
        while f"{hook}:next" not in completed and time.monotonic() < deadline:
            time.sleep(0.005)
        assert f"{hook}:next" in completed
        assert worker.shutdown(drain_timeout=1.0)


def test_ready_contract_fails_closed_for_genuine_scheduler_stall() -> None:
    body = _source("ready")
    assert "strategy_progress" in body
    assert 'strategy_progress["ok"]' in body


def test_post_ai_timeout_is_reported_as_an_explicit_evidence_gap() -> None:
    body = _source("_post_ai_dead_letter")
    assert "evidence handler exceeded" in body
    assert "HOOK_TIMEOUT" in body
    assert "_record_post_ai_evidence_gap" in body
