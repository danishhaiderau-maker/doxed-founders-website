"""Fresh-epoch cutover: the genesis segment baselines history; only new bytes ship."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3

import pytest

import research_segment_format as fmt
import research_segment_puller as puller_mod
from test_research_segments import Env, _rows


def _epoch(tmp_path, **kwargs) -> Env:
    return Env(tmp_path, baseline_genesis=True, **kwargs)


def _manifest(env: Env, seq: int) -> dict:
    return json.loads(env.store.get(fmt.manifest_key("v1", seq)))


def _tree(env: Env) -> dict[str, bytes]:
    tree = env.shadow / "tree"
    return {path.relative_to(tree).as_posix(): path.read_bytes()
            for path in tree.rglob("*") if path.is_file()}


def _history(env: Env) -> None:
    env.write("signal_replay.jsonl", _rows(0, 50) + b'{"partial"')
    env.write("signal_replay.jsonl.1", _rows(0, 400, "old"))
    env.write("v3/receipts/idem/0001.json", b'{"old": 1}')
    env.write("research_session.json", b'{"collector_v22_epoch_id": "epoch-new"}')


def test_genesis_records_history_and_ships_only_current_state_and_new_bytes(tmp_path):
    env = _epoch(tmp_path)
    _history(env)
    results = env.ship_all()
    assert results[0]["genesis"] is True
    genesis = _manifest(env, 1)
    assert [(m["kind"], m["path"]) for m in genesis["members"]] == [
        ("BASELINE", "signal_replay.jsonl")]
    member = genesis["members"][0]
    prefix = _rows(0, 50)
    assert member["base_offset"] == len(prefix) and member["size"] == 0
    assert member["source_sha256"] == hashlib.sha256(prefix).hexdigest()
    assert member["source_size"] == len(prefix) + len(b'{"partial"')
    later = [(m["kind"], m["path"]) for seq in range(2, 10)
             if env.store.get(fmt.manifest_key("v1", seq))
             for m in _manifest(env, seq)["members"]]
    assert later == [("SNAPSHOT", "research_session.json")]
    state = json.loads((env.state_dir / "state.json").read_text())
    assert state["baseline"]["append_streams"] == 1 and state["baseline"]["tracked_only_files"] == 2
    assert state["files"]["signal_replay.jsonl.1"]["baseline"] is True

    env.write("signal_replay.jsonl", b'}\n' + _rows(50, 3), append=True)
    env.ship_all()
    env.puller().pull_once()
    assert _tree(env) == {
        "signal_replay.jsonl": b'{"partial"}\n' + _rows(50, 3),
        "research_session.json": b'{"collector_v22_epoch_id": "epoch-new"}',
    }
    assert env.puller().load_state()["baselines"]["signal_replay.jsonl"]["base_offset"] == len(prefix)


def test_csv_keeps_its_header_so_new_rows_stay_readable(tmp_path):
    env = _epoch(tmp_path)
    env.write("sub/trades.csv", b"ts,side\n1,buy\n2,sell\n")
    env.ship_all()
    env.write("sub/trades.csv", b"3,buy\n", append=True)
    env.ship_all()
    env.puller().pull_once()
    assert _tree(env) == {"sub/trades.csv": b"ts,side\n3,buy\n"}


def test_rotation_after_cutover_seals_relative_to_baseline_and_restarts_active(tmp_path):
    env = _epoch(tmp_path)
    active = env.write("market_microstructure_1s.jsonl", _rows(0, 10))
    env.ship_all()
    env.write("market_microstructure_1s.jsonl", _rows(10, 2), append=True)
    env.ship_all()
    env.puller().pull_once()
    env.write("market_microstructure_1s.jsonl", _rows(12, 2), append=True)
    os.rename(active, active.with_name("market_microstructure_1s.jsonl.1"))
    env.write("market_microstructure_1s.jsonl", _rows(100, 3))
    env.ship_all()
    env.puller().pull_once()
    assert _tree(env) == {"market_microstructure_1s.jsonl.1": _rows(10, 4),
                          "market_microstructure_1s.jsonl": _rows(100, 3)}
    baselines = env.puller().load_state()["baselines"]
    assert "market_microstructure_1s.jsonl" not in baselines
    assert baselines["market_microstructure_1s.jsonl.1"]["base_offset"] == len(_rows(0, 10))
    env.write("market_microstructure_1s.jsonl", _rows(103, 1), append=True)
    env.ship_all()
    env.puller().pull_once()
    assert _tree(env)["market_microstructure_1s.jsonl"] == _rows(100, 4)


def test_seal_reapplied_after_crash_still_moves_the_baseline(tmp_path, monkeypatch):
    env = _epoch(tmp_path)
    active = env.write("tape.jsonl", _rows(0, 10))
    env.ship_all()
    env.puller().pull_once()
    env.write("tape.jsonl", _rows(10, 2), append=True)
    os.rename(active, active.with_name("tape.jsonl.1"))
    env.write("tape.jsonl", _rows(100, 1))
    env.ship_all()
    real_save = puller_mod.SegmentPuller.save_state

    def crash_on_save(self, state):
        raise OSError("sleep during checkpoint")

    monkeypatch.setattr(puller_mod.SegmentPuller, "save_state", crash_on_save)
    with pytest.raises(OSError):
        env.puller().pull_once()
    monkeypatch.setattr(puller_mod.SegmentPuller, "save_state", real_save)
    env.puller().pull_once()
    assert _tree(env) == {"tape.jsonl.1": _rows(10, 2), "tape.jsonl": _rows(100, 1)}


def test_history_that_changes_or_vanishes_after_cutover_ships_or_tombstones(tmp_path):
    env = _epoch(tmp_path)
    _history(env)
    env.ship_all()
    env.clock[0] += 1
    env.write("v3/receipts/idem/0001.json", b'{"old": 2}')
    env.write("v3/receipts/idem/0002.json", b'{"new": 1}')
    (env.runtime / "signal_replay.jsonl.1").unlink()
    env.ship_all()
    env.puller().pull_once()
    tree = _tree(env)
    assert tree["v3/receipts/idem/0001.json"] == b'{"old": 2}'
    assert tree["v3/receipts/idem/0002.json"] == b'{"new": 1}'
    assert "signal_replay.jsonl.1" not in tree
    markers = list((env.shadow / "tombstones").glob("*.json"))
    assert [json.loads(p.read_text())["path"] for p in markers] == ["signal_replay.jsonl.1"]


def test_sqlite_ships_as_consistent_backup_in_the_new_epoch(tmp_path):
    env = _epoch(tmp_path)
    db = env.runtime / "research" / "research.db"
    db.parent.mkdir(parents=True)
    with sqlite3.connect(db) as connection:
        connection.execute("CREATE TABLE t (x)")
        connection.executemany("INSERT INTO t VALUES (?)", [(i,) for i in range(100)])
    env.ship_all()
    members = [m for seq in (2, 3) if env.store.get(fmt.manifest_key("v1", seq))
               for m in _manifest(env, seq)["members"]]
    assert [(m["path"], m["consistency"]) for m in members] == [
        ("research/research.db", "sqlite_online_backup_v1")]
    env.puller().pull_once()
    with sqlite3.connect(env.shadow / "tree" / "research" / "research.db") as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 100


def test_baseline_refuses_a_tree_that_is_not_fresh(tmp_path):
    env = _epoch(tmp_path)
    env.write("a.jsonl", _rows(0, 5))
    env.ship_all()
    stale = env.shadow / "tree" / "a.jsonl"
    stale.parent.mkdir(parents=True, exist_ok=True)
    stale.write_bytes(_rows(0, 5))
    with pytest.raises(puller_mod.PullerError, match="fresh tree"):
        env.puller().pull_once()


def test_genesis_is_off_by_default_and_runs_only_on_an_empty_checkpoint(tmp_path):
    env = Env(tmp_path)
    env.write("a.jsonl", _rows(0, 5))
    env.ship_all()
    assert _manifest(env, 1)["members"][0]["kind"] == "APPEND"
    env.kwargs["baseline_genesis"] = True
    env.write("a.jsonl", _rows(5, 1), append=True)
    env.ship_all()
    assert _manifest(env, 2)["members"][0]["kind"] == "APPEND"


def _parity_module():
    import importlib.util
    from pathlib import Path
    path = Path(__file__).resolve().parents[2] / "scripts" / "research_segment_fly_parity.py"
    spec = importlib.util.spec_from_file_location("research_segment_fly_parity", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_checkpoint_parity_is_green_after_pull_and_red_on_tamper(tmp_path):
    from test_research_segments_volume import VolumeEnv
    parity = _parity_module()
    env = VolumeEnv(tmp_path, baseline_genesis=True)
    try:
        _history(env)
        env.write("sub/trades.csv", b"ts,side\n1,buy\n")
        env.ship_all()
        env.write("signal_replay.jsonl", b'}\n' + _rows(50, 200), append=True)
        env.write("sub/trades.csv", b"2,sell\n", append=True)
        env.ship_all()
        env.http_puller().pull_once()
        checkpoint = env.http.checkpoint_files()
        state = env.http_puller().load_state()
        assert checkpoint["seq"] == state["applied_seq"]
        report = parity.classify(checkpoint["files"], checkpoint["tombstones"],
                                 env.shadow / "tree", state["baselines"])
        assert report["verdict"] == "GREEN", report
        assert report["counts"]["baseline_only"] == 2 and report["counts"]["append_match"] == 2
        tree_file = env.shadow / "tree" / "signal_replay.jsonl"
        raw = bytearray(tree_file.read_bytes())
        raw[-10] ^= 1
        tree_file.write_bytes(bytes(raw))
        (env.shadow / "tree" / "research_session.json").unlink()
        report = parity.classify(checkpoint["files"], checkpoint["tombstones"],
                                 env.shadow / "tree", state["baselines"])
        assert report["verdict"] == "RED"
        assert report["counts"]["sealed_mismatch"] == 1 and report["counts"]["missing"] == 1
    finally:
        env.close()


def test_archived_v1_stays_pullable_and_ackable_next_to_live_v2(tmp_path):
    import io
    import research_segment_server as server_mod
    from research_segment_store import VolumeStore
    volume = tmp_path / "data"
    store = VolumeStore(volume / "segment-store")
    for prefix, state in (("v1", "segment-shipper"), ("v2", "segment-shipper-v2")):
        raw = fmt.build_segment([b"x\n"])
        manifest = fmt.canonical_json(fmt.build_manifest(
            prefix=prefix, seq=1, prev_manifest_sha256=fmt.GENESIS_PREV_SHA256, segment_raw=raw,
            members=[{"index": 0, "kind": "SNAPSHOT", "path": "a.json", "size": 2,
                      "sha256": fmt.sha256_bytes(b"x\n"), "consistency": "atomic_file"}],
            source_git_rev="abc", collection_epoch_id="e", window_start=0, window_end=1))
        store.put_if_absent(fmt.segment_key(prefix, 1), raw, sha256=fmt.sha256_bytes(raw), content_type="x")
        store.put_if_absent(fmt.manifest_key(prefix, 1), manifest, sha256=fmt.sha256_bytes(manifest),
                            content_type="x")
        (volume / state).mkdir(parents=True)
        (volume / state / "status.json").write_text(json.dumps({"shipped_seq": 1}))
    env = {"BOT_DATA_DIR": str(volume), "RESEARCH_SEGMENTS_SINK": "volume", "BOT_ADMIN_TOKEN": "t",
           "RESEARCH_SEGMENTS_PREFIX": "v2", "RESEARCH_SEGMENTS_STATE_DIR": str(volume / "segment-shipper-v2"),
           "RESEARCH_SEGMENTS_ARCHIVE_PREFIXES": f"v1={volume / 'segment-shipper'}"}
    app = server_mod.mount(lambda environ, start: [b"flask"], env)

    def call(path, method="GET", body=b""):
        seen = {}
        environ = {"PATH_INFO": path, "REQUEST_METHOD": method, "HTTP_X_BOT_ADMIN_TOKEN": "t",
                   "CONTENT_LENGTH": str(len(body)), "wsgi.input": io.BytesIO(body)}
        payload = b"".join(app(environ, lambda status, headers: seen.update(status=status)))
        return seen["status"], payload

    for prefix in ("v1", "v2"):
        status, raw = call(f"/api/research-segments/{prefix}/head")
        assert status.startswith("200") and json.loads(raw)["prefix"] == prefix
    manifest_v1 = store.get(fmt.manifest_key("v1", 1))
    ack = fmt.build_ack(through_seq=1, manifest_sha256=fmt.sha256_bytes(manifest_v1),
                        applied_at="2026-09-30T00:00:00Z", verifier_version="t")
    status, _raw = call("/api/research-segments/v1/ack", "POST", ack)
    assert status.startswith("201")
    assert store.get(fmt.ack_key("v1", 1)) == ack
    assert call("/api/research-segments/v3/head")[0].startswith("404")


def test_baseline_member_validation():
    good = {"index": 0, "kind": "BASELINE", "path": "a.jsonl", "size": 0,
            "sha256": fmt.sha256_bytes(b""), "base_offset": 10, "source_sha256": "a" * 64}
    fmt.validate_member(good, 0)
    with pytest.raises(fmt.SegmentFormatError):
        fmt.validate_member({**good, "source_sha256": "nope"}, 0)
    with pytest.raises(fmt.SegmentFormatError):
        fmt.validate_member({**good, "size": 11}, 0)


def _retire_sealed_then_rotate_again(env: Env, active) -> None:
    env.write("tape.jsonl", _rows(0, 10))
    env.ship_all()
    env.write("tape.jsonl", _rows(10, 2), append=True)
    os.rename(active, active.with_name("tape.jsonl.1"))
    env.write("tape.jsonl", _rows(100, 3))
    env.ship_all()
    env.puller().pull_once()
    assert "tape.jsonl.1" in env.puller().load_state()["baselines"]
    # A boundary reset retires the sealed generation on Fly ...
    (env.runtime / "tape.jsonl.1").unlink()
    env.clock[0] += 1
    env.ship_all()
    env.puller().pull_once()
    # ... and the next rotation seals the new stream (shipped from byte 0) onto the same name.
    env.write("tape.jsonl", _rows(103, 2), append=True)
    os.rename(active, active.with_name("tape.jsonl.1"))
    env.write("tape.jsonl", _rows(200, 1))
    env.ship_all()


def test_seal_onto_a_retired_name_ignores_its_stale_baseline(tmp_path):
    env = _epoch(tmp_path)
    active = env.runtime / "tape.jsonl"
    _retire_sealed_then_rotate_again(env, active)
    result = env.puller().pull_once()
    assert result["applied_seq"] == result["acked_seq"]
    assert _tree(env) == {"tape.jsonl.1": _rows(100, 5), "tape.jsonl": _rows(200, 1)}
    state = env.puller().load_state()
    assert "tape.jsonl.1" not in state["baselines"]
    assert "tape.jsonl.1" not in state["tombstoned"]
    kept = [p.read_bytes() for p in (env.shadow / "quarantine").rglob("tape.jsonl.1")]
    assert kept == [_rows(10, 2)]


def test_seal_onto_a_retired_name_reapplied_after_crash(tmp_path, monkeypatch):
    env = _epoch(tmp_path)
    active = env.runtime / "tape.jsonl"
    _retire_sealed_then_rotate_again(env, active)
    real_save = puller_mod.SegmentPuller.save_state

    def crash_on_save(self, state):
        raise OSError("sleep during checkpoint")

    monkeypatch.setattr(puller_mod.SegmentPuller, "save_state", crash_on_save)
    with pytest.raises(OSError):
        env.puller().pull_once()
    monkeypatch.setattr(puller_mod.SegmentPuller, "save_state", real_save)
    env.puller().pull_once()
    assert _tree(env) == {"tape.jsonl.1": _rows(100, 5), "tape.jsonl": _rows(200, 1)}
