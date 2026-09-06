import json
import pytest
from test_fly_sync_bundle_adapter import build, adapter, FakeTime


def prime(tmp_path):
    request, fetch, calls, source = build(tmp_path)
    request.update(verified_local_root=str(source), checkpoint_root=str(tmp_path / "cache"))
    adapter.run(request, emit=lambda _: None, fetch=fetch, sleep=lambda _: None)
    calls.clear()
    return request, fetch, calls, source


def test_restart_rehashes_local_but_skips_descriptor_chunks_and_throttle(tmp_path):
    request, fetch, calls, _ = prime(tmp_path)
    emitted, sleeps = [], []
    adapter.run(request, emit=emitted.append, fetch=fetch, sleep=sleeps.append)
    assert len(calls) == 1 and "/bundles?" in calls[0]
    assert sleeps == []
    assert emitted[0]["reused_local"] is True
    assert emitted[-1]["status"] == "COMPLETE" and emitted[-1]["ack_sent"] is False


@pytest.mark.parametrize("damage", ["local", "missing", "descriptor", "entry", "identity"])
def test_checkpoint_cannot_replace_current_verification(tmp_path, damage):
    request, fetch, calls, source = prime(tmp_path)
    if damage in ("local", "missing"):
        path = source / request["manifest"]["files"][0]["path"]
        # Local reuse root is separate from immutable server source.
        import shutil
        local = tmp_path / "local"
        shutil.copytree(source, local)
        request["verified_local_root"] = str(local)
        path = local / request["manifest"]["files"][0]["path"]
        if damage == "missing": path.unlink()
        else: path.write_bytes(b"x" * path.stat().st_size)
    else:
        cache = next((tmp_path / "cache").glob("*.json"))
        value = json.loads(cache.read_text())
        if damage == "descriptor": value["descriptor"]["source_git_rev"] = "bad"
        elif damage == "entry": value["entry"]["payload_bytes"] += 1
        else: value["generation"]["source_git_rev"] = "bad"
        cache.write_text(json.dumps(value))
    adapter.run(request, emit=lambda _: None, fetch=fetch, sleep=lambda _: None)
    assert any("descriptor=1" in call for call in calls)
    if damage in ("local", "missing"):
        assert any("offset=" in call for call in calls)


def test_timeout_retains_checkpoint_without_complete_then_resumes(tmp_path):
    request, fetch, calls, source = build(tmp_path)
    request.update(verified_local_root=str(source), checkpoint_root=str(tmp_path / "cache"))
    time, emitted = FakeTime(), []
    def waiting(url, **kwargs):
        status, headers, body = fetch(url, **kwargs)
        if "/bundles?" in url:
            value = json.loads(body)
            value["status"] = "BUILDING"
            body = json.dumps(value).encode()
        return status, headers, body
    with pytest.raises(ValueError, match="DEADLINE"):
        adapter.run(request, emit=emitted.append, fetch=waiting, clock=time.clock, sleep=time.sleep)
    assert not any(item["status"] == "COMPLETE" for item in emitted)
    calls.clear()
    adapter.run(request, emit=lambda _: None, fetch=fetch, sleep=lambda _: None)
    assert len(calls) == 1


def test_cache_capacity_and_malformed_cache_are_optimization_only(tmp_path, monkeypatch):
    request, fetch, calls, source = build(tmp_path)
    request.update(verified_local_root=str(source), checkpoint_root=str(tmp_path / "cache"))
    monkeypatch.setattr(adapter, "MAX_CACHE_BYTES", 1)
    adapter.run(request, emit=lambda _: None, fetch=fetch, sleep=lambda _: None)
    assert not list((tmp_path / "cache").iterdir())
    monkeypatch.setattr(adapter, "MAX_CACHE_BYTES", 32 * 1024 * 1024)
    adapter.run(request, emit=lambda _: None, fetch=fetch, sleep=lambda _: None)
    cache = next((tmp_path / "cache").glob("*.json"))
    cache.write_bytes(b"not-json")
    calls.clear()
    adapter.run(request, emit=lambda _: None, fetch=fetch, sleep=lambda _: None)
    assert any("descriptor=1" in call for call in calls)


def test_write_io_failure_preserves_success_and_inventory_is_once(tmp_path, monkeypatch):
    from pathlib import Path
    request, fetch, calls, source = build(tmp_path, 129)
    request.update(verified_local_root=str(source), checkpoint_root=str(tmp_path / "cache"))
    original = Path.iterdir
    scans = []
    def inventory(path):
        if path == tmp_path / "cache": scans.append(path)
        return original(path)
    monkeypatch.setattr(Path, "iterdir", inventory)
    def blocked(*args, **kwargs):
        raise OSError("simulated disk failure")
    monkeypatch.setattr(adapter.tempfile, "mkstemp", blocked)
    emitted = []
    adapter.run(request, emit=emitted.append, fetch=fetch, sleep=lambda _: None)
    assert emitted[-1]["status"] == "COMPLETE"
    assert emitted[-1]["ack_sent"] is False
    assert len(scans) == 1
