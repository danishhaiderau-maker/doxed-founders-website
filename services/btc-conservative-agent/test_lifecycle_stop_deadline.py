import threading
import time
import subprocess
import pytest
import lifecycle_pipeline_runtime as subject


def runtime():
    obj = object.__new__(subject.LifecyclePipelineRuntime)
    obj._lock = threading.RLock()
    obj._stop_event = threading.Event()
    obj._process = None
    obj._thread = None
    obj._status = {"running": True, "owner": True}
    obj._release_owner = lambda: None
    return obj


def test_contended_stop_and_status_preserve_owner():
    obj = runtime()
    ready, release = threading.Event(), threading.Event()
    def owner():
        with obj._lock:
            ready.set()
            release.wait(2)
    thread = threading.Thread(target=owner)
    thread.start()
    ready.wait(1)
    try:
        start = time.monotonic()
        assert obj.stop(.03) is False
        with pytest.raises(TimeoutError):
            obj.status(.03)
        assert time.monotonic() - start < .5
        assert obj._status["owner"] is True
    finally:
        release.set()
        thread.join()


def test_process_timeout_does_not_release_owner():
    obj = runtime()
    class Process:
        def poll(self): return None
        def terminate(self): pass
        def wait(self, timeout):
            time.sleep(timeout)
            raise subprocess.TimeoutExpired("worker", timeout)
    obj._process = Process()
    assert obj.stop(.03) is False
    assert obj._status["owner"] is True


def test_success_clears_owner():
    obj = runtime()
    assert obj.stop(.1)
    assert obj.status()["owner"] is False


def test_module_contention_retains_runtime(monkeypatch):
    obj = runtime()
    monkeypatch.setattr(subject, "_default_runtime", obj)
    with subject._default_lock:
        assert subject.stop(.01) is False
    assert subject._default_runtime is obj
