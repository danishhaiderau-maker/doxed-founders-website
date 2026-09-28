"""Bounded reset-intent retry contract without importing the live bot app."""
import ast
from pathlib import Path
import threading
from unittest.mock import Mock

import pytest


@pytest.fixture
def wrapper_env():
    path = Path(__file__).with_name("bot.py")
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    function = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "perform_fresh_collection_reset"
    )

    class Clock:
        now = 100.0

        def monotonic(self):
            return self.now

        def sleep(self, seconds):
            assert env["_fresh_collection_lock"].locked()
            self.now += seconds

    clock = Clock()
    env = {
        "time": clock,
        "_fresh_collection_lock": threading.Lock(),
        "_FRESH_RESET_RETRYABLE_PREMUTATION_ERRORS": frozenset({
            "fresh_collection_sync_scheduler_busy",
            "fresh_collection_sync_builder_active",
            "fresh_collection_cleanup_lease_busy",
            "fresh_collection_epoch_writer_busy",
            "fresh_collection_research_writer_busy",
        }),
        "_FRESH_RESET_QUIESCE_TIMEOUT_SEC": 0.5,
        "_FRESH_RESET_QUIESCE_RETRY_SEC": 0.25,
    }
    env["_perform_fresh_collection_reset_locked"] = Mock()
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(path), "exec"), env)
    env["clock"] = clock
    return env


def test_transient_writer_contention_retries_under_one_reset_intent(wrapper_env):
    calls = []

    def attempt(*, send_local_signal):
        assert wrapper_env["_fresh_collection_lock"].locked()
        calls.append(send_local_signal)
        if len(calls) == 1:
            return {
                "ok": False,
                "wipe_aborted": True,
                "error": "fresh_collection_research_writer_busy",
            }
        return {"ok": True, "wipe_aborted": False, "epoch_id": "new"}

    wrapper_env["_perform_fresh_collection_reset_locked"].side_effect = attempt
    result = wrapper_env["perform_fresh_collection_reset"](send_local_signal=False)

    assert result["ok"] is True
    assert result["quiesce_attempts"] == 2
    assert result["quiesce_retry_errors"] == ["fresh_collection_research_writer_busy"]
    assert calls == [False, False]
    assert not wrapper_env["_fresh_collection_lock"].locked()


@pytest.mark.parametrize("error", [
    "fresh_collection_requires_paused_disarmed_flat_boundary",
    "fresh_collection_lifecycle_not_quiescent",
])
def test_non_contention_failure_is_never_retried(wrapper_env, error):
    wrapper_env["_perform_fresh_collection_reset_locked"].return_value = {
        "ok": False,
        "wipe_aborted": True,
        "error": error,
    }

    result = wrapper_env["perform_fresh_collection_reset"]()

    assert result["error"] == error
    wrapper_env["_perform_fresh_collection_reset_locked"].assert_called_once_with(
        send_local_signal=True
    )
    assert "quiesce_attempts" not in result


def test_ambiguous_or_partially_completed_result_is_never_retried(wrapper_env):
    wrapper_env["_perform_fresh_collection_reset_locked"].return_value = {
        "ok": False,
        "wipe_aborted": False,
        "reset_completed": True,
        "error": "fresh_collection_research_writer_busy",
    }

    result = wrapper_env["perform_fresh_collection_reset"]()

    assert result["reset_completed"] is True
    wrapper_env["_perform_fresh_collection_reset_locked"].assert_called_once()
    assert "quiesce_attempts" not in result


def test_retry_deadline_is_bounded_and_reports_exhaustion(wrapper_env):
    wrapper_env["_perform_fresh_collection_reset_locked"].return_value = {
        "ok": False,
        "wipe_aborted": True,
        "error": "fresh_collection_epoch_writer_busy",
    }

    result = wrapper_env["perform_fresh_collection_reset"]()

    assert result["quiesce_retry_exhausted"] is True
    assert result["quiesce_attempts"] == 3
    assert result["quiesce_wait_seconds"] == 0.5
    assert result["quiesce_retry_errors"] == [
        "fresh_collection_epoch_writer_busy",
        "fresh_collection_epoch_writer_busy",
        "fresh_collection_epoch_writer_busy",
    ]
    assert not wrapper_env["_fresh_collection_lock"].locked()


def test_exception_releases_reset_intent_without_retry(wrapper_env):
    wrapper_env["_perform_fresh_collection_reset_locked"].side_effect = RuntimeError(
        "boundary failure"
    )

    with pytest.raises(RuntimeError, match="boundary failure"):
        wrapper_env["perform_fresh_collection_reset"]()

    wrapper_env["_perform_fresh_collection_reset_locked"].assert_called_once()
    assert not wrapper_env["_fresh_collection_lock"].locked()


def test_retained_reset_intent_fences_cancellation_worker_restart():
    path = Path(__file__).with_name("bot.py")
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    function = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_get_cancellation_evidence_worker"
    )
    reset_lock = threading.Lock()
    reset_lock.acquire()
    worker_factory = Mock()
    env = {
        "_cancellation_evidence_worker": None,
        "_cancellation_evidence_worker_lock": threading.Lock(),
        "_cancellation_evidence_reset_fence": False,
        "_fresh_collection_lock": reset_lock,
        "BoundedEvidenceWorker": worker_factory,
        "_write_cancellation_evidence_handoff": Mock(),
        "_cancellation_evidence_dead_letter": Mock(),
    }
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(path), "exec"), env)
    try:
        assert env["_get_cancellation_evidence_worker"]() is None
    finally:
        reset_lock.release()
    worker_factory.assert_not_called()
