import hashlib
import json
from pathlib import Path
import pytest
import data_sync_bundle_retirement as retirement
import data_sync_bundle_storage as storage
import data_sync_bundle_runtime as runtime
from test_data_sync_bundle_runtime import _status_fixture


def args(tmp_path):
    meta, source, output, directory = _status_fixture(tmp_path)
    state = directory / "bundle-worker-state.json"
    return meta, source, output, directory, dict(current_generation="b" * 64,
        expected_state_sha256=hashlib.sha256(state.read_bytes()).hexdigest(),
        protected_generations=lambda: set(), receipt_path=tmp_path / "retirement.json")


def test_retire_one_validated_derivative_preserves_source(tmp_path):
    meta, source, output, directory, options = args(tmp_path)
    original = {str(p): p.read_bytes() for p in source.rglob("*") if p.is_file()}
    assert runtime._persist_coordinator_status(meta, source, output, {"status": "COMPLETE"}, started_at="test")
    receipt = retirement.retire_derivative_generation(source, output, meta["generation_id"], **options)
    assert receipt["status"] == "COMPLETE" and receipt["raw_source_deleted"] is False
    assert not directory.exists()
    assert all(__import__('pathlib').Path(p).read_bytes() == raw for p, raw in original.items())
    assert json.loads(options["receipt_path"].read_text())["status"] == "COMPLETE"


@pytest.mark.parametrize("defect", ["protected", "hash", "orphan", "current", "package"])
def test_retirement_refuses_unproven_or_active_targets(tmp_path, defect):
    meta, source, output, directory, options = args(tmp_path)
    if defect == "protected": options["protected_generations"] = lambda: {meta["generation_id"]}
    if defect == "hash": options["expected_state_sha256"] = "0" * 64
    if defect == "current": options["current_generation"] = meta["generation_id"]
    if defect == "orphan": (directory / "unknown").write_text("keep")
    if defect == "package": next((directory / "packages").iterdir()).write_bytes(b"wrong")
    with pytest.raises(ValueError):
        retirement.retire_derivative_generation(source, output, meta["generation_id"], **options)
    assert directory.exists() and not options["receipt_path"].exists()


def test_storage_admits_only_valid_diagnostics(tmp_path):
    meta, source, output, directory, options = args(tmp_path)
    assert runtime._persist_coordinator_status(meta, source, output, {"status": "BUILDING"}, started_at="test")
    assert storage.check_derivative_admission(output, meta["generation_id"], 1)["status"] == "ADMITTED"
    early_meta = {**meta, "generation_id": "c" * 64}
    assert runtime._persist_coordinator_status(early_meta, source, output, {"status": "FAILED"}, started_at="test")
    assert storage.check_derivative_admission(output, meta["generation_id"], 1)["status"] == "ADMITTED"
    (output / runtime.COORDINATOR_EARLY_STATUS_FILE).write_text('{}')
    with pytest.raises(ValueError): storage.check_derivative_admission(output, meta["generation_id"], 1)


def test_retirement_requires_worker_lease(tmp_path):
    meta, source, output, directory, options = args(tmp_path)
    with retirement._singleton_lease(output / ".bundle-worker.lease"):
        with pytest.raises(Exception, match="LEASE_HELD"):
            retirement.retire_derivative_generation(source, output, meta["generation_id"], **options)
    assert directory.exists() and not options["receipt_path"].exists()


def test_retirement_rechecks_protection_before_intent(tmp_path):
    meta, source, output, directory, options = args(tmp_path)
    calls = []
    def protection():
        calls.append(1)
        return set() if len(calls) == 1 else {meta["generation_id"]}
    options["protected_generations"] = protection
    with pytest.raises(ValueError, match="PROTECTED"):
        retirement.retire_derivative_generation(source, output, meta["generation_id"], **options)
    assert directory.exists() and not options["receipt_path"].exists()


def test_retirement_rejects_source_receipt(tmp_path):
    meta, source, output, directory, options = args(tmp_path)
    options["receipt_path"] = source / "receipt.json"
    with pytest.raises(ValueError, match="MUST_BE_EXTERNAL"):
        retirement.retire_derivative_generation(source, output, meta["generation_id"], **options)
    assert directory.exists()


@pytest.mark.parametrize("failure", ["second_unlink", "rmdir", "replace"])
def test_interrupted_retirement_resumes_exact_receipt(tmp_path, monkeypatch, failure):
    meta, source, output, directory, options = args(tmp_path)
    original_unlink, original_rmdir, original_replace = Path.unlink, Path.rmdir, retirement.os.replace
    calls = []
    def unlink(path, *args, **kwargs):
        if directory in path.parents:
            calls.append(1)
            if failure == "second_unlink" and len(calls) == 2:
                raise OSError("interrupted")
        return original_unlink(path, *args, **kwargs)
    def rmdir(path, *args, **kwargs):
        if failure == "rmdir" and directory in path.parents:
            raise OSError("interrupted")
        return original_rmdir(path, *args, **kwargs)
    def replace(*args):
        if failure == "replace": raise OSError("interrupted")
        return original_replace(*args)
    with monkeypatch.context() as scoped:
        scoped.setattr(Path, "unlink", unlink)
        scoped.setattr(Path, "rmdir", rmdir)
        scoped.setattr(retirement.os, "replace", replace)
        with pytest.raises(OSError):
            retirement.retire_derivative_generation(source, output, meta["generation_id"], **options)
    assert json.loads(options["receipt_path"].read_text())["status"] == "VERIFIED_RETIREMENT_INTENT"
    result = retirement.retire_derivative_generation(source, output, meta["generation_id"], **options)
    assert result["status"] == "COMPLETE" and not directory.exists()


@pytest.mark.parametrize("substitution", ["directory", "symlink"])
def test_post_intent_directory_substitution_is_refused(tmp_path, monkeypatch, substitution):
    meta, source, output, directory, options = args(tmp_path)
    original_open = Path.open
    moved = tmp_path / "moved"
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "bundle-worker-state.json").write_text("outside preserve")
    if substitution == "symlink":
        probe = tmp_path / "probe"
        try: probe.symlink_to(outside, target_is_directory=True)
        except OSError: pytest.skip("symlink creation unavailable")
        probe.unlink()
    class SwapAfterClose:
        def __init__(self, stream): self.stream = stream
        def __enter__(self): return self.stream
        def __exit__(self, *args):
            self.stream.close()
            directory.rename(moved)
            if substitution == "symlink":
                directory.symlink_to(outside, target_is_directory=True)
            else:
                directory.mkdir()
                (directory / "bundle-worker-state.json").write_text("outside preserve")
    def open_(path, *args, **kwargs):
        stream = original_open(path, *args, **kwargs)
        return SwapAfterClose(stream) if path == options["receipt_path"] and args and args[0] == "x" else stream
    monkeypatch.setattr(Path, "open", open_)
    with pytest.raises(ValueError, match="DIRECTORY_CHANGED|LINK_REJECTED"):
        retirement.retire_derivative_generation(source, output, meta["generation_id"], **options)
    assert (directory / "bundle-worker-state.json").read_text() == "outside preserve"
    assert (moved / "bundle-worker-state.json").exists()
