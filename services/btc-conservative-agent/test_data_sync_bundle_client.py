import hashlib
import json
from pathlib import Path

import pytest
from flask import Flask

import data_sync_bundle_client as mod
from data_sync_bundle_api import register_bundle_routes
from data_sync_bundle_worker import run_bundle_worker
from test_data_sync_bundle_worker import _fixture, _row, GEN


def fixture(tmp_path, large=False):
    source = tmp_path / "src"
    rows = [_row(source, f"v3/market_segments/11/{'1'*64}.json",
                 b"x" * (1048587 if large else 123)),
            _row(source, f"v3/market_segments/22/{'2'*64}.json", b"second")]
    meta = _fixture(tmp_path, rows)
    output = tmp_path / "out"
    run_bundle_worker(meta, source, output)
    app = Flask(__name__)
    register_bundle_routes(app, authenticated=lambda: True,
                           generation_lookup=lambda _g: meta, output_root=output)
    http = app.test_client()
    index = http.get(f"/api/data-sync/bundles?generation_id={GEN}").json
    generation = {**index["generation"], "ack_eligible": True}
    requests = []

    def fetch(url, *, timeout):
        assert 0 < timeout <= 15
        requests.append(url)
        response = http.get(url)
        return response.status_code, dict(response.headers), response.data

    return index["packages"][0], generation, rows, fetch, requests


def test_worker_api_client_full_roundtrip_stages_not_promotes_or_acks(tmp_path):
    index, gen, rows, fetch, calls = fixture(tmp_path, large=True)
    stage = tmp_path / "stage"
    result = mod.fetch_verified_package(index, gen, rows, stage, fetch)
    assert result["ack_authority"] == "ORIGINAL_MANIFEST_ROWS_ONLY"
    assert len(result["members"]) == 2
    for expected, actual in zip(rows, result["members"]):
        assert {k: actual[k] for k in mod.ROW_FIELDS} == expected
        assert Path(actual["staged_path"]).read_bytes() == (tmp_path / "src" / expected["path"]).read_bytes()
        assert not (stage / expected["path"]).exists()
    assert hashlib.sha256(Path(result["package_path"]).read_bytes()).hexdigest() == index["package_sha256"]
    assert len(calls) == 3 and all("/api/data-sync/bundle?" in url for url in calls)
    assert "offset=1048576" in calls[-1]


def _local_members(tmp_path, rows):
    local = tmp_path / "canonical"
    for row in rows:
        target = local / row["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((tmp_path / "src" / row["path"]).read_bytes())
    return local


def test_verified_local_members_skip_chunks_without_staging_or_mutation(tmp_path):
    index, gen, rows, fetch, calls = fixture(tmp_path, large=True)
    local = _local_members(tmp_path, rows)
    signatures = {row["path"]: (local / row["path"]).stat() for row in rows}
    stage = tmp_path / "not-created"
    result = mod.fetch_verified_package(index, gen, rows, stage, fetch, verified_local_root=local)
    assert result["reused_local"] is True and result["staging_path"] is None
    assert "package_path" not in result
    assert result["ack_authority"] == "ORIGINAL_MANIFEST_ROWS_ONLY"
    assert len(calls) == 1 and "descriptor=1" in calls[0]
    assert not stage.exists()
    for row, member in zip(rows, result["members"]):
        path = local / row["path"]
        assert member["staged_path"] == str(path)
        assert {key: member[key] for key in mod.ROW_FIELDS} == row
        assert path.stat().st_mtime_ns == signatures[row["path"]].st_mtime_ns
        assert path.read_bytes() == (tmp_path / "src" / row["path"]).read_bytes()


@pytest.mark.parametrize("kind", ["missing", "size", "same_size_hash", "root_missing"])
def test_incomplete_local_package_downloads_normally_without_changing_local(tmp_path, kind):
    index, gen, rows, fetch, calls = fixture(tmp_path)
    local = _local_members(tmp_path, rows)
    damaged = local / rows[0]["path"]
    if kind == "missing":
        damaged.unlink()
    elif kind == "size":
        damaged.write_bytes(b"short")
    elif kind == "same_size_hash":
        damaged.write_bytes(b"z" * damaged.stat().st_size)
    else:
        local = tmp_path / "absent-local-root"
        damaged = local / rows[0]["path"]
    before = damaged.read_bytes() if damaged.exists() else None
    result = mod.fetch_verified_package(index, gen, rows, tmp_path / "stage", fetch,
                                        verified_local_root=local)
    assert result.get("reused_local") is not True
    assert len(calls) > 1 and "package_path" in result
    assert (damaged.read_bytes() if damaged.exists() else None) == before
    if kind == "root_missing":
        assert not local.exists()


@pytest.mark.parametrize("field", ["source_git_rev", "collection_epoch_id", "tile_registry_signature"])
def test_local_reuse_cannot_bypass_current_generation(tmp_path, field):
    index, gen, rows, fetch, calls = fixture(tmp_path)
    local = _local_members(tmp_path, rows)
    gen[field] = "other-generation"
    with pytest.raises(mod.BundleClientError, match="DESCRIPTOR_GENERATION_MISMATCH"):
        mod.fetch_verified_package(index, gen, rows, tmp_path / "stage", fetch,
                                   verified_local_root=local)
    assert len(calls) == 1
    assert not (tmp_path / "stage").exists()


def test_local_reuse_cannot_bypass_original_manifest_or_descriptor(tmp_path):
    index, gen, rows, fetch, calls = fixture(tmp_path)
    local = _local_members(tmp_path, rows)
    rows[0]["inode"] += 1
    with pytest.raises(mod.BundleClientError, match="MANIFEST_MEMBER_MISMATCH"):
        mod.fetch_verified_package(index, gen, rows, tmp_path / "stage", fetch,
                                   verified_local_root=local)
    assert len(calls) == 1
    rows[0]["inode"] -= 1
    index["descriptor_sha256"] = "0" * 64
    with pytest.raises(mod.BundleClientError, match="DESCRIPTOR_HASH_MISMATCH"):
        mod.fetch_verified_package(index, gen, rows, tmp_path / "stage", fetch,
                                   verified_local_root=local)
    assert len(calls) == 2


def test_local_reuse_rejects_root_reparse_without_reading_members(tmp_path, monkeypatch):
    from types import SimpleNamespace
    index, gen, rows, fetch, calls = fixture(tmp_path)
    local = _local_members(tmp_path, rows)
    original = Path.lstat
    def reparse(path, *args, **kwargs):
        actual = original(path, *args, **kwargs)
        if path == local:
            return SimpleNamespace(st_mode=actual.st_mode, st_file_attributes=0x400)
        return actual
    monkeypatch.setattr(Path, "lstat", reparse)
    with pytest.raises(mod.BundleClientError, match="LOCAL_REUSE_LINK_REJECTED"):
        mod.fetch_verified_package(index, gen, rows, tmp_path / "stage", fetch,
                                   verified_local_root=local)
    assert len(calls) == 1 and not (tmp_path / "stage").exists()


def test_local_reuse_rejects_member_symlink_metadata(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import stat
    index, gen, rows, fetch, calls = fixture(tmp_path)
    local = _local_members(tmp_path, rows)
    member = local / rows[0]["path"]
    original = Path.lstat
    def linked(path, *args, **kwargs):
        if path == member:
            return SimpleNamespace(st_mode=stat.S_IFLNK, st_file_attributes=0)
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "lstat", linked)
    with pytest.raises(mod.BundleClientError, match="LOCAL_REUSE_LINK_REJECTED"):
        mod.fetch_verified_package(index, gen, rows, tmp_path / "stage", fetch,
                                   verified_local_root=local)
    assert len(calls) == 1


def test_local_reuse_detects_early_member_changed_while_later_members_verified(tmp_path):
    index, gen, rows, fetch, _ = fixture(tmp_path)
    local = _local_members(tmp_path, rows)
    normal = mod.fetch_verified_package(index, gen, rows, tmp_path / "stage", fetch)
    selected = {row["path"]: row for row in normal["descriptor"]["members"]}
    checks = [0]
    def change_early():
        checks[0] += 1
        if checks[0] == 3:  # second member begins after first was hashed
            (local / rows[0]["path"]).write_bytes(b"z" * rows[0]["size"])
    assert mod._verified_local_members(local, selected, change_early) is None


def test_local_reuse_honors_deadline_without_mutating_evidence(tmp_path):
    index, gen, rows, fetch, _ = fixture(tmp_path)
    local = _local_members(tmp_path, rows)
    normal = mod.fetch_verified_package(index, gen, rows, tmp_path / "stage", fetch)
    selected = {row["path"]: row for row in normal["descriptor"]["members"]}
    def expired():
        raise mod.BundleClientError("PACKAGE_DEADLINE_EXCEEDED")
    with pytest.raises(mod.BundleClientError, match="PACKAGE_DEADLINE_EXCEEDED"):
        mod._verified_local_members(local, selected, expired)
    assert (local / rows[0]["path"]).read_bytes() == (tmp_path / "src" / rows[0]["path"]).read_bytes()


@pytest.mark.parametrize("field", ["source_git_rev", "collection_epoch_id", "tile_registry_signature"])
def test_full_identity_mismatch_before_package_download(tmp_path, field):
    index, gen, rows, fetch, calls = fixture(tmp_path)
    gen[field] = "other"
    with pytest.raises(mod.BundleClientError, match="DESCRIPTOR_GENERATION_MISMATCH"):
        mod.fetch_verified_package(index, gen, rows, tmp_path / "stage", fetch)
    assert len(calls) == 1


@pytest.mark.parametrize("field", ["size", "inode", "mtime_ns", "consistency_mode"])
def test_original_manifest_identity_mismatch_before_download(tmp_path, field):
    index, gen, rows, fetch, calls = fixture(tmp_path)
    rows[0][field] = "append" if field == "consistency_mode" else rows[0][field] + 1
    with pytest.raises(mod.BundleClientError, match="MANIFEST_MEMBER_MISMATCH"):
        mod.fetch_verified_package(index, gen, rows, tmp_path / "stage", fetch)
    assert len(calls) == 1


@pytest.mark.parametrize("kind", ["missing", "duplicate", "boolean"])
def test_manifest_missing_duplicate_and_bool_not_integer(tmp_path, kind):
    index, gen, rows, fetch, _ = fixture(tmp_path)
    if kind == "missing": rows.pop()
    elif kind == "duplicate": rows.append(dict(rows[0]))
    else: rows[0]["inode"] = True
    with pytest.raises(mod.BundleClientError, match="MANIFEST_MEMBER"):
        mod.fetch_verified_package(index, gen, rows, tmp_path / "stage", fetch)


def test_descriptor_hash_tamper_not_retried(tmp_path):
    index, gen, rows, fetch, calls = fixture(tmp_path)
    index["descriptor_sha256"] = "0" * 64
    with pytest.raises(mod.BundleClientError, match="DESCRIPTOR_HASH_MISMATCH"):
        mod.fetch_verified_package(index, gen, rows, tmp_path / "stage", fetch)
    assert len(calls) == 1


@pytest.mark.parametrize("key,value", [
    ("X-Chunk-Offset", "123"), ("X-Chunk-Sha256", "0"*64),
    ("X-Inventory-Generation", "b"*64), ("X-Package-Size", "12"),
    ("X-Package-Sha256", "f"*64), ("X-Chunk-EOF", "false"),
])
def test_bad_chunk_fence_cleans_only_own_partial(tmp_path, key, value):
    index, gen, rows, fetch, calls = fixture(tmp_path)
    stage = tmp_path / "stage"
    stage.mkdir()
    sentinel = stage / "preserved.txt"
    sentinel.write_text("keep")
    def corrupt(url, **kwargs):
        status, headers, body = fetch(url, **kwargs)
        if "descriptor=1" not in url: headers[key] = value
        return status, headers, body
    with pytest.raises(mod.BundleClientError, match="PACKAGE_CHUNK_FENCE_MISMATCH"):
        mod.fetch_verified_package(index, gen, rows, stage, corrupt)
    assert list(stage.iterdir()) == [sentinel]
    assert len(calls) == 2


def test_repeated_first_chunk_fails_without_loop(tmp_path):
    index, gen, rows, fetch, calls = fixture(tmp_path, large=True)
    first = None
    def repeat(url, **kwargs):
        nonlocal first
        response = fetch(url, **kwargs)
        if "offset=0&" in url: first = response
        elif "offset=" in url: return first
        return response
    with pytest.raises(mod.BundleClientError, match="HTTP_BODY_LIMIT_OR_TYPE|PACKAGE_CHUNK_FENCE_MISMATCH"):
        mod.fetch_verified_package(index, gen, rows, tmp_path / "stage", repeat)
    assert len(calls) == 3 and list((tmp_path / "stage").iterdir()) == []


def test_body_tamper_even_with_recomputed_chunk_sha_fails_package_sha(tmp_path):
    index, gen, rows, fetch, _ = fixture(tmp_path)
    def corrupt(url, **kwargs):
        status, headers, body = fetch(url, **kwargs)
        if "offset=" in url:
            body = b"z" + body[1:]
            headers["X-Chunk-Sha256"] = hashlib.sha256(body).hexdigest()
        return status, headers, body
    with pytest.raises(mod.BundleClientError, match="PACKAGE_SHA256_MISMATCH"):
        mod.fetch_verified_package(index, gen, rows, tmp_path / "stage", corrupt)


def test_transient_retry_bounded_and_auth_not_retried(tmp_path):
    index, gen, rows, fetch, _ = fixture(tmp_path)
    calls = []
    def transient(url, **kwargs):
        calls.append(url)
        return (503, {}, b"building") if len(calls) == 1 else fetch(url, **kwargs)
    sleeps = []
    mod.fetch_verified_package(index, gen, rows, tmp_path / "stage", transient, sleep=sleeps.append)
    assert sleeps == [0.25]
    calls.clear()
    def denied(url, **kwargs):
        calls.append(url)
        return 401, {}, b"denied"
    with pytest.raises(mod.BundleClientError, match="HTTP_401"):
        mod.fetch_verified_package(index, gen, rows, tmp_path / "stage", denied)
    assert len(calls) == 1


def test_timeouts_exhaust_retries_and_deadline_rejects_late_adapter(tmp_path):
    index, gen, rows, fetch, _ = fixture(tmp_path)
    attempts = []
    def timeout(url, **kwargs):
        attempts.append(url)
        raise TimeoutError()
    with pytest.raises(mod.BundleClientError, match="RETRY_EXHAUSTED"):
        mod.fetch_verified_package(index, gen, rows, tmp_path / "stage", timeout, sleep=lambda _x: None)
    assert len(attempts) == 3
    now = [0.0]
    def late(url, **kwargs):
        result = fetch(url, **kwargs)
        now[0] = 121
        return result
    with pytest.raises(mod.BundleClientError, match="DEADLINE_EXCEEDED"):
        mod.fetch_verified_package(index, gen, rows, tmp_path / "stage", late, clock=lambda: now[0])


def test_descriptor_duplicate_keys_rejected(tmp_path):
    index, gen, rows, _, _ = fixture(tmp_path)
    with pytest.raises(mod.BundleClientError, match="DESCRIPTOR_JSON_INVALID"):
        mod.fetch_verified_package(index, gen, rows, tmp_path / "stage",
                                   lambda *a, **k: (200, {}, b'{"a":1,"a":1}'))


def test_non_ack_generation_no_fetch(tmp_path):
    index, gen, rows, _, _ = fixture(tmp_path)
    gen["ack_eligible"] = False
    with pytest.raises(mod.BundleClientError, match="MANIFEST_GENERATION_INVALID"):
        mod.fetch_verified_package(index, gen, rows, tmp_path / "stage",
                                   lambda *a, **k: pytest.fail("network called"))


def test_staging_reparse_attribute_rejected(tmp_path, monkeypatch):
    index, gen, rows, fetch, _ = fixture(tmp_path)
    stage = tmp_path / "stage"
    stage.mkdir()
    original = Path.lstat
    def lstat(path):
        if path == stage:
            class Stat: st_file_attributes = 0x400
            return Stat()
        return original(path)
    monkeypatch.setattr(Path, "lstat", lstat)
    with pytest.raises(mod.BundleClientError, match="STAGING_LINK_REJECTED"):
        mod.fetch_verified_package(index, gen, rows, stage, fetch)


def test_preindexed_manifest_only_looks_up_selected_members(tmp_path):
    index, gen, rows, fetch, _ = fixture(tmp_path)
    class Indexed(dict):
        def __iter__(self): pytest.fail("indexed manifest scanned")
        def items(self): pytest.fail("indexed manifest scanned")
        def values(self): pytest.fail("indexed manifest scanned")
    lookup = Indexed({row["path"]: row for row in rows})
    lookup["unrelated"] = {"path": "unrelated"}
    result = mod.fetch_verified_package(index, gen, lookup, tmp_path / "stage", fetch)
    assert len(result["members"]) == 2


def test_indexed_manifest_key_cannot_alias_other_path(tmp_path):
    index, gen, rows, fetch, _ = fixture(tmp_path)
    lookup = {row["path"]: row for row in rows}
    lookup[rows[0]["path"]] = rows[1]
    with pytest.raises(mod.BundleClientError, match="KEY_MISMATCH"):
        mod.fetch_verified_package(index, gen, lookup, tmp_path / "stage", fetch)
