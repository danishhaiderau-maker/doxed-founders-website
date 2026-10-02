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
    # The racing stream is dropped and the rest of the same cycle still ships.
    raced = shipper.cycle()
    assert raced["race"] == "z_research.db" and raced["shipped"]
    members = json.loads(env.store.get(fmt.manifest_key("v1", raced["shipped"]["seq"])))["members"]
    assert [m["path"] for m in members] == ["a_live.jsonl"]
    status = json.loads(shipper.status_path.read_text())
    assert [item["path"] for item in status["racing_paths"]] == ["z_research.db"]
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


def test_atomically_replaced_receipts_ship_their_new_version_instead_of_racing(tmp_path):
    env = Env(tmp_path)
    env.store = VolumeStore(env.store_root)
    env.write("a_live.jsonl", _rows(0, 3))
    receipts = [
        env.write(f"v3/receipts/r/{name}/complete.json", b'{"v": 1}')
        for name in "abcdef"
    ]
    assert len(receipts) > shipper_mod.RACE_REBUILDS_PER_CYCLE
    shipper = env.shipper()
    real_plan = shipper.plan

    def plan_then_replace(*args, **kwargs):
        ops = real_plan(*args, **kwargs)
        for path in receipts:
            tmp = path.with_suffix(".tmp")
            tmp.write_bytes(b'{"v": 2}')
            os.replace(tmp, path)
        return ops

    shipper.plan = plan_then_replace
    result = shipper.cycle()
    assert result["shipped"] and "race" not in result
    members = json.loads(env.store.get(fmt.manifest_key("v1", result["shipped"]["seq"])))["members"]
    assert sorted(m["path"] for m in members) == ["a_live.jsonl"] + sorted(
        f"v3/receipts/r/{name}/complete.json" for name in "abcdef"
    )
    assert shipper.race_backoff == {}
    shipper.plan = real_plan
    env.puller().pull_once()
    env.assert_tree_matches_source()


def test_append_stream_replaced_between_scan_and_read_still_races(tmp_path):
    env = Env(tmp_path)
    env.store = VolumeStore(env.store_root)
    live = env.write("a_live.jsonl", _rows(0, 3))
    env.write("z_other.json", b'{"x": 1}')
    shipper = env.shipper()
    real_plan = shipper.plan

    def plan_then_replace(*args, **kwargs):
        ops = real_plan(*args, **kwargs)
        tmp = live.with_suffix(".tmp")
        tmp.write_bytes(live.read_bytes())
        os.replace(tmp, live)
        return ops

    shipper.plan = plan_then_replace
    raced = shipper.cycle()
    assert raced["race"] == "a_live.jsonl"


def test_many_racing_streams_never_block_the_append_backlog_and_build_once(tmp_path):
    env = Env(tmp_path)
    env.store = VolumeStore(env.store_root)
    live = env.write("market_microstructure_1s.jsonl", _rows(0, 200))
    doomed = [env.write(f"v3/receipts/handoff-{name}.json", b'{"x": 1}') for name in "abcdefgh"]
    swapped = env.write("z_rotating.jsonl", _rows(0, 3))
    assert len(doomed) + 1 > shipper_mod.RACE_REBUILDS_PER_CYCLE
    shipper = env.shipper()
    real_plan, real_build = shipper.plan, shipper._build
    builds = [0]

    def plan_then_churn(*args, **kwargs):
        ops = real_plan(*args, **kwargs)
        for path in doomed:
            path.unlink()
        tmp = swapped.with_suffix(".tmp")
        tmp.write_bytes(swapped.read_bytes())
        os.replace(tmp, swapped)
        return ops

    def counting_build(*args, **kwargs):
        builds[0] += 1
        return real_build(*args, **kwargs)

    shipper.plan, shipper._build = plan_then_churn, counting_build
    result = shipper.cycle()
    assert result["shipped"], result
    assert builds[0] == 1
    members = json.loads(env.store.get(fmt.manifest_key("v1", result["shipped"]["seq"])))["members"]
    assert [m["path"] for m in members] == ["market_microstructure_1s.jsonl"]
    assert {item["path"] for item in shipper.racing_paths()} == {
        *(f"v3/receipts/handoff-{name}.json" for name in "abcdefgh"), "z_rotating.jsonl"}
    assert "market_microstructure_1s.jsonl" not in shipper.race_backoff

    shipper.plan = real_plan
    with live.open("ab") as handle:
        handle.write(_rows(200, 50))
    env.clock[0] += shipper_mod.RACE_BACKOFF_BASE_SECONDS
    follow = shipper.cycle()
    members = json.loads(env.store.get(fmt.manifest_key("v1", follow["shipped"]["seq"])))["members"]
    by_path = {m["path"]: m for m in members}
    assert by_path["market_microstructure_1s.jsonl"]["kind"] == fmt.KIND_APPEND
    assert by_path["market_microstructure_1s.jsonl"]["base_offset"] > 0
    assert "z_rotating.jsonl" in by_path
    assert shipper.race_backoff == {}
    env.puller().pull_once()
    env.assert_tree_matches_source()


def test_seal_race_skips_the_dependent_append_of_the_same_stream(tmp_path):
    env = Env(tmp_path)
    env.store = VolumeStore(env.store_root)
    live = env.write("a_live.jsonl", _rows(0, 3))
    env.write("b_other.json", b'{"x": 1}')
    shipper = env.shipper()
    assert shipper.cycle()["shipped"]
    with live.open("ab") as handle:
        handle.write(_rows(3, 2))
    os.replace(live, live.with_name("a_live.jsonl.1"))
    env.write("a_live.jsonl", _rows(5, 2))
    env.write("b_other.json", b'{"x": 2}')
    real_read = shipper._read

    def seal_races(op):
        if op["kind"] == fmt.KIND_SEAL:
            raise shipper_mod.PlanRace("sealed file changed", op["stream"])
        return real_read(op)

    shipper._read = seal_races
    result = shipper.cycle()
    members = json.loads(env.store.get(fmt.manifest_key("v1", result["shipped"]["seq"])))["members"]
    assert [m["path"] for m in members] == ["b_other.json"]
    assert result["race"] == "a_live.jsonl"
    shipper._read = real_read
    env.clock[0] += shipper_mod.RACE_BACKOFF_BASE_SECONDS
    assert shipper.cycle()["shipped"]
    env.puller().pull_once()
    env.assert_tree_matches_source()


def test_file_deleted_between_scan_and_read_is_a_race_for_its_stream_only(tmp_path):
    env = Env(tmp_path)
    env.store = VolumeStore(env.store_root)
    env.write("a_live.jsonl", _rows(0, 3))
    doomed = env.write("z_handoff.json", b'{"x": 1}')
    shipper = env.shipper()
    real_plan = shipper.plan

    def plan_then_delete(*args, **kwargs):
        ops = real_plan(*args, **kwargs)
        doomed.unlink()
        return ops

    shipper.plan = plan_then_delete
    raced = shipper.cycle()
    assert raced["race"] == "z_handoff.json" and raced["shipped"]
    members = json.loads(env.store.get(fmt.manifest_key("v1", raced["shipped"]["seq"])))["members"]
    assert [m["path"] for m in members] == ["a_live.jsonl"]
    assert [item["path"] for item in shipper.racing_paths()] == ["z_handoff.json"]
    shipper.plan = real_plan
    # The vanished stream can never ship, so it must not stay in racing_paths
    # (the laptop refuses promotion while a never-shipped path is racing).
    assert shipper.cycle()["shipped"] is None
    assert shipper.race_backoff == {}
    assert json.loads(shipper.status_path.read_text())["racing_paths"] == []
    env.puller().pull_once()
    env.assert_tree_matches_source()


def test_file_deleted_between_scan_and_plan_is_skipped(tmp_path):
    env = Env(tmp_path)
    env.store = VolumeStore(env.store_root)
    env.write("a_live.jsonl", _rows(0, 3))
    doomed = env.write("z_handoff.json", b'{"x": 1}')
    shipper = env.shipper()
    real_scan = shipper.scan

    def scan_then_delete():
        found = real_scan()
        doomed.unlink()
        return found

    shipper.scan = scan_then_delete
    real_plan_entry = shipper._plan_entry

    def plan_entry_missing(relpath, path, stat, tracked, tombstones):
        if relpath == "z_handoff.json":
            raise FileNotFoundError(path)
        return real_plan_entry(relpath, path, stat, tracked, tombstones)

    shipper._plan_entry = plan_entry_missing
    result = shipper.cycle()
    assert result["shipped"] and "race" not in result
    members = json.loads(env.store.get(fmt.manifest_key("v1", 1)))["members"]
    assert [m["path"] for m in members] == ["a_live.jsonl"]


def test_lifecycle_pipeline_request_files_are_not_shipped(tmp_path):
    env = Env(tmp_path)
    env.store = VolumeStore(env.store_root)
    env.write("v3/lifecycle_worker/pipeline-request-abc123.json", b"{}")
    env.write("v3/lifecycle_worker/pipeline-result-abc123.json", b"{}")
    env.write("v3/lifecycle_worker/status.json", b"{}")
    env.write("v3/pipeline-request-abc123.json", b"{}")
    shipper = env.shipper()
    shipper.rules = shipper_mod.load_selection_rules()
    scanned = shipper.scan()
    assert "v3/lifecycle_worker/pipeline-request-abc123.json" not in scanned
    assert "v3/lifecycle_worker/pipeline-result-abc123.json" not in scanned
    assert {"v3/lifecycle_worker/status.json", "v3/pipeline-request-abc123.json"} <= set(scanned)


def test_volume_store_cap_default_leaves_the_free_floor_authoritative():
    assert shipper_mod.VOLUME_DEFAULT_MAX_STORE_BYTES == 10 * 1024 ** 3
    assert shipper_mod.VOLUME_DEFAULT_MIN_FREE_BYTES == 4 * 1024 ** 3


def test_head_reports_the_sleeping_mode_not_the_last_cycles(tmp_path):
    venv = VolumeEnv(tmp_path, max_segment_bytes=1000, backlog_boost_bytes=2000,
                     boost_segment_bytes=8000)
    try:
        _assert_head_reports_sleeping_mode(venv)
    finally:
        venv.close()


def test_main_loop_switches_priority_and_publishes_sleeping_after_the_cycle(tmp_path, monkeypatch):
    env = Env(tmp_path, max_segment_bytes=1000, backlog_boost_bytes=2000, boost_segment_bytes=8000)
    env.store = VolumeStore(env.store_root)
    for index in range(8):
        env.write(f"stream{index}.jsonl", _rows(0, 150, tag=str(index)))
    shipper = env.shipper()
    priorities, sleeps = [], []

    class _Stop(BaseException):
        pass

    def fake_sleep(seconds):
        sleeps.append(seconds)
        raise _Stop

    monkeypatch.setenv("RESEARCH_SEGMENTS_ENABLED", "1")
    monkeypatch.setenv("RESEARCH_SEGMENTS_MIN_FREE_BYTES", "1")
    monkeypatch.setattr(shipper_mod, "shipper_from_env", lambda *a, **k: shipper)
    monkeypatch.setattr(shipper_mod, "_set_priority",
                        lambda boosted, nice=10: priorities.append(boosted))
    monkeypatch.setattr(shipper_mod.time, "sleep", fake_sleep)
    with pytest.raises(_Stop):
        shipper_mod.main()
    # The first cycle ran idle and left a backlog; the switch happens before the sleep.
    assert priorities == [False, True]
    status = json.loads(shipper.status_path.read_text())
    assert status["worker_state"] == "SLEEPING" and status["priority"] == "boost"
    assert status["backlog_mode"] is True and status["segment_budget_bytes"] == 8000
    assert status["next_cycle_at"] == env.clock[0] + sleeps[0]


def _assert_head_reports_sleeping_mode(venv):
    for index in range(8):
        venv.write(f"stream{index}.jsonl", _rows(0, 150, tag=str(index)))
    shipper = venv.shipper()
    shipper.cycle()
    shipper.boosted = shipper.boost_due()
    assert shipper.boosted is True
    shipper.publish_mode("CYCLING", priority="boost", priority_error=None)
    while shipper.cycle()["shipped"]:
        pass
    # The drain cycle ran boosted; the sleeping worker has already dropped back.
    shipper.boosted = shipper.boost_due()
    shipper.publish_mode("SLEEPING", priority="idle", priority_error=None,
                         next_cycle_at=venv.clock[0] + 300)
    code, _, raw = venv.call("head")
    assert code == 200
    head = json.loads(raw)
    assert head["shipper_worker_state"] == "SLEEPING"
    assert head["shipper_next_cycle_at"] == venv.clock[0] + 300
    assert head["backlog_mode"] is False and head["shipper_priority"] == "idle"
    assert head["segment_budget_bytes"] == shipper.max_segment_bytes


# ------------------------------------------------------- backlog mode
def _raw_member_bytes(env, result) -> int:
    manifest = json.loads(env.store.get(fmt.manifest_key("v1", result["shipped"]["seq"])))
    return sum(member["size"] for member in manifest["members"])


def test_backlog_mode_ships_larger_segments_then_returns_to_regular_budget(tmp_path):
    env = Env(tmp_path, max_segment_bytes=1000, backlog_boost_bytes=2000, boost_segment_bytes=8000)
    env.store = VolumeStore(env.store_root)
    for index in range(8):
        env.write(f"stream{index}.jsonl", _rows(0, 150, tag=str(index)))
    shipper = env.shipper()
    first = shipper.cycle()
    assert first["backlog_mode"] is False and first["segment_budget_bytes"] == 1000
    assert _raw_member_bytes(env, first) <= 1000 and first["deferred_bytes"] > 2000

    boosted = shipper.cycle()
    assert boosted["backlog_mode"] is True and boosted["segment_budget_bytes"] == 8000
    assert 1000 < _raw_member_bytes(env, boosted) <= 8000
    status = json.loads(shipper.status_path.read_text())
    assert status["backlog_mode"] is True and status["segment_budget_bytes"] == 8000
    # A restarted worker resumes backlog mode from the published status.
    assert env.shipper().boost_due() is True

    while True:
        result = shipper.cycle()
        if not result["shipped"]:
            break
    assert result["deferred_bytes"] == 0 and result["backlog_mode"] is False
    assert json.loads(shipper.status_path.read_text())["backlog_mode"] is False
    env.write("stream0.jsonl", _rows(150, 5, tag="0"), append=True)
    small = shipper.cycle()
    assert small["shipped"] and small["segment_budget_bytes"] == 1000
    env.puller().pull_once()
    env.assert_tree_matches_source()


def test_backlog_mode_never_outgrows_the_store_cap(tmp_path):
    env = Env(tmp_path, max_segment_bytes=1000, backlog_boost_bytes=2000, boost_segment_bytes=8000,
              sink="volume", max_store_bytes=6000)
    env.store = VolumeStore(env.store_root)
    for index in range(8):
        env.write(f"stream{index}.jsonl", _rows(0, 60, tag=str(index)))
    shipper = env.shipper()
    first = shipper.cycle()
    assert first["deferred_bytes"] > 2000
    # store_bytes + one boosted segment would exceed the cap -> regular budget.
    second = shipper.cycle()
    assert second["backlog_mode"] is False and _raw_member_bytes(env, second) <= 1000


def test_backlog_mode_disabled_by_default(tmp_path):
    env = Env(tmp_path, max_segment_bytes=500)
    env.store = VolumeStore(env.store_root)
    env.write("a.jsonl", _rows(0, 200))
    shipper = env.shipper()
    shipper.cycle()
    assert shipper.boost_due() is False
    assert shipper.cycle()["segment_budget_bytes"] == 500


def test_env_enables_backlog_mode_with_bounded_defaults(tmp_path):
    shipper = shipper_mod.shipper_from_env({
        "BOT_DATA_DIR": str(tmp_path), "RESEARCH_SEGMENTS_SINK": "volume",
        "RESEARCH_SEGMENTS_STATE_DIR": str(tmp_path / "state"),
    })
    assert shipper.backlog_boost_bytes == shipper_mod.DEFAULT_BACKLOG_BOOST_BYTES == 8 * 1024 * 1024
    assert shipper.boost_segment_bytes == shipper_mod.DEFAULT_BOOST_SEGMENT_BYTES == 64 * 1024 * 1024
    assert shipper.boost_segment_bytes <= shipper.max_member_bytes


def test_priority_switches_between_idle_class_and_bounded_nice(monkeypatch):
    calls = []
    monkeypatch.setattr(shipper_mod.os, "SCHED_OTHER", 0, raising=False)
    monkeypatch.setattr(shipper_mod.os, "SCHED_IDLE", 5, raising=False)
    monkeypatch.setattr(shipper_mod.os, "PRIO_PROCESS", 0, raising=False)
    monkeypatch.setattr(shipper_mod.os, "sched_param", lambda priority: priority, raising=False)
    monkeypatch.setattr(shipper_mod.os, "sched_setscheduler",
                        lambda pid, policy, param: calls.append(("policy", policy)), raising=False)
    monkeypatch.setattr(shipper_mod.os, "setpriority",
                        lambda which, who, nice: calls.append(("nice", nice)), raising=False)
    assert shipper_mod._set_priority(True, 10) is None
    assert calls == [("policy", 0), ("nice", 10)]
    calls.clear()
    assert shipper_mod._set_priority(False) is None
    assert calls == [("nice", shipper_mod.IDLE_NICE), ("policy", 5)]

    def refuse(*_args):
        raise PermissionError("not permitted")

    monkeypatch.setattr(shipper_mod.os, "setpriority", refuse, raising=False)
    assert "PermissionError" in shipper_mod._set_priority(True, 10)


def test_scan_skips_excluded_dirs_state_dir_and_symlinks(tmp_path):
    env = Env(tmp_path)
    env.write("top.jsonl", b"{}\n")
    env.write("v3/ledgers/deep.jsonl", b"{}\n")
    env.write(".locks/held.json", b"{}")
    env.write("notes.bin", b"x")
    link = env.runtime / "alias.jsonl"
    try:
        link.symlink_to(env.runtime / "top.jsonl")
    except (OSError, NotImplementedError):
        link = None
    found = env.shipper().scan()
    assert sorted(found) == ["top.jsonl", "v3/ledgers/deep.jsonl"]
    path, stat = found["v3/ledgers/deep.jsonl"]
    assert path == env.runtime / "v3" / "ledgers" / "deep.jsonl" and stat.st_ino


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
def test_server_and_prune_hook_have_no_bot_lock_surface(name):
    source = (ROOT / name).read_text(encoding="utf-8")
    assert not _imports(ROOT / name) & {"bot", "flask", "btc_conservative_agent", "shutil", "ccxt"}
    assert "trade_lock" not in source and "state_lock" not in source
    assert "rmtree" not in source and "os.remove" not in source


def test_server_never_deletes_and_prune_deletes_only_in_execute():
    server = (ROOT / "research_segment_server.py").read_text(encoding="utf-8")
    for forbidden in (".unlink(", "os.rename", ".rename(", "os.replace"):
        assert forbidden not in server
    tree = ast.parse((ROOT / "research_segment_prune.py").read_text(encoding="utf-8"))
    owners = {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
              and ".unlink(" in ast.unparse(node)}
    assert owners == {"execute"}


def test_bot_mounts_segment_server_ahead_of_flask_with_reserved_workers():
    source = (ROOT / "bot.py").read_text(encoding="utf-8")
    assert "app=_mount_research_segment_server(app)" in source
    assert "_research_segment_thread_cap = threading.BoundedSemaphore(2)" in source
    assert 'request_path.startswith(self._research_segment_path_prefix)' in source
    assert '"research_segments", "RESEARCH_SEGMENTS"' in source
    dispatch = server_mod.mount(lambda environ, start: [b"flask"], {"BOT_ADMIN_TOKEN": TOKEN})
    assert dispatch({"PATH_INFO": "/health"}, lambda *a: None) == [b"flask"]


def test_fly_config_enables_volume_sink_with_dry_run_pruning_default():
    toml = (ROOT / "fly.toml").read_text(encoding="utf-8")
    assert 'RESEARCH_SEGMENTS_ENABLED = "1"' in toml and 'RESEARCH_SEGMENTS_SINK = "volume"' in toml
    assert 'RESEARCH_SEGMENTS_PRUNE_ENABLED = "1"' in toml
    assert 'RESEARCH_SEGMENTS_PRUNE_DEFAULT_MODE = "dry_run"' in toml
    assert "enforce" not in toml.lower()


# ------------------------------------------------------------- prune hook
