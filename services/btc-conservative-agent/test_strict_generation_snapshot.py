import ast
import hashlib
import os
import re
import time
import uuid
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
BOT = (HERE / "bot.py").read_text(encoding="utf-8")
GEN = "a" * 64


def _load():
    tree = ast.parse(BOT)
    wanted = {"_data_sync_generation_matches", "_data_sync_strict_snapshot",
              "_DATA_SYNC_STRICT_SNAPSHOT_MAX_BYTES", "_DATA_SYNC_STRICT_SNAPSHOT_TTL_SEC"}
    selected = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in wanted:
            selected.append(node)
        elif isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id in wanted for t in node.targets):
            selected.append(node)
    namespace = {"Path": Path, "hashlib": hashlib, "os": os, "re": re, "time": time, "uuid": uuid}
    exec(compile(ast.Module(body=selected, type_ignores=[]), "bot.py", "exec"), namespace)
    return namespace


@pytest.fixture()
def ns():
    return _load()


def _identity(path):
    st = path.stat()
    return st.st_size, st.st_mtime_ns, int(getattr(st, "st_ino", 0) or 0)


def test_snapshot_keeps_manifest_bytes_after_live_append(tmp_path, ns):
    source = tmp_path / "lifecycle_genome.jsonl"
    original = b'{"row":1}\n' * 5000
    source.write_bytes(original)
    size, mtime, inode = _identity(source)
    root = tmp_path / "strict-snapshots"
    first = ns["_data_sync_strict_snapshot"](source, root, GEN, "research/genome/lifecycle_genome.jsonl", size, mtime, inode)
    assert first is not None and first.read_bytes() == original
    with source.open("ab") as handle:
        handle.write(b'{"row":2}\n')
    again = ns["_data_sync_strict_snapshot"](source, root, GEN, "research/genome/lifecycle_genome.jsonl", size, mtime, inode)
    assert again == first and again.read_bytes() == original
    assert hashlib.sha256(again.read_bytes()).hexdigest() == hashlib.sha256(original).hexdigest()


def test_snapshot_refuses_changed_generation(tmp_path, ns):
    source = tmp_path / "ledger.jsonl"
    source.write_bytes(b"x" * 100)
    size, mtime, inode = _identity(source)
    with source.open("ab") as handle:
        handle.write(b"y")
    root = tmp_path / "strict-snapshots"
    assert ns["_data_sync_strict_snapshot"](source, root, GEN, "ledger.jsonl", size, mtime, inode) is None
    assert not root.exists() or not any(root.rglob("*.bin"))


@pytest.mark.parametrize("generation,size_delta", [("not-a-generation", 0), ("A" * 64, 0), (GEN, None)])
def test_snapshot_rejects_invalid_generation_or_size(tmp_path, ns, generation, size_delta):
    source = tmp_path / "doc.json"
    source.write_bytes(b"{}")
    size, mtime, inode = _identity(source)
    if size_delta is None:
        size = ns["_DATA_SYNC_STRICT_SNAPSHOT_MAX_BYTES"] + 1
    assert ns["_data_sync_strict_snapshot"](source, tmp_path / "s", generation, "doc.json", size, mtime, inode) is None


def test_snapshot_identity_is_part_of_the_key(tmp_path, ns):
    source = tmp_path / "doc.jsonl"
    source.write_bytes(b"one\n")
    first = ns["_data_sync_strict_snapshot"](source, tmp_path / "s", GEN, "doc.jsonl", *_identity(source))
    source.write_bytes(b"two!\n")
    second = ns["_data_sync_strict_snapshot"](source, tmp_path / "s", GEN, "doc.jsonl", *_identity(source))
    assert first != second
    assert first.read_bytes() == b"one\n" and second.read_bytes() == b"two!\n"


def test_stale_other_generation_snapshots_are_pruned(tmp_path, ns):
    root = tmp_path / "strict-snapshots"
    stale = root / ("b" * 64)
    stale.mkdir(parents=True)
    (stale / "old.bin").write_bytes(b"old")
    old = time.time() - ns["_DATA_SYNC_STRICT_SNAPSHOT_TTL_SEC"] - 60
    os.utime(stale, (old, old))
    fresh = root / ("c" * 64)
    fresh.mkdir()
    source = tmp_path / "doc.jsonl"
    source.write_bytes(b"data\n")
    assert ns["_data_sync_strict_snapshot"](source, root, GEN, "doc.jsonl", *_identity(source)) is not None
    assert not stale.exists() and fresh.exists()


def _strict_branch():
    start = BOT.index("def api_data_sync_file():")
    end = BOT.index("def _data_sync_ack_v3_identity_matches", start)
    body = BOT[start:end]
    return body[body.index("elif None not in (expected_size, expected_mtime, expected_inode):"):]


def test_endpoint_serves_multichunk_strict_reads_from_snapshot_before_fencing():
    branch = _strict_branch()
    gate = branch.index("if offset > 0 or size > limit:")
    create = branch.index("_data_sync_strict_snapshot(")
    serve = branch.index("with snapshot.open(\"rb\") as handle:")
    register = branch.index("_data_sync_register_served_ack_generation(ack_inventory_sha256, relpath, size, mtime_ns)")
    fence = branch.index('return jsonify({"error": "file generation changed after manifest"}), 409')
    assert gate < create < serve < register < fence
    assert 'response.headers["X-Data-Size"] = str(size)' in branch[serve:fence]
    assert 'response.headers["X-Chunk-Sha256"]' in branch[serve:fence]
    assert '"strict-snapshots"' in branch[create - 200:create + 200]
