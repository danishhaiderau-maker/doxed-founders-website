"""Shadow-mode sealed-segment transfer: shipper, puller, store and parity."""

from __future__ import annotations

import ast
import hashlib
import http.server
import json
import os
import threading
from pathlib import Path

import pytest

import research_segment_format as fmt
import research_segment_parity as parity
import research_segment_puller as puller_mod
import research_segment_shipper as shipper_mod
from research_segment_store import LocalDirectoryStore, PreconditionFailed, S3Store

ROOT = Path(__file__).resolve().parent
RULES = {
    "extensions": frozenset({".jsonl", ".json", ".csv", ".log", ".db", ".sqlite3", ".txt"}),
    "excluded_names": frozenset({"bot.log", "manifest.json"}),
    "excluded_dir_names": frozenset({".locks", "research_archive"}),
}


class Env:
    def __init__(self, tmp_path: Path, **shipper_kwargs):
        self.volume = tmp_path / "volume"
        self.runtime = self.volume / "runtime"
        self.runtime.mkdir(parents=True)
        self.store_root = tmp_path / "bucket"
        self.store = LocalDirectoryStore(self.store_root)
        self.shadow = tmp_path / "laptop" / "fly-mirror-segments"
        self.archive = tmp_path / "laptop" / "fly-segments"
        self.state_dir = self.volume / "segment-shipper"
        self.kwargs = shipper_kwargs
        self.clock = [1000.0]

    def shipper(self) -> shipper_mod.SegmentShipper:
        return shipper_mod.SegmentShipper(
            store=self.store, volume_root=self.volume, runtime_root=self.runtime,
            state_dir=self.state_dir, rules=RULES, source_git_rev="abc123def456",
            clock=lambda: self.clock[0], **self.kwargs,
        )

    def puller(self, **kwargs) -> puller_mod.SegmentPuller:
        return puller_mod.SegmentPuller(store=self.store, shadow_root=self.shadow,
                                        archive_root=self.archive, **kwargs)

    def write(self, relpath: str, raw: bytes, append: bool = False) -> Path:
        path = self.runtime / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("ab" if append else "wb") as handle:
            handle.write(raw)
        return path

    def ship_all(self) -> list[dict]:
        results = []
        while True:
            result = self.shipper().cycle()
            results.append(result)
            if not result["shipped"] and not result["deferred_bytes"]:
                return results
            if not result["shipped"]:
                raise AssertionError("shipper deferred bytes without shipping")

    def assert_tree_matches_source(self) -> None:
        tree = self.shadow / "tree"
        shipper = self.shipper()
        source = {rel: path.read_bytes() for rel, (path, _stat) in shipper.scan().items()}
        mirrored = {
            path.relative_to(tree).as_posix(): path.read_bytes()
            for path in tree.rglob("*") if path.is_file()
        }
        assert mirrored == source


def _rows(start: int, count: int, tag: str = "r") -> bytes:
    return b"".join(json.dumps({"i": i, "tag": tag}).encode() + b"\n" for i in range(start, start + count))


# --------------------------------------------------------------- end to end
def test_genesis_append_snapshot_round_trip_and_ack(tmp_path):
    env = Env(tmp_path)
    env.write("trade_lifecycle.jsonl", _rows(0, 5))
    env.write("state/paper_lifecycle_v1.json", b'{"open": []}')
    env.write("bot.log", b"excluded\n")
    env.write(".locks/x.json", b"{}")
    env.ship_all()
    result = env.puller().pull_once()
    assert result == {"applied_now": 1, "applied_seq": 1, "acked_seq": 1}
    env.assert_tree_matches_source()
    assert not (env.shadow / "tree" / "bot.log").exists()
    ack = fmt.parse_ack(env.store.get(fmt.ack_key("v1", 1)))
    assert ack["through_seq"] == 1

    env.write("trade_lifecycle.jsonl", _rows(5, 3), append=True)
    env.write("trade_lifecycle.jsonl", b'{"partial": ', append=True)
    env.ship_all()
    manifest = json.loads(env.store.get(fmt.manifest_key("v1", 2)))
    [member] = manifest["members"]
    assert member["kind"] == "APPEND" and member["base_offset"] == len(_rows(0, 5))
    assert manifest["source_git_rev"] == "abc123def456"
    env.puller().pull_once()
    tree_copy = (env.shadow / "tree" / "trade_lifecycle.jsonl").read_bytes()
    assert tree_copy == _rows(0, 5) + _rows(5, 3)  # partial record never shipped
    assert fmt.parse_ack(env.store.get(fmt.ack_key("v1", 2)))["through_seq"] == 2


def test_manifest_records_epoch_and_chain(tmp_path):
    env = Env(tmp_path)
    env.write("research_session.json", json.dumps({"collector_v22_epoch_id": "epoch-abc"}).encode())
    env.write("a.jsonl", _rows(0, 2))
    env.ship_all()
    env.write("a.jsonl", _rows(2, 2), append=True)
    env.ship_all()
    first = env.store.get(fmt.manifest_key("v1", 1))
    second = json.loads(env.store.get(fmt.manifest_key("v1", 2)))
    assert json.loads(first)["prev_manifest_sha256"] == fmt.GENESIS_PREV_SHA256
    assert second["prev_manifest_sha256"] == hashlib.sha256(first).hexdigest()
    assert second["collection_epoch_id"] == "epoch-abc"


# ------------------------------------------------------- chain gaps / reorder
def test_chain_gap_stops_without_skipping(tmp_path):
    env = Env(tmp_path)
    for index in range(3):
        env.write("a.jsonl", _rows(index * 2, 2), append=True)
        env.ship_all()
    (env.store_root / fmt.manifest_key("v1", 2)).unlink()
    result = env.puller().pull_once()
    assert result["applied_seq"] == 1 and result["acked_seq"] == 1
    assert not env.store.get(fmt.ack_key("v1", 3))


def test_reordered_manifest_is_rejected(tmp_path):
    env = Env(tmp_path)
    for index in range(3):
        env.write("a.jsonl", _rows(index * 2, 2), append=True)
        env.ship_all()
    two = env.store_root / fmt.manifest_key("v1", 2)
    three = env.store_root / fmt.manifest_key("v1", 3)
    raw_two, raw_three = two.read_bytes(), three.read_bytes()
    two.write_bytes(raw_three)
    three.write_bytes(raw_two)
    with pytest.raises(fmt.SegmentFormatError, match="gap or reorder"):
        env.puller().pull_once()
    assert env.puller().load_state()["applied_seq"] == 1


def test_chain_break_and_tampered_segment_are_rejected(tmp_path):
    env = Env(tmp_path)
    env.write("a.jsonl", _rows(0, 2))
    env.ship_all()
    env.write("a.jsonl", _rows(2, 2), append=True)
    env.ship_all()
    env.puller().pull_once(max_segments=1)
    path = env.store_root / fmt.manifest_key("v1", 2)
    manifest = json.loads(path.read_bytes())
    manifest["prev_manifest_sha256"] = "f" * 64
    path.write_bytes(fmt.canonical_json(manifest))
    with pytest.raises(fmt.SegmentFormatError, match="chain break"):
        env.puller().pull_once()

    other = Env(tmp_path / "other")
    other.write("a.jsonl", _rows(0, 2))
    other.ship_all()
    segment = other.store_root / fmt.segment_key("v1", 1)
    segment.write_bytes(segment.read_bytes() + b"x")
    with pytest.raises(fmt.SegmentFormatError, match="size/sha256"):
        other.puller().pull_once()


# ------------------------------------------------------ deterministic rebuild
def test_segment_and_manifest_rebuild_is_byte_identical(tmp_path):
    payloads = [b"alpha\n", b"", b"\x00\x01" * 1000]
    assert fmt.build_segment(payloads) == fmt.build_segment(list(payloads))
    env = Env(tmp_path)
    env.write("a.jsonl", _rows(0, 20))
    env.write("s.json", b'{"k": 1}')
    shipper = env.shipper()
    state = shipper.load_state()
    selected, _ = shipper.select(shipper.plan(state, shipper.scan()))
    first = shipper.build(state, selected)
    env.clock[0] += 3600  # wall clock must not leak into the sealed bytes
    selected_again, _ = shipper.select(shipper.plan(state, shipper.scan()))
    second = shipper.build(state, selected_again)
    assert first[0] == second[0] and first[1] == second[1]


# --------------------------------------------------- conditional-write conflict
def test_local_store_is_write_once(tmp_path):
    store = LocalDirectoryStore(tmp_path)
    body = b"x"
    store.put_if_absent("k/a", body, sha256=hashlib.sha256(body).hexdigest(), content_type="t")
    with pytest.raises(PreconditionFailed):
        store.put_if_absent("k/a", body, sha256=hashlib.sha256(body).hexdigest(), content_type="t")


def test_conflicting_existing_segment_fails_closed(tmp_path):
    env = Env(tmp_path)
    env.write("a.jsonl", _rows(0, 2))
    squatter = b"not our segment"
    env.store.put_if_absent(fmt.segment_key("v1", 1), squatter,
                            sha256=hashlib.sha256(squatter).hexdigest(), content_type="x")
    with pytest.raises(shipper_mod.ShipperConflict):
        env.shipper().cycle()
    assert env.shipper().load_state()["seq"] == 0
    assert env.store.get(fmt.segment_key("v1", 1)) == squatter
    assert env.store.get(fmt.manifest_key("v1", 1)) is None


def test_identical_existing_segment_is_idempotent_success(tmp_path):
    env = Env(tmp_path)
    env.write("a.jsonl", _rows(0, 2))
    shipper = env.shipper()
    state = shipper.load_state()
    selected, _ = shipper.select(shipper.plan(state, shipper.scan()))
    segment_raw, _manifest, _state = shipper.build(state, selected)
    env.store.put_if_absent(fmt.segment_key("v1", 1), segment_raw,
                            sha256=hashlib.sha256(segment_raw).hexdigest(), content_type="x")
    result = env.shipper().cycle()
    assert result["shipped"]["segment"] == "ALREADY_PRESENT"
    assert result["shipped"]["manifest"] == "CREATED"


# ----------------------------------------------------------------- rotation
def test_rotation_seals_tail_and_restarts_active(tmp_path):
    env = Env(tmp_path)
    active = env.write("market_microstructure_1s.jsonl", _rows(0, 10))
    env.ship_all()
    env.puller().pull_once()
    env.write("market_microstructure_1s.jsonl", _rows(10, 4), append=True)
    os.rename(active, active.with_name("market_microstructure_1s.jsonl.1"))
    env.write("market_microstructure_1s.jsonl", _rows(100, 3))
    env.ship_all()
    manifest = json.loads(env.store.get(fmt.manifest_key("v1", 2)))
    kinds = [(m["kind"], m["path"]) for m in manifest["members"]]
    assert kinds == [("SEAL", "market_microstructure_1s.jsonl.1"),
                     ("APPEND", "market_microstructure_1s.jsonl")]
    seal = manifest["members"][0]
    assert seal["base_offset"] == len(_rows(0, 10))
    assert seal["final_sha256"] == hashlib.sha256(_rows(0, 14)).hexdigest()
    env.puller().pull_once()
    env.assert_tree_matches_source()
    env.ship_all()
    assert env.store.get(fmt.manifest_key("v1", 3)) is None  # sealed file not re-shipped


def test_existing_rotations_at_genesis_ship_as_snapshots(tmp_path):
    env = Env(tmp_path)
    env.write("x.jsonl.1", _rows(0, 3))
    env.write("x.jsonl", _rows(3, 1))
    env.ship_all()
    kinds = {m["path"]: m["kind"] for m in json.loads(env.store.get(fmt.manifest_key("v1", 1)))["members"]}
    assert kinds == {"x.jsonl": "APPEND", "x.jsonl.1": "SNAPSHOT"}
    env.puller().pull_once()
    env.assert_tree_matches_source()


# --------------------------------------------------------- non-append rewrite
def test_shrink_and_same_size_rewrite_ship_full_replacement(tmp_path):
    env = Env(tmp_path)
    path = env.write("lifecycle.jsonl", _rows(0, 10))
    env.ship_all()
    env.puller().pull_once()
    path.write_bytes(_rows(0, 4, tag="repaired"))  # tail repair shrinks the file
    env.ship_all()
    member = json.loads(env.store.get(fmt.manifest_key("v1", 2)))["members"][0]
    assert member["kind"] == "REWRITE" and member["generation"] == 1
    env.puller().pull_once()
    env.assert_tree_matches_source()
    quarantined = env.shadow / "quarantine" / fmt.seq_token(2) / "lifecycle.jsonl"
    assert quarantined.read_bytes() == _rows(0, 10)

    original = path.read_bytes()
    stat = path.stat()
    path.write_bytes(original.replace(b"repaired", b"REPAIRED"))  # same size, new prefix
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 5_000_000_000))
    env.write("lifecycle.jsonl", _rows(50, 1), append=True)
    env.ship_all()
    member = json.loads(env.store.get(fmt.manifest_key("v1", 3)))["members"][0]
    assert member["kind"] == "REWRITE" and member["generation"] == 2
    env.puller().pull_once()
    env.assert_tree_matches_source()


def test_vanished_file_is_tombstoned_never_deleted_on_laptop(tmp_path):
    env = Env(tmp_path)
    path = env.write("gone.jsonl", _rows(0, 2))
    env.ship_all()
    env.puller().pull_once()
    path.unlink()
    env.ship_all()
    member = json.loads(env.store.get(fmt.manifest_key("v1", 2)))["members"][0]
    assert member["kind"] == "TOMBSTONE" and member["size"] == 0
    env.puller().pull_once()
    assert (env.shadow / "tree" / "gone.jsonl").read_bytes() == _rows(0, 2)
    assert list((env.shadow / "tombstones").glob("*.json"))
    env.write("gone.jsonl", _rows(9, 1))
    env.ship_all()
    member = json.loads(env.store.get(fmt.manifest_key("v1", 3)))["members"][0]
    assert member["kind"] == "REWRITE"
    env.puller().pull_once()
    assert (env.shadow / "tree" / "gone.jsonl").read_bytes() == _rows(9, 1)


# ------------------------------------------- crash between upload and commit
def test_crash_between_upload_and_checkpoint_recovers_exactly_once(tmp_path, monkeypatch):
    env = Env(tmp_path)
    env.write("a.jsonl", _rows(0, 3))
    real_write = shipper_mod._atomic_write

    def crash_on_checkpoint(path, raw):
        if Path(path).name == "state.json":
            raise SystemExit("simulated crash after upload")
        return real_write(path, raw)

    monkeypatch.setattr(shipper_mod, "_atomic_write", crash_on_checkpoint)
    with pytest.raises(SystemExit):
        env.shipper().cycle()
    monkeypatch.setattr(shipper_mod, "_atomic_write", real_write)
    assert env.store.get(fmt.segment_key("v1", 1)) is not None
    assert env.shipper().load_state()["seq"] == 0
    assert (env.state_dir / "intent" / "intent.json").is_file()

    env.write("a.jsonl", _rows(3, 2), append=True)  # new data arrives meanwhile
    result = env.shipper().cycle()
    assert result["recovered"] == {"seq": 1, "segment": "ALREADY_PRESENT",
                                   "manifest": "ALREADY_PRESENT",
                                   "segment_bytes": result["recovered"]["segment_bytes"]}
    assert result["shipped"]["seq"] == 2
    assert not (env.state_dir / "intent" / "intent.json").exists()
    env.puller().pull_once()
    env.assert_tree_matches_source()


def test_crash_before_intent_uploads_nothing(tmp_path, monkeypatch):
    env = Env(tmp_path)
    env.write("a.jsonl", _rows(0, 3))

    def crash(*_args, **_kwargs):
        raise SystemExit("crash while writing intent")

    monkeypatch.setattr(shipper_mod.SegmentShipper, "write_intent", crash)
    with pytest.raises(SystemExit):
        env.shipper().cycle()
    assert env.store.list_keys("v1/") == []


# ------------------------------------------------------------ laptop resume
def test_laptop_resume_after_partial_apply_and_sleep(tmp_path):
    env = Env(tmp_path)
    env.write("a.jsonl", _rows(0, 5))
    env.write("b.json", b'{"v": 1}')
    env.ship_all()
    env.write("a.jsonl", _rows(5, 5), append=True)
    env.ship_all()
    env.write("a.jsonl", _rows(10, 5), append=True)
    env.ship_all()
    first = env.puller().pull_once(max_segments=1)
    assert first["applied_seq"] == 1 and first["acked_seq"] == 1

    # Simulate sleep mid-segment 2: half of the append landed, state not saved.
    manifest = json.loads(env.store.get(fmt.manifest_key("v1", 2)))
    member = manifest["members"][0]
    with (env.shadow / "tree" / "a.jsonl").open("ab") as handle:
        handle.write(_rows(5, 5)[:17])
    assert member["base_offset"] == len(_rows(0, 5))

    resumed = env.puller().pull_once()
    assert resumed == {"applied_now": 2, "applied_seq": 3, "acked_seq": 3}
    env.assert_tree_matches_source()
    again = env.puller().pull_once()
    assert again == {"applied_now": 0, "applied_seq": 3, "acked_seq": 3}
    ack_keys = env.store.list_keys(fmt.ack_prefix("v1"))
    assert [key.rsplit("/", 1)[-1] for key in ack_keys] == [
        f"{fmt.seq_token(1)}.json", f"{fmt.seq_token(3)}.json"]
    assert len(list((env.archive / "v1" / "seg").glob("*.tar.gz"))) == 3


def test_resume_after_crash_between_seal_append_and_rename(tmp_path, monkeypatch):
    env = Env(tmp_path)
    active = env.write("t.jsonl", _rows(0, 4))
    env.ship_all()
    env.puller().pull_once()
    env.write("t.jsonl", _rows(4, 2), append=True)
    os.rename(active, active.with_name("t.jsonl.1"))
    env.write("t.jsonl", _rows(7, 1))
    env.ship_all()
    real_replace = os.replace
    calls = {"n": 0}

    def crash_on_seal_rename(src, dst):
        if str(dst).endswith("t.jsonl.1") and calls["n"] == 0:
            calls["n"] += 1
            raise SystemExit("sleep during seal rename")
        return real_replace(src, dst)

    monkeypatch.setattr(puller_mod.os, "replace", crash_on_seal_rename)
    with pytest.raises(SystemExit):
        env.puller().pull_once()
    monkeypatch.setattr(puller_mod.os, "replace", real_replace)
    env.puller().pull_once()
    env.assert_tree_matches_source()


def test_existing_ack_disagreement_fails_closed(tmp_path):
    env = Env(tmp_path)
    env.write("a.jsonl", _rows(0, 2))
    env.ship_all()
    bogus = fmt.build_ack(through_seq=1, manifest_sha256="e" * 64, applied_at="x", verifier_version="y")
    env.store.put_if_absent(fmt.ack_key("v1", 1), bogus, sha256=hashlib.sha256(bogus).hexdigest(),
                            content_type="application/json")
    with pytest.raises(puller_mod.PullerError, match="disagrees"):
        env.puller().pull_once()


def test_shipper_reads_laptop_ack_without_pruning(tmp_path):
    env = Env(tmp_path)
    source = env.write("a.jsonl", _rows(0, 2))
    env.ship_all()
    env.puller().pull_once()
    assert env.shipper().poll_laptop_ack() == 1
    status = json.loads((env.state_dir / "status.json").read_text())
    assert status["laptop_acked_seq"] == 1 and status["pruning_enabled"] is False
    assert source.read_bytes() == _rows(0, 2)


# ---------------------------------------------------------- budget / backlog
def test_large_backlog_is_split_across_segments_at_record_boundaries(tmp_path):
    env = Env(tmp_path, max_segment_bytes=300)
    env.write("big.jsonl", _rows(0, 40))
    env.write("small.json", b"{}")
    results = env.ship_all()
    shipped = [r for r in results if r["shipped"]]
    assert len(shipped) >= 3
    for seq in range(1, len(shipped) + 1):
        manifest = json.loads(env.store.get(fmt.manifest_key("v1", seq)))
        for member in manifest["members"]:
            if member["kind"] == "APPEND":
                assert member["size"] <= 300
    env.puller().pull_once()
    env.assert_tree_matches_source()


def test_oversized_member_blocks_only_its_own_stream(tmp_path):
    env = Env(tmp_path, max_member_bytes=64)
    env.write("huge.sqlite3", b"x" * 100)
    env.write("ok.json", b'{"fine": true}')
    result = env.shipper().cycle()
    manifest = json.loads(env.store.get(fmt.manifest_key("v1", 1)))
    assert [m["path"] for m in manifest["members"]] == ["ok.json"]
    status = json.loads((env.state_dir / "status.json").read_text())
    assert status["oversized_paths"] == ["huge.sqlite3"] and result["deferred_bytes"] == 100


def test_large_mutating_snapshot_is_throttled_per_interval(tmp_path):
    env = Env(tmp_path, large_snapshot_bytes=100, large_snapshot_interval=600)
    db = env.write("research.db", b"a" * 200)
    env.write("small.json", b"{}")
    env.ship_all()
    db.write_bytes(b"b" * 200)
    os.utime(db, ns=(db.stat().st_atime_ns, db.stat().st_mtime_ns + 1_000_000_000))
    env.write("small.json", b'{"v": 2}')
    env.ship_all()
    manifest = json.loads(env.store.get(fmt.manifest_key("v1", 2)))
    assert [m["path"] for m in manifest["members"]] == ["small.json"]
    status = json.loads((env.state_dir / "status.json").read_text())
    assert status["throttled_snapshots"] == ["research.db"]
    env.clock[0] += 601
    env.ship_all()
    manifest = json.loads(env.store.get(fmt.manifest_key("v1", 3)))
    assert [m["path"] for m in manifest["members"]] == ["research.db"]
    env.puller().pull_once()
    env.assert_tree_matches_source()


# ------------------------------------------------------------ safety surface
def test_shipper_has_no_bot_http_lock_or_prune_surface():
    source = (ROOT / "research_segment_shipper.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert not imported & {"bot", "flask", "http", "socketserver", "btc_conservative_agent",
                           "shutil", "requests", "ccxt"}
    assert "trade_lock" not in source
    assert "rmtree" not in source
    # Deletion lives only in research_segment_prune.execute, reached solely in enforce mode.
    assert source.count("prune.execute(") == 1


def test_shipper_disabled_by_default(monkeypatch, capsys):
    monkeypatch.delenv("RESEARCH_SEGMENTS_ENABLED", raising=False)
    assert shipper_mod.main() == 0
    assert "disabled" in capsys.readouterr().out


def test_entrypoint_launches_shipper_only_behind_flag():
    text = (ROOT / "fly-entrypoint.sh").read_text(encoding="utf-8")
    assert 'if [ "${RESEARCH_SEGMENTS_ENABLED:-0}" = "1" ]; then' in text
    assert "nice -n 10 python /app/research_segment_shipper.py" in text
    launch = text.index("research_segment_shipper.py")
    assert launch < text.index("starting btc_conservative_agent.py")


def test_selection_rules_are_owned_by_segment_code_not_bot_source():
    rules = shipper_mod.load_selection_rules()
    assert ".jsonl" in rules["extensions"] and ".sqlite3" in rules["extensions"]
    assert {"bot.log", "sync_inventory_current.json"} <= rules["excluded_names"]
    assert {".locks", "research_archive"} <= rules["excluded_dir_names"]
    shipper_source = (ROOT / "research_segment_shipper.py").read_text(encoding="utf-8")
    assert "bot.py" not in shipper_source.split('"""', 2)[2]
    assert "RESEARCH_SEGMENTS_BOT_SOURCE" not in shipper_source


def test_jsonl_validation_caches_are_never_shipped():
    rules = shipper_mod.load_selection_rules()
    shipper = shipper_mod.SegmentShipper.__new__(shipper_mod.SegmentShipper)
    shipper.rules = rules
    assert not shipper._allowed_name("market_microstructure_1s.jsonl.validation.json")
    assert shipper._allowed_name("market_microstructure_1s.jsonl")
    assert shipper._allowed_name("validation_summary.json")


def test_puller_refuses_onedrive_and_legacy_mirror(tmp_path):
    store = LocalDirectoryStore(tmp_path / "b")
    with pytest.raises(puller_mod.PullerError, match="OneDrive"):
        puller_mod.SegmentPuller(store=store, shadow_root=Path(r"C:\Users\x\OneDrive\m"),
                                 archive_root=tmp_path / "a")
    with pytest.raises(puller_mod.PullerError, match="legacy mirror"):
        puller_mod.SegmentPuller(store=store, shadow_root=tmp_path / "canonical-research-data",
                                 archive_root=tmp_path / "a")


def test_manifest_rejects_path_escape():
    member = {"index": 0, "kind": "SNAPSHOT", "path": "../evil.json", "size": 0,
              "sha256": hashlib.sha256(b"").hexdigest()}
    with pytest.raises(fmt.SegmentFormatError):
        fmt.validate_member(member, 0)


# ------------------------------------------------------------------- parity
def test_parity_prefix_rules(tmp_path):
    shadow, legacy = tmp_path / "shadow", tmp_path / "legacy"
    for root in (shadow, legacy):
        (root / "v3").mkdir(parents=True)
    (shadow / "a.jsonl").write_bytes(_rows(0, 5))
    (legacy / "a.jsonl").write_bytes(_rows(0, 7))
    (shadow / "a.jsonl.1").write_bytes(b"sealed\n")
    (legacy / "a.jsonl.1").write_bytes(b"sealed\n")
    (shadow / "v3" / "s.json").write_bytes(b"{1}")
    (legacy / "v3" / "s.json").write_bytes(b"{2}")
    (legacy / "old.jsonl").write_bytes(b"history\n")
    (legacy / "x.jsonl.1.4812.abc.download").write_bytes(b"ignored")
    report = parity.compare(shadow, legacy)
    assert report["verdict"] == "GREEN"
    assert report["counts"]["prefix_match"] == 1 and report["counts"]["exact"] == 1
    assert report["counts"]["content_differs"] == 1 and report["counts"]["missing_in_shadow"] == 1

    (shadow / "a.jsonl").write_bytes(_rows(0, 5, tag="diverged"))
    (shadow / "a.jsonl.1").write_bytes(b"SEALED\n")
    report = parity.compare(shadow, legacy)
    assert report["verdict"] == "RED"
    assert report["counts"]["prefix_mismatch"] == 1 and report["counts"]["sealed_mismatch"] == 1


@pytest.mark.skipif(os.name != "nt", reason="Windows share-mode contract")
def test_parity_reader_allows_legacy_rename_and_delete(tmp_path):
    target = tmp_path / "mirror.jsonl"
    target.write_bytes(b"old\n")
    with parity.open_shared_read(target) as handle:
        os.replace(target, tmp_path / "quarantined.jsonl")
        assert handle.read() == b"old\n"
    doomed = tmp_path / "doomed.jsonl"
    doomed.write_bytes(b"x\n")
    with parity.open_shared_read(doomed):
        doomed.unlink()
    assert not doomed.exists()


# --------------------------------------------------------------- S3 protocol
class _FakeS3(http.server.BaseHTTPRequestHandler):
    objects: dict = {}
    seen: list = []

    def log_message(self, *_args):
        pass

    def do_PUT(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        self.seen.append(dict(self.headers.items()))
        if self.headers.get("If-None-Match") != "*":
            self.send_response(400); self.end_headers(); return
        if self.path in self.objects:
            self.send_response(412); self.end_headers(); return
        self.objects[self.path] = (body, self.headers.get("x-amz-meta-sha256"))
        self.send_response(200); self.end_headers()

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if "list-type=2" in self.path:
            keys = "".join(f"<Contents><Key>{k.split('/', 2)[2]}</Key></Contents>"
                           for k in sorted(self.objects) if "/acks/" in k)
            raw = ('<?xml version="1.0"?><ListBucketResult xmlns="http://s3.amazonaws.com/doc/'
                   f'2006-03-01/"><IsTruncated>false</IsTruncated>{keys}</ListBucketResult>').encode()
            self.send_response(200); self.send_header("Content-Length", str(len(raw)))
            self.end_headers(); self.wfile.write(raw); return
        if path not in self.objects:
            self.send_response(404); self.end_headers(); return
        body = self.objects[path][0]
        self.send_response(200); self.send_header("Content-Length", str(len(body)))
        self.end_headers(); self.wfile.write(body)

    def do_HEAD(self):
        if self.path not in self.objects:
            self.send_response(404); self.end_headers(); return
        self.send_response(200)
        self.send_header("x-amz-meta-sha256", self.objects[self.path][1])
        self.end_headers()


def test_s3_store_conditional_put_get_head_list():
    _FakeS3.objects, _FakeS3.seen = {}, []
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _FakeS3)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        store = S3Store(endpoint=f"http://127.0.0.1:{server.server_port}", bucket="bkt",
                        access_key_id="AKIDTEST", secret_access_key="never-logged", attempts=1)
        assert "never-logged" not in repr(store)
        body = b"segment-bytes"
        digest = hashlib.sha256(body).hexdigest()
        store.put_if_absent("v1/acks/laptop/000000000001.json", body, sha256=digest,
                            content_type="application/json")
        with pytest.raises(PreconditionFailed):
            store.put_if_absent("v1/acks/laptop/000000000001.json", body, sha256=digest,
                                content_type="application/json")
        headers = {k.lower(): v for k, v in _FakeS3.seen[0].items()}
        assert headers["authorization"].startswith("AWS4-HMAC-SHA256 Credential=AKIDTEST/")
        assert "if-none-match" in headers["authorization"].split("SignedHeaders=")[1]
        assert headers["x-amz-content-sha256"] == digest
        assert store.get("v1/acks/laptop/000000000001.json") == body
        assert store.get("v1/missing") is None
        assert store.head_sha256("v1/acks/laptop/000000000001.json") == digest
        assert store.list_keys("v1/acks/laptop/") == ["v1/acks/laptop/000000000001.json"]
    finally:
        server.shutdown()


# ------------------------------------------------- hot-file race isolation
def _racing_anchors(monkeypatch, name: str, when) -> None:
    real = shipper_mod._anchors

    def anchors(path, offset):
        if Path(path).name == name and when(Path(path), offset):
            raise shipper_mod.PlanRace(f"{path} shrank while hashing")
        return real(path, offset)

    monkeypatch.setattr(shipper_mod, "_anchors", anchors)


def _append_offsets(shipper) -> dict:
    return {rel: entry["offset"] for rel, entry in shipper.load_state()["files"].items()
            if entry.get("class") == "append"}


def test_plan_phase_race_backs_off_one_stream_and_ships_the_rest(tmp_path, monkeypatch):
    env = Env(tmp_path)
    env.write("a.jsonl", _rows(0, 2))
    env.write("b.jsonl", _rows(0, 2))
    env.ship_all()
    env.write("a.jsonl", _rows(2, 2), append=True)
    env.write("b.jsonl", _rows(2, 2), append=True)
    old = len(_rows(0, 2))
    _racing_anchors(monkeypatch, "a.jsonl", lambda _path, offset: offset == old)

    shipper = env.shipper()
    result = shipper.cycle()

    assert result["shipped"] is not None
    assert _append_offsets(shipper) == {"a.jsonl": old, "b.jsonl": len(_rows(0, 4))}
    assert "a.jsonl" in shipper.race_backoff
    monkeypatch.undo()
    env.ship_all()
    env.puller().pull_once()
    env.assert_tree_matches_source()


def test_build_phase_anchor_race_skips_only_its_stream(tmp_path, monkeypatch):
    env = Env(tmp_path)
    env.write("a.jsonl", _rows(0, 2))
    env.write("b.jsonl", _rows(0, 2))
    env.ship_all()
    env.write("a.jsonl", _rows(2, 2), append=True)
    env.write("b.jsonl", _rows(2, 2), append=True)
    new = len(_rows(0, 4))
    _racing_anchors(monkeypatch, "a.jsonl", lambda _path, offset: offset == new)

    shipper = env.shipper()
    result = shipper.cycle()

    assert result["shipped"] is not None
    assert _append_offsets(shipper) == {"a.jsonl": len(_rows(0, 2)), "b.jsonl": new}
    assert "a.jsonl" in shipper.race_backoff
    monkeypatch.undo()
    env.ship_all()
    env.puller().pull_once()
    env.assert_tree_matches_source()


class _SlowShipper:
    def __init__(self, seconds: float):
        self.seconds = seconds

    def cycle(self) -> dict:
        threading.Event().wait(self.seconds)
        return {"shipped": None}


def test_guarded_cycle_escalates_only_a_starved_cycle():
    fired = []
    result = shipper_mod.run_guarded_cycle(_SlowShipper(0.5), starved_after=0.05,
                                           on_starved=lambda: fired.append(1))
    assert result == {"shipped": None} and fired == [1]

    fired.clear()
    shipper_mod.run_guarded_cycle(_SlowShipper(0.0), starved_after=0.2,
                                  on_starved=lambda: fired.append(1))
    threading.Event().wait(0.4)
    assert fired == []

    shipper_mod.run_guarded_cycle(_SlowShipper(0.1), starved_after=0.0,
                                  on_starved=lambda: fired.append(1))
    assert fired == []


def test_main_polls_ack_outside_the_cycle_error_path():
    source = (ROOT / "research_segment_shipper.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    main = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "main")
    loop = next(node for node in ast.walk(main) if isinstance(node, ast.While))
    first = loop.body[1]
    assert isinstance(first, ast.Expr) and ast.unparse(first) == "poll_ack()"
    for handler in (node for node in ast.walk(loop) if isinstance(node, ast.Try)):
        assert "poll_laptop_ack" not in ast.unparse(handler)
