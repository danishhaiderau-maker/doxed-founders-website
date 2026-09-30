"""Volume sink, authenticated HTTP serving, laptop ACK recording and prune hook."""

from __future__ import annotations

import ast
import json
import os
import threading
import urllib.error
import urllib.request
from pathlib import Path
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server

import pytest

import research_segment_format as fmt
import research_segment_prune as prune_mod
import research_segment_server as server_mod
import research_segment_shipper as shipper_mod
from research_segment_store import (HttpSegmentSource, PreconditionFailed, StoreError, VolumeStore,
                                    store_from_env)
from test_research_segments import Env, _rows

ROOT = Path(__file__).resolve().parent
TOKEN = "test-admin-token-never-logged"


class _Quiet(WSGIRequestHandler):
    def log_message(self, *_args):
        pass


class VolumeEnv(Env):
    """Shipper writes to a VolumeStore; the laptop reads over HTTP."""

    def __init__(self, tmp_path: Path, **kwargs):
        super().__init__(tmp_path, **kwargs)
        self.store = VolumeStore(self.store_root)
        self.server_app = server_mod.SegmentServer(
            store_root=self.store_root, state_dir=self.state_dir, admin_token=TOKEN)
        self._httpd = make_server("127.0.0.1", 0, self.server_app, server_class=WSGIServer,
                                  handler_class=_Quiet)
        threading.Thread(target=self._httpd.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self._httpd.server_port}"
        self.http = HttpSegmentSource(base_url=self.base, admin_token=TOKEN, attempts=1)

    def close(self):
        self._httpd.shutdown()
        self._httpd.server_close()

    def http_puller(self, **kwargs):
        import research_segment_puller as puller_mod
        return puller_mod.SegmentPuller(store=self.http, shadow_root=self.shadow,
                                        archive_root=self.archive, **kwargs)

    def call(self, route: str, *, method="GET", body=None, token=TOKEN, headers=None):
        request = urllib.request.Request(f"{self.base}/api/research-segments/v1/{route}",
                                         data=body, method=method, headers=dict(headers or {}))
        if token is not None:
            request.add_header("X-Bot-Admin-Token", token)
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, dict(response.headers.items()), response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, dict(exc.headers.items()), exc.read()


@pytest.fixture()
def venv(tmp_path):
    env = VolumeEnv(tmp_path)
    yield env
    env.close()


def _ack_body(seq: int, manifest_raw: bytes) -> bytes:
    return fmt.build_ack(through_seq=seq, manifest_sha256=fmt.sha256_bytes(manifest_raw),
                         applied_at="2026-09-30T00:00:00Z", verifier_version="test")


# ------------------------------------------------------------- volume sink
def test_store_from_env_selects_volume_sink_layout(tmp_path):
    store = store_from_env({"RESEARCH_SEGMENTS_SINK": "volume", "BOT_DATA_DIR": str(tmp_path)})
    assert isinstance(store, VolumeStore)
    store.put_if_absent("v1/seg/000000000001.tar.gz", b"x", sha256=fmt.sha256_bytes(b"x"),
                        content_type="application/gzip")
    assert (tmp_path / "segment-store" / "v1" / "seg" / "000000000001.tar.gz").read_bytes() == b"x"
    with pytest.raises(PreconditionFailed):
        store.put_if_absent("v1/seg/000000000001.tar.gz", b"y", sha256=fmt.sha256_bytes(b"y"),
                            content_type="application/gzip")
    assert (tmp_path / "segment-store" / "v1" / "seg" / "000000000001.tar.gz").read_bytes() == b"x"
    with pytest.raises(StoreError):
        store_from_env({"RESEARCH_SEGMENTS_SINK": "ftp"})


def test_tigris_sink_remains_the_default(tmp_path):
    with pytest.raises(StoreError, match="bucket and credentials"):
        store_from_env({})
    assert shipper_mod.sink_from_env({}) == "tigris"


def test_volume_store_is_outside_the_shipped_universe(tmp_path):
    env = Env(tmp_path)
    env.store = VolumeStore(env.volume / "segment-store")
    env.write("a.jsonl", _rows(0, 3))
    env.ship_all()
    assert (env.volume / "segment-store" / "v1" / "man" / "000000000001.json").is_file()
    assert all(not rel.startswith("segment-store") for rel in env.shipper().scan())
    assert env.shipper().cycle()["shipped"] is None


def test_store_cap_fails_closed_without_touching_sources(tmp_path):
    env = Env(tmp_path, max_store_bytes=1)
    env.store = VolumeStore(env.store_root)
    source = env.write("a.jsonl", _rows(0, 3))
    assert env.shipper().cycle()["shipped"]["seq"] == 1
    env.write("a.jsonl", _rows(3, 3), append=True)
    result = env.shipper().cycle()
    assert result["store_cap_reached"] is True and result["shipped"] is None
    status = json.loads((env.state_dir / "status.json").read_text())
    assert status["last_error"] == "STORE_CAP_REACHED" and status["store_bytes"] > 1
    assert source.read_bytes() == _rows(0, 6)


# ------------------------------------------------------------ HTTP end to end
def test_http_pull_applies_in_order_acks_and_shipper_sees_ack(venv):
    venv.write("trade_lifecycle.jsonl", _rows(0, 5))
    venv.write("state/paper.json", b'{"open": []}')
    venv.ship_all()
    venv.write("trade_lifecycle.jsonl", _rows(5, 4), append=True)
    venv.ship_all()
    result = venv.http_puller().pull_once()
    assert result["applied_seq"] == 2 and result["acked_seq"] == 2
    assert result["ack_receipt"]["result"] == "RECORDED" and result["ack_receipt"]["through_seq"] == 2
    venv.assert_tree_matches_source()
    manifest_two = venv.store.get(fmt.manifest_key("v1", 2))
    recorded = fmt.parse_ack(venv.store.get(fmt.ack_key("v1", 2)))
    assert recorded["manifest_sha256"] == fmt.sha256_bytes(manifest_two)
    receipt = json.loads(venv.store.get(server_mod.receipt_key("v1", 2)))
    assert receipt["through_seq"] == 2 and receipt["received_at"]
    assert venv.shipper().poll_laptop_ack() == 2
    status, _headers, raw = venv.call("head")
    head = json.loads(raw)
    assert status == 200 and head["published_seq"] == 2 and head["laptop_acked"]["through_seq"] == 2
    assert head["pruning_enabled"] is False and head["store_bytes"] > 0
    log = (venv.shadow / ".puller" / "ack-receipts.jsonl").read_text().splitlines()
    assert json.loads(log[-1])["through_seq"] == 2


def test_http_resume_after_sleep_is_idempotent(venv):
    venv.write("a.jsonl", _rows(0, 5))
    venv.ship_all()
    assert venv.http_puller().pull_once()["applied_seq"] == 1
    assert venv.http_puller().pull_once() == {"applied_now": 0, "applied_seq": 1, "acked_seq": 1}
    venv.write("a.jsonl", _rows(5, 2), append=True)
    venv.ship_all()
    assert venv.http_puller().pull_once()["acked_seq"] == 2
    venv.assert_tree_matches_source()


def test_http_chain_gap_stops_without_skipping(venv):
    for index in range(3):
        venv.write(f"f{index}.jsonl", _rows(0, 2, tag=str(index)))
        venv.ship_all()
    hidden = venv.store_root / "v1" / "man" / "000000000002.json"
    parked = hidden.with_name("parked-2.json")
    hidden.rename(parked)
    result = venv.http_puller().pull_once()
    assert result["applied_seq"] == 1 and result["acked_seq"] == 1
    assert not (venv.shadow / "tree" / "f2.jsonl").exists()
    parked.rename(hidden)
    assert venv.http_puller().pull_once()["applied_seq"] == 3


def test_http_tampered_segment_is_rejected(venv):
    venv.write("a.jsonl", _rows(0, 5))
    venv.ship_all()
    segment = venv.store_root / "v1" / "seg" / "000000000001.tar.gz"
    raw = bytearray(segment.read_bytes())
    raw[-1] ^= 0xFF
    segment.write_bytes(bytes(raw))
    with pytest.raises(fmt.SegmentFormatError):
        venv.http_puller().pull_once()
    assert not venv.store.get(fmt.ack_key("v1", 1))


def test_segment_without_manifest_is_not_published(venv):
    venv.write("a.jsonl", _rows(0, 2))
    venv.ship_all()
    status, _h, raw = venv.call("seg/2")
    assert status == 404 and json.loads(raw)["error"] == "NOT_PUBLISHED"


# ------------------------------------------------------------- serving rules
def test_auth_required_and_fail_closed_without_token(venv, tmp_path):
    venv.write("a.jsonl", _rows(0, 2))
    venv.ship_all()
    for token in (None, "", "wrong"):
        status, _h, raw = venv.call("man/1", token=token)
        assert status == 401 and b"segment" not in raw.lower()
    assert venv.call("ack", method="POST", body=b"{}", token=None)[0] == 401
    unset = server_mod.SegmentServer(store_root=venv.store_root, state_dir=venv.state_dir, admin_token="")
    captured = {}
    body = unset({"PATH_INFO": "/api/research-segments/v1/man/1", "REQUEST_METHOD": "GET",
                  "HTTP_X_BOT_ADMIN_TOKEN": ""}, lambda status, headers: captured.setdefault("s", status))
    assert captured["s"].startswith("503") and b"ADMIN_TOKEN_NOT_CONFIGURED" in b"".join(body)
    tigris = server_mod.server_from_env({"BOT_ADMIN_TOKEN": TOKEN, "BOT_DATA_DIR": str(tmp_path)})
    assert tigris.enabled is False
    with pytest.raises(StoreError, match="unauthorized"):
        HttpSegmentSource(base_url=venv.base, admin_token="wrong", attempts=1).get(fmt.manifest_key("v1", 1))
    assert TOKEN not in repr(venv.http)


def test_idempotent_reserve_with_manifest_etag(venv):
    venv.write("a.jsonl", _rows(0, 400))
    venv.ship_all()
    manifest_raw = venv.store.get(fmt.manifest_key("v1", 1))
    manifest = json.loads(manifest_raw)
    first = venv.call("seg/1")
    second = venv.call("seg/1")
    assert first[0] == second[0] == 200 and first[2] == second[2]
    assert first[1]["ETag"] == f'"{manifest["segment_sha256"]}"'
    assert int(first[1]["Content-Length"]) == manifest["segment_size"] == len(first[2])
    assert venv.call("seg/1", headers={"If-None-Match": first[1]["ETag"]})[0] == 304
    man = venv.call("man/1")
    assert man[2] == manifest_raw and man[1]["ETag"] == f'"{fmt.sha256_bytes(manifest_raw)}"'
    assert venv.call("man/1", method="POST", body=b"x")[0] == 405
    assert venv.call("man/0")[0] == 404 and venv.call("../../etc/passwd")[0] == 404


def test_segment_stream_uses_bounded_chunks(venv):
    venv.write("big.jsonl", os.urandom(300 * 1024).hex().encode() + b"\n")
    venv.ship_all()
    captured = {}
    stream = venv.server_app({"PATH_INFO": "/api/research-segments/v1/seg/1", "REQUEST_METHOD": "GET",
                              "HTTP_X_BOT_ADMIN_TOKEN": TOKEN},
                             lambda status, headers: captured.update(status=status, headers=dict(headers)))
    chunks = list(stream)
    stream.close()
    assert captured["status"] == "200 OK" and len(chunks) > 1
    assert max(len(chunk) for chunk in chunks) <= server_mod.CHUNK_BYTES
    assert sum(map(len, chunks)) == int(captured["headers"]["Content-Length"])


# -------------------------------------------------------------------- ACKs
def test_ack_is_write_once_monotonic_and_head_matched(venv):
    for index in range(3):
        venv.write("a.jsonl", _rows(index * 2, 2), append=True)
        venv.ship_all()
    man = {seq: venv.store.get(fmt.manifest_key("v1", seq)) for seq in (1, 2, 3)}
    post = lambda body: venv.call("ack", method="POST", body=body,
                                  headers={"Content-Type": "application/json"})
    status, _h, raw = post(_ack_body(2, man[2]))
    assert status == 201 and json.loads(raw)["result"] == "RECORDED"
    status, _h, raw = post(_ack_body(2, man[2]))
    assert status == 200 and json.loads(raw)["result"] == "ALREADY_RECORDED"
    status, _h, raw = post(_ack_body(1, man[1]))
    assert status == 409 and json.loads(raw)["error"] == "ACK_REGRESSION"
    status, _h, raw = post(_ack_body(3, man[2]))
    assert status == 409 and json.loads(raw)["error"] == "ACK_HEAD_MISMATCH"
    status, _h, raw = post(fmt.build_ack(through_seq=99, manifest_sha256="a" * 64,
                                         applied_at="x", verifier_version="t"))
    assert status == 409 and json.loads(raw)["error"] == "ACK_AHEAD_OF_PUBLISHED"
    assert post(b'{"schema": "nope"}')[0] == 400
    assert post(json.dumps(json.loads(_ack_body(3, man[3])), indent=1).encode())[0] == 400
    assert post(b"x" * (server_mod.MAX_ACK_BODY_BYTES + 1))[0] == 413
    assert venv.call("ack", method="GET")[0] == 405
    # A restarted server rebuilds the ACK head from disk and stays monotonic.
    restarted = server_mod.SegmentServer(store_root=venv.store_root, state_dir=venv.state_dir,
                                         admin_token=TOKEN)
    assert restarted.acked_head()["through_seq"] == 2
    assert restarted.record_ack(_ack_body(1, man[1]))[0].startswith("409")
    assert restarted.record_ack(_ack_body(3, man[3]))[0].startswith("201")
    assert sorted(p.name for p in (venv.store_root / "v1" / "acks" / "laptop").iterdir()) == [
        "000000000002.json", "000000000003.json"]
    status, _h, raw = venv.call("ack/3")
    assert status == 200 and fmt.parse_ack(raw)["through_seq"] == 3


def test_puller_fails_closed_when_fly_holds_a_different_ack_chain(venv):
    venv.write("a.jsonl", _rows(0, 2))
    venv.ship_all()
    venv.write("a.jsonl", _rows(2, 2), append=True)
    venv.ship_all()
    venv.server_app.record_ack(_ack_body(2, venv.store.get(fmt.manifest_key("v1", 2))))
    import research_segment_puller as puller_mod
    puller = venv.http_puller()
    with pytest.raises(puller_mod.PullerError, match="disagrees"):
        puller.pull_once(max_segments=1)
    assert puller.load_state()["applied_seq"] == 1 and puller.load_state()["acked_seq"] == 0


# ------------------------------------------------------------ large backlog
def test_large_first_backlog_drains_in_bounded_batches(tmp_path):
    env = VolumeEnv(tmp_path, max_segment_bytes=512)
    try:
        for index in range(6):
            env.write(f"stream{index}.jsonl", _rows(0, 60, tag=str(index)))
        results = env.ship_all()
        shipped = [r["shipped"] for r in results if r["shipped"]]
        assert len(shipped) >= 15
        assert all(r["segment_bytes"] < 4096 for r in shipped)
        applied_per_run, runs = [], 0
        while True:
            runs += 1
            result = env.http_puller().pull_once(max_segments=7)
            applied_per_run.append(result["applied_now"])
            assert result["acked_seq"] == result["applied_seq"]
            if result["applied_seq"] == len(shipped):
                break
            assert runs < 50
        assert max(applied_per_run) <= 7 and runs >= 3
        env.assert_tree_matches_source()
        assert env.shipper().poll_laptop_ack() == len(shipped)
    finally:
        env.close()


def test_large_snapshot_is_not_starved_by_earlier_growing_streams(tmp_path):
    env = Env(tmp_path, max_segment_bytes=300, large_snapshot_bytes=10 ** 9)
    env.store = VolumeStore(env.store_root)
    env.write("a_live.jsonl", _rows(0, 1))
    env.write("z_research.db", b"d" * 1000)
    shipped_paths = []
    for cycle in range(6):
        env.write("a_live.jsonl", _rows(cycle + 1, 1), append=True)
        result = env.shipper().cycle()
        seq = result["shipped"]["seq"]
        shipped_paths += [m["path"] for m in json.loads(env.store.get(fmt.manifest_key("v1", seq)))["members"]]
        if "z_research.db" in shipped_paths:
            break
    assert "z_research.db" in shipped_paths and cycle <= 2
    env.write("a_live.jsonl", _rows(50, 1), append=True)
    env.ship_all()
    env.puller().pull_once()
    env.assert_tree_matches_source()


def test_hot_snapshot_written_between_scan_and_read_still_ships(tmp_path):
    env = Env(tmp_path, large_snapshot_bytes=10 ** 9)
    env.store = VolumeStore(env.store_root)
    db = env.write("research.db", b"a" * 4000)
    shipper = env.shipper()
    real_scan = shipper.scan

    def scan_then_write():
        found = real_scan()
        db.write_bytes(b"b" * 4100)
        os.utime(db, ns=(db.stat().st_atime_ns, db.stat().st_mtime_ns + 5_000_000_000))
        return found

    shipper.scan = scan_then_write
    result = shipper.cycle()
    assert result["shipped"] and "race" not in result
    member = json.loads(env.store.get(fmt.manifest_key("v1", 1)))["members"][0]
    assert member["path"] == "research.db" and member["size"] == 4100
    shipper.scan = real_scan
    assert shipper.cycle()["shipped"] is None
    env.puller().pull_once()
    env.assert_tree_matches_source()


def test_snapshot_changing_during_every_copy_backs_off_without_stalling_others(tmp_path, monkeypatch):
    env = Env(tmp_path, large_snapshot_bytes=10 ** 9)
    env.store = VolumeStore(env.store_root)
    env.write("a_live.jsonl", _rows(0, 3))
    db = env.write("z_research.db", b"d" * 1000)
    hot_inode = db.stat().st_ino
    real_fstat = os.fstat
    ticks = [0]

    def churning_fstat(fd):
        result = real_fstat(fd)
        if result.st_ino != hot_inode:
            return result
        ticks[0] += 1
        fields = list(result)
        fields[8] += ticks[0]
        return os.stat_result(fields, {"st_mtime_ns": result.st_mtime_ns + ticks[0]})

    monkeypatch.setattr(shipper_mod.os, "fstat", churning_fstat)
    shipper = env.shipper()
    raced = shipper.cycle()
    assert raced["shipped"] is None and raced["race"] == "z_research.db"
    status = json.loads(shipper.status_path.read_text())
    assert status["last_error"].startswith("PLAN_RACE") and "changed while copying" in status["last_error"]
    assert [item["path"] for item in status["racing_paths"]] == ["z_research.db"]

    shipped = shipper.cycle()
    members = json.loads(env.store.get(fmt.manifest_key("v1", shipped["shipped"]["seq"])))["members"]
    assert [m["path"] for m in members] == ["a_live.jsonl"]
    assert shipper.cycle()["shipped"] is None

    env.clock[0] += shipper_mod.RACE_BACKOFF_BASE_SECONDS
    assert shipper.cycle()["race"] == "z_research.db"
    assert shipper.race_backoff["z_research.db"][0] == 2
    assert shipper.race_backoff["z_research.db"][1] == env.clock[0] + 2 * shipper_mod.RACE_BACKOFF_BASE_SECONDS

    monkeypatch.setattr(shipper_mod.os, "fstat", real_fstat)
    env.clock[0] += 2 * shipper_mod.RACE_BACKOFF_BASE_SECONDS
    final = shipper.cycle()
    assert final["shipped"] and shipper.race_backoff == {}
    assert json.loads(shipper.status_path.read_text())["racing_paths"] == []
    env.puller().pull_once()
    env.assert_tree_matches_source()


# ------------------------------------------------------- SQLite snapshots
def _sqlite_db(path: Path, rows: int, start: int = 0) -> None:
    import sqlite3
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, body TEXT)")
        connection.executemany("INSERT INTO events (id, body) VALUES (?, ?)",
                               [(i, "x" * 200) for i in range(start, start + rows)])


def _sqlite_rows(path: Path) -> int:
    import sqlite3
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        return connection.execute("SELECT COUNT(*) FROM events").fetchone()[0]


def test_sqlite_above_member_cap_ships_as_consistent_backup_alone_and_streamed(tmp_path, monkeypatch):
    env = Env(tmp_path, max_member_bytes=64 * 1024, large_snapshot_bytes=10 ** 9)
    env.store = VolumeStore(env.store_root)
    env.write("a_live.jsonl", _rows(0, 3))
    _sqlite_db(env.runtime / "research.db", rows=2000)
    source_size = (env.runtime / "research.db").stat().st_size
    assert source_size > 64 * 1024
    real_build = fmt.build_segment

    def in_memory_build(payloads):
        assert all(len(payload) < 64 * 1024 for payload in payloads), "SQLite must be streamed"
        return real_build(payloads)

    monkeypatch.setattr(shipper_mod.fmt, "build_segment", in_memory_build)
    env.ship_all()
    manifests = [json.loads(env.store.get(fmt.manifest_key("v1", seq)))
                 for seq in range(1, env.shipper().load_state()["seq"] + 1)]
    db_manifest = next(m for m in manifests if m["members"][0]["path"] == "research.db")
    assert len(db_manifest["members"]) == 1
    assert db_manifest["members"][0]["consistency"] == shipper_mod.SQLITE_CONSISTENCY
    tracked = env.shipper().load_state()["files"]["research.db"]
    assert tracked["size"] == source_size and tracked["wal"] == [0, 0, 0]
    assert env.shipper().cycle()["shipped"] is None
    assert not list((env.state_dir / "sqlite-snapshots").glob("*.db"))
    env.puller().pull_once()
    assert _sqlite_rows(env.shadow / "tree" / "research.db") == 2000


def test_sqlite_written_concurrently_is_never_shipped_torn(tmp_path):
    import sqlite3
    env = Env(tmp_path, large_snapshot_bytes=10 ** 9)
    env.store = VolumeStore(env.store_root)
    db = env.runtime / "research.db"
    _sqlite_db(db, rows=3000)
    stop = threading.Event()

    def writer():
        next_id = 10_000
        with sqlite3.connect(db, timeout=5) as connection:
            while not stop.is_set():
                connection.execute("INSERT INTO events (id, body) VALUES (?, ?)", (next_id, "y" * 200))
                connection.commit()
                next_id += 1

    thread = threading.Thread(target=writer)
    thread.start()
    try:
        result = env.shipper().cycle()
    finally:
        stop.set()
        thread.join()
    assert result["shipped"] or result.get("race") == "research.db"
    if result["shipped"]:
        env.puller().pull_once()
        assert _sqlite_rows(env.shadow / "tree" / "research.db") >= 3000


def test_sqlite_backup_that_cannot_finish_backs_off_and_ships_nothing(tmp_path, monkeypatch):
    env = Env(tmp_path, large_snapshot_bytes=10 ** 9)
    env.store = VolumeStore(env.store_root)
    _sqlite_db(env.runtime / "research.db", rows=10)

    def too_hot(source, target, *, deadline_seconds):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"partial")
        raise TimeoutError("research.db online backup exceeded 180s")

    monkeypatch.setattr(shipper_mod, "sqlite_online_backup", too_hot)
    shipper = env.shipper()
    result = shipper.cycle()
    assert result["shipped"] is None and result["race"] == "research.db"
    assert shipper.load_state()["seq"] == 0
    assert not list((env.state_dir / "sqlite-snapshots").glob("*.db"))
    assert "online backup not completed" in json.loads(shipper.status_path.read_text())["last_error"]


def test_oversized_paths_stay_visible_when_budget_breaks_first(tmp_path):
    env = Env(tmp_path, max_segment_bytes=300, max_member_bytes=2000, large_snapshot_bytes=10 ** 9)
    env.store = VolumeStore(env.store_root)
    env.write("a_live.jsonl", _rows(0, 20))
    env.write("b_live.jsonl", _rows(0, 20))
    env.write("z_huge.json", b"h" * 5000)
    shipper = env.shipper()
    result = shipper.cycle()
    assert result["shipped"] and result["deferred_bytes"]
    assert json.loads(shipper.status_path.read_text())["oversized_paths"] == ["z_huge.json"]


def test_huge_sqlite_is_reshipped_at_most_once_per_huge_interval(tmp_path):
    env = Env(tmp_path, max_member_bytes=64 * 1024, large_snapshot_bytes=1024,
              large_snapshot_interval=60, huge_snapshot_interval=6 * 3600)
    env.store = VolumeStore(env.store_root)
    db = env.runtime / "research.db"
    _sqlite_db(db, rows=2000)
    env.ship_all()
    _sqlite_db(db, rows=10, start=5000)
    env.clock[0] += 3600
    shipper = env.shipper()
    assert shipper.cycle()["shipped"] is None
    assert "research.db" in json.loads(shipper.status_path.read_text())["throttled_snapshots"]
    env.clock[0] += 6 * 3600
    assert env.shipper().cycle()["shipped"]


# --------------------------------------------------------- safety surfaces
def _imports(path: Path) -> set:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    return names


@pytest.mark.parametrize("name", ["research_segment_server.py", "research_segment_prune.py"])
def test_server_and_prune_hook_have_no_bot_lock_or_delete_surface(name):
    source = (ROOT / name).read_text(encoding="utf-8")
    assert not _imports(ROOT / name) & {"bot", "flask", "btc_conservative_agent", "shutil", "ccxt"}
    assert "trade_lock" not in source and "state_lock" not in source
    for forbidden in (".unlink(", "os.remove", "rmtree", "os.rename", ".rename(", "os.replace"):
        assert forbidden not in source


def test_bot_mounts_segment_server_ahead_of_flask_with_reserved_workers():
    source = (ROOT / "bot.py").read_text(encoding="utf-8")
    assert "app=_mount_research_segment_server(app)" in source
    assert "_research_segment_thread_cap = threading.BoundedSemaphore(2)" in source
    assert 'request_path.startswith(self._research_segment_path_prefix)' in source
    assert '"research_segments", "RESEARCH_SEGMENTS"' in source
    dispatch = server_mod.mount(lambda environ, start: [b"flask"], {"BOT_ADMIN_TOKEN": TOKEN})
    assert dispatch({"PATH_INFO": "/health"}, lambda *a: None) == [b"flask"]


def test_fly_config_enables_volume_sink_shadow_only():
    toml = (ROOT / "fly.toml").read_text(encoding="utf-8")
    assert 'RESEARCH_SEGMENTS_ENABLED = "1"' in toml and 'RESEARCH_SEGMENTS_SINK = "volume"' in toml
    assert "PRUNE" not in toml
    assert shipper_mod.PRUNING_ENABLED is False and prune_mod.PRUNE_ENABLED is False


# ------------------------------------------------------------- prune hook
def test_prune_plan_is_deny_by_default_and_never_deletes(venv):
    rotated = venv.write("market.jsonl.1", _rows(0, 20))
    venv.write("market.jsonl", _rows(20, 2))
    venv.write("state.json", b"{}")
    venv.ship_all()
    shipper = venv.shipper()
    state, universe = shipper.load_state(), shipper.scan()
    rules = {"extensions": frozenset({".jsonl", ".json"})}
    before = prune_mod.plan_prune(shipper_state=state, rules=rules, universe=universe,
                                  store_root=venv.store_root, prefix="v1", snapshot_receipt=None,
                                  environ={"RESEARCH_SEGMENTS_PRUNE_ENABLED": "1"})
    assert before["candidates"] == [] and before["allowed"] is False
    manifest = venv.store.get(fmt.manifest_key("v1", 1))
    venv.server_app.record_ack(_ack_body(1, manifest))
    plan = prune_mod.plan_prune(shipper_state=state, rules=rules, universe=universe,
                                store_root=venv.store_root, prefix="v1",
                                snapshot_receipt={"schema": prune_mod.SNAPSHOT_RECEIPT_SCHEMA,
                                                  "created_at": "2999-01-01T00:00:00Z"},
                                environ={"RESEARCH_SEGMENTS_PRUNE_ENABLED": "1"})
    assert [c["path"] for c in plan["candidates"]] == ["market.jsonl.1"]
    assert plan["allowed"] is False
    assert "PRUNE_DISABLED_IN_CODE" in plan["deny_reasons"]
    assert "INSUFFICIENT_PROVEN_ACK_CYCLES" in plan["deny_reasons"]
    assert rotated.read_bytes() == _rows(0, 20)
