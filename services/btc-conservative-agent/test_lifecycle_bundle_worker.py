import json
import hashlib
import os
import subprocess
import sys
from pathlib import Path

import lifecycle_bundle_worker as worker


def _request(tmp_path: Path, nonce: str, **updates):
    root = tmp_path / "data"
    work = root / ".lifecycle-bundle-work"
    work.mkdir(parents=True)
    payload = {
        "schema": worker.REQUEST_SCHEMA,
        "nonce": nonce,
        "data_root": str(root.resolve()),
        "work_root": str(work.resolve()),
        "source_revision": "a" * 40,
        "launched_unix": 90.0,
        "now": 100.0,
        "max_bundles": 3,
    }
    payload.update(updates)
    request = work / f"bundle-request-{nonce}.json"
    result = work / f"bundle-result-{nonce}.json"
    request.write_text(json.dumps(payload), encoding="utf-8")
    return root, request, result


def test_worker_materializes_with_nonce_bound_atomic_result(tmp_path, monkeypatch):
    nonce = "1" * 32
    root, request, result = _request(tmp_path, nonce)
    calls = []
    monkeypatch.setattr(worker, "materialize_ready_bundles", lambda *args, **kwargs: (
        calls.append((args, kwargs)) or {
            "schema": "lifecycle_bundle_materialization_result_v1",
            "candidate_count": 0, "materialized_or_verified": 0, "bundles": [],
            "source_cleanup_authorized": False,
        }
    ))
    assert worker.run(request, result, nonce) == 0
    receipt = json.loads(result.read_text(encoding="utf-8"))
    assert calls == [((root.resolve(),), {
        "now": 100.0, "max_bundles": 3,
        "max_scan_bytes": worker.DEFAULT_MAX_SCAN_BYTES,
        "max_scan_rows": worker.DEFAULT_MAX_SCAN_ROWS,
        "max_runtime_sec": 60.0,
    })]
    assert receipt["schema"] == worker.RESULT_SCHEMA
    assert receipt["nonce"] == nonce
    assert receipt["source_revision"] == "a" * 40
    assert receipt["requested_max_bundles"] == 3
    assert receipt["request_sha256"] == hashlib.sha256(request.read_bytes()).hexdigest()
    supplied = receipt.pop("result_sha256")
    assert supplied == hashlib.sha256(
        json.dumps(receipt, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).hexdigest()
    assert receipt["source_cleanup_authorized"] is False
    assert not list(result.parent.glob("*.tmp"))


def test_worker_rejects_nonce_path_and_root_escape(tmp_path, monkeypatch):
    nonce = "2" * 32
    _, request, result = _request(tmp_path, nonce)
    monkeypatch.setattr(worker, "materialize_ready_bundles", lambda *args, **kwargs: {})
    assert worker.run(request, result, "3" * 32) == 1
    assert worker.run(request, tmp_path / result.name, nonce) == 1
    assert not result.exists()


def test_worker_rejects_linked_data_root(tmp_path, monkeypatch):
    if os.name == "nt":
        target = tmp_path / "target"
        target.mkdir()
        linked = tmp_path / "linked"
        completed = subprocess.run(["cmd", "/c", "mklink", "/J", str(linked), str(target)], capture_output=True, text=True)
        assert completed.returncode == 0, completed.stderr or completed.stdout
    else:
        target = tmp_path / "target"
        target.mkdir()
        linked = tmp_path / "linked"
        linked.symlink_to(target, target_is_directory=True)
    work = target / "work"
    work.mkdir()
    nonce = "4" * 32
    request = work / f"bundle-request-{nonce}.json"
    result = work / f"bundle-result-{nonce}.json"
    request.write_text(json.dumps({
        "schema": worker.REQUEST_SCHEMA, "nonce": nonce,
        "data_root": str(linked), "work_root": str(work), "max_bundles": 1,
    }), encoding="utf-8")
    monkeypatch.setattr(worker, "materialize_ready_bundles", lambda *args, **kwargs: {})
    assert worker.run(request, result, nonce) == 1
    assert not result.exists()


def test_worker_rejects_credentials_unknown_fields_and_unbounded_work(tmp_path, monkeypatch):
    monkeypatch.setattr(worker, "materialize_ready_bundles", lambda *args, **kwargs: {})
    for index, updates in enumerate((
        {"api_key": "must-not-cross-boundary"},
        {"extra": "unsupported"},
        {"max_bundles": 0},
        {"max_bundles": worker.MAX_BUNDLES_PER_RUN + 1},
        {"max_bundles": True},
        {"max_scan_bytes": worker.MAX_SCAN_BYTES_PER_RUN + 1},
        {"max_scan_rows": worker.MAX_SCAN_ROWS_PER_RUN + 1},
        {"max_runtime_sec": worker.MAX_RUNTIME_SEC + 1},
    ), 5):
        nonce = format(index, "x") * 32
        _, request, result = _request(tmp_path / str(index), nonce, **updates)
        assert worker.run(request, result, nonce) == 1
        assert not result.exists()


def test_real_worker_is_secret_free_and_handles_empty_root(tmp_path):
    nonce = "a" * 32
    _, request, result = _request(tmp_path, nonce, now=None, max_bundles=1)
    env = {"PYTHONIOENCODING": "utf-8", "PYTHONHASHSEED": "0", "PYTHONNOUSERSITE": "1"}
    completed = subprocess.run(
        [sys.executable, str(Path(worker.__file__)), "--request", str(request),
         "--result", str(result), "--nonce", nonce],
        cwd=Path(worker.__file__).parent, env=env, capture_output=True, timeout=15,
    )
    assert completed.returncode == 0, completed.stderr.decode(errors="replace")
    receipt = json.loads(result.read_text(encoding="utf-8"))
    assert receipt["materialization"]["candidate_count"] == 0
    serialized = result.read_text(encoding="utf-8").lower()
    assert all(marker not in serialized for marker in ("api_key", "api_secret", "password", "credential"))
