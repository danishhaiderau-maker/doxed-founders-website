import json
import threading
import pytest
from evidence_phase_trace import EvidencePhaseTrace


def test_current_blocked_phase_and_elapsed_are_observable_without_payload():
    now = [0.0]
    trace = EvidencePhaseTrace(clock=lambda: now[0])
    reached = threading.Event()
    release = threading.Event()
    class Gate:
        def acquire(self):
            reached.set()
            assert release.wait(2)
        def release(self):
            pass
    def run():
        with trace.hook("ai_reason", "sensitive-key-not-output"):
            with trace.acquire(Gate(), "research_gate_wait"):
                now[0] = 8
    worker = threading.Thread(target=run)
    worker.start()
    assert reached.wait(2)
    now[0] = 5
    snapshot = trace.snapshot()
    assert snapshot["active"][0]["phase"] == "research_gate_wait"
    assert snapshot["active"][0]["phase_age_seconds"] == 5
    assert "sensitive-key-not-output" not in json.dumps(snapshot)
    release.set()
    worker.join(2)
    assert not worker.is_alive()
    recent = trace.snapshot()["recent"][0]
    assert recent["phase_seconds"]["research_gate_wait"] == 5
    assert recent["elapsed_seconds"] == 8
    assert recent["outcome"] == "RETURNED"


def test_nested_phases_restore_outer_age_and_record_exception_without_message():
    now = [0.0]
    trace = EvidencePhaseTrace(clock=lambda: now[0])
    with pytest.raises(OSError):
        with trace.hook("reversal_study", "scan"):
            with trace.phase("validation"):
                now[0] = 2
                with trace.phase("file_fsync"):
                    now[0] = 3
                assert trace.snapshot()["active"][0]["phase_age_seconds"] == 3
                raise OSError("private payload")
    snapshot = trace.snapshot()
    assert snapshot["active"] == []
    assert snapshot["recent"][0]["phase_seconds"] == {"file_fsync": 1, "validation": 3}
    assert snapshot["recent"][0]["outcome"] == "RAISED"
    assert "private payload" not in json.dumps(snapshot)


def test_bounded_active_history_and_no_custom_key_dispatch():
    trace = EvidencePhaseTrace(max_active=1, history=2)
    class Secret:
        def __str__(self):
            pytest.fail("custom string dispatched")
    for _ in range(4):
        with trace.hook("ai_reason", Secret()):
            with trace.hook("reversal_study", "nested"):
                with trace.phase("validation"):
                    assert len(trace.snapshot()["active"]) == 1
    result = trace.snapshot()
    assert len(result["recent"]) == 2 and result["dropped_traces"] == 4


def test_lock_released_on_exception_and_snapshot_is_detached():
    trace = EvidencePhaseTrace()
    lock = threading.Lock()
    with pytest.raises(RuntimeError):
        with trace.hook("ai_reason", "scan"):
            with trace.acquire(lock, "path_lock_wait"):
                raise RuntimeError("failure")
    assert lock.acquire(blocking=False)
    lock.release()
    result = trace.snapshot()
    result["recent"][0]["phase_seconds"]["injected"] = 9
    assert "injected" not in trace.snapshot()["recent"][0]["phase_seconds"]


def test_uninstrumented_call_is_noop_and_unknown_labels_rejected():
    trace = EvidencePhaseTrace()
    with trace.phase("validation"):
        pass
    assert trace.snapshot()["recent"] == []
    with pytest.raises(ValueError):
        with trace.phase("secret contents"):
            pass
