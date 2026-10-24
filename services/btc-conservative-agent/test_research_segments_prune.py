"""Custody-gated Fly pruning (retired under R5, HM C4): plan library, custody route, retired mode route."""

from __future__ import annotations

import ast
import json
import os

import pytest

import research_segment_format as fmt
import research_segment_prune as prune_mod
from test_research_segments import _rows
from test_research_segments_volume import VolumeEnv, _ack_body


@pytest.fixture()
def venv(tmp_path):
    env = VolumeEnv(tmp_path)
    yield env
    env.close()


def _custody(venv, seq: int, verified: dict, **overrides) -> dict:
    receipt = {"schema": prune_mod.CUSTODY_SCHEMA, "prefix": "v1", "through_seq": seq,
               "manifest_sha256": fmt.sha256_bytes(venv.store.get(fmt.manifest_key("v1", seq))),
               "acked_seq": seq, "parity_seq": seq, "parity_verdict": "GREEN",
               "analyzer_consumed_seq": seq, "analysis_snapshot_id": "snap",
               "analysis_snapshot_receipt_sha256": "0" * 64, "verified_files": verified}
    receipt.update(overrides)
    return receipt


def _post(venv, receipt: dict):
    status, _headers, raw = venv.call("custody", method="POST", body=json.dumps(receipt).encode(),
                                      headers={"Content-Type": "application/json"})
    return status, json.loads(raw)


def _ship_two_acks(venv):
    rotated = venv.write("signal_replay.jsonl.1", _rows(0, 20))
    venv.write("signal_replay.jsonl", _rows(20, 2))
    protected = venv.write("bitfinex_relay_audit.jsonl.1", _rows(0, 3))
    venv.ship_all()
    venv.server_app.record_ack(_ack_body(1, venv.store.get(fmt.manifest_key("v1", 1))))
    venv.write("signal_replay.jsonl", _rows(22, 2), append=True)
    venv.ship_all()
    venv.server_app.record_ack(_ack_body(2, venv.store.get(fmt.manifest_key("v1", 2))))
    return rotated, protected


def _volume_shipper(venv):
    shipper = venv.shipper()
    shipper.sink = "volume"
    return shipper


def _age_segments(venv, seconds: float) -> None:
    for path in (venv.store_root / "v1" / "seg").glob("*.tar.gz"):
        os.utime(path, (venv.clock[0] - seconds, venv.clock[0] - seconds))


def test_plan_requires_custody(venv):
    rotated, _protected = _ship_two_acks(venv)
    shipper = venv.shipper()
    plan = prune_mod.plan_prune(shipper_state=shipper.load_state(), universe=shipper.scan(),
                                store_root=venv.store_root, prefix="v1", custody=None,
                                now=rotated.stat().st_mtime + 10 * 86400)
    assert plan["allowed"] is False and plan["candidates"] == []
    assert "NO_LAPTOP_CUSTODY_RECEIPT" in plan["deny_reasons"]


def test_plan_requires_two_proven_ack_cycles(venv):
    venv.write("signal_replay.jsonl.1", _rows(0, 5))
    venv.ship_all()
    venv.server_app.record_ack(_ack_body(1, venv.store.get(fmt.manifest_key("v1", 1))))
    shipper = venv.shipper()
    plan = prune_mod.plan_prune(shipper_state=shipper.load_state(), universe=shipper.scan(),
                                store_root=venv.store_root, prefix="v1", custody=_custody(venv, 1, {}),
                                now=10 ** 10)
    assert "INSUFFICIENT_PROVEN_ACK_CYCLES" in plan["deny_reasons"]


def test_custody_route_validates_and_is_write_once(venv):
    _ship_two_acks(venv)
    sha = fmt.sha256_bytes(_rows(0, 20))
    status, body = _post(venv, _custody(venv, 2, {"signal_replay.jsonl.1": sha}, parity_verdict="RED"))
    assert status == 409 and body["error"] == "CUSTODY_PARITY_NOT_GREEN"
    status, body = _post(venv, _custody(venv, 2, {}, manifest_sha256="f" * 64))
    assert status == 409 and body["error"] == "CUSTODY_MANIFEST_MISMATCH"
    status, body = _post(venv, _custody(venv, 2, {}, analyzer_consumed_seq=1))
    assert status == 409 and body["error"] == "CUSTODY_ANALYZER_CONSUMED_SEQ_BELOW_THROUGH"
    status, body = _post(venv, _custody(venv, 2, {"nested/x.jsonl.1": sha}))
    assert status == 409 and body["error"] == "CUSTODY_VERIFIED_FILES_INVALID"
    status, body = _post(venv, _custody(venv, 2, {"signal_replay.jsonl.1": sha}))
    assert status == 201 and body["verified_files"] == 1
    status, body = _post(venv, {**_custody(venv, 2, {}), "issued_at": "later"})
    assert status == 200 and body["result"] == "ALREADY_RECORDED"
    status, body = _post(venv, _custody(venv, 1, {}))
    assert status == 409 and body["error"] == "CUSTODY_REGRESSION"
    status, _h, raw = venv.call("custody")
    latest = json.loads(raw)
    assert status == 200 and latest["through_seq"] == 2 and latest["verified_files"] == 1


def test_custody_ahead_of_ack_is_refused(venv):
    _ship_two_acks(venv)
    venv.write("signal_replay.jsonl", _rows(24, 2), append=True)
    venv.ship_all()
    status, body = _post(venv, _custody(venv, 3, {}))
    assert status == 409 and body["error"] == "CUSTODY_AHEAD_OF_ACK"


def test_c4_prune_mode_route_is_retired_409(venv, monkeypatch):
    for env in (None, "1"):
        if env is None:
            monkeypatch.delenv("RESEARCH_SEGMENTS_PRUNE_ENABLED", raising=False)
        else:
            monkeypatch.setenv("RESEARCH_SEGMENTS_PRUNE_ENABLED", env)
        status, _h, raw = venv.call("prune-mode")
        assert status == 200 and json.loads(raw)["mode"] == "off"
        status, _h, raw = venv.call("prune-mode", method="POST", body=b'{"mode": "enforce"}')
        assert status == 409 and json.loads(raw)["error"] == "CUSTODY_PRUNE_RETIRED_R5"
        assert json.loads(raw)["mode"] == "off"
    status, _h, _raw = venv.call("prune-mode", token="wrong")
    assert status == 401


def test_c4_mode_is_always_off_and_cannot_be_written(tmp_path):
    assert prune_mod.CUSTODY_PRUNE_RETIRED is True
    assert prune_mod.read_mode(tmp_path, {"RESEARCH_SEGMENTS_PRUNE_ENABLED": "1"})[0] == "off"
    assert prune_mod.read_mode(tmp_path, {})[0] == "off"
    with pytest.raises(PermissionError):
        prune_mod.write_mode(tmp_path, "enforce")


def test_c4_shipper_has_no_custody_prune_pass(venv):
    shipper = _volume_shipper(venv)
    assert not hasattr(shipper, "prune_pass")
    assert shipper.prune_mode() == "off"


def test_unverified_young_newest_and_changed_rotations_are_kept(venv):
    rotated, _protected = _ship_two_acks(venv)
    shipper = venv.shipper()
    old = rotated.stat().st_mtime + 2 * 86400

    def plan(custody, now):
        return prune_mod.plan_prune(shipper_state=shipper.load_state(), universe=shipper.scan(),
                                    store_root=venv.store_root, prefix="v1", custody=custody, now=now)

    custody = _custody(venv, 2, {"signal_replay.jsonl.1": fmt.sha256_bytes(_rows(0, 20))})
    kept = plan(custody, old)
    assert kept["runtime"] == [] and kept["runtime_skipped"]["NEWEST_KEPT"] >= 1
    original_keep = prune_mod.TIER_B_KEEP_LATEST
    prune_mod.TIER_B_KEEP_LATEST = 0
    try:
        assert plan({**custody, "verified_files": {}}, old)["runtime_skipped"]["NOT_LAPTOP_HASH_VERIFIED"] == 1
        young = plan(custody, rotated.stat().st_mtime + 3600)
        assert young["runtime"] == [] and young["segments"] == []
        ready = plan(custody, old)
        assert [item["relpath"] for item in ready["runtime"]] == ["signal_replay.jsonl.1"]
        assert "bitfinex_relay_audit.jsonl.1" not in [i["relpath"] for i in ready["runtime"]]
    finally:
        prune_mod.TIER_B_KEEP_LATEST = original_keep
    with rotated.open("ab") as handle:
        handle.write(b'{"late":1}\n')
    result = prune_mod.execute({**ready, "segments": []}, runtime_root=venv.runtime)
    assert result["skipped_changed"] == 1 and rotated.exists()


def test_tape_rotations_outlive_the_restart_minute_bar_window(venv, monkeypatch):
    monkeypatch.setattr(prune_mod, "OTHER_KEEP_LATEST", 0)
    tape = venv.write("market_microstructure_1s.jsonl.1", _rows(0, 20))
    venv.write("market_microstructure_1s.jsonl", _rows(20, 2))
    venv.ship_all()
    venv.server_app.record_ack(_ack_body(1, venv.store.get(fmt.manifest_key("v1", 1))))
    venv.write("market_microstructure_1s.jsonl", _rows(22, 2), append=True)
    venv.ship_all()
    venv.server_app.record_ack(_ack_body(2, venv.store.get(fmt.manifest_key("v1", 2))))
    shipper = venv.shipper()
    custody = _custody(venv, 2, {"market_microstructure_1s.jsonl.1": fmt.sha256_bytes(_rows(0, 20))})

    def plan(age_days):
        return prune_mod.plan_prune(shipper_state=shipper.load_state(), universe=shipper.scan(),
                                    store_root=venv.store_root, prefix="v1", custody=custody,
                                    now=tape.stat().st_mtime + age_days * 86400)

    window_days = prune_mod.TAPE_STORE_RETENTION_MINUTES / 1440.0
    assert window_days >= 14
    held = plan(window_days)
    assert held["runtime"] == [] and held["runtime_skipped"]["TOO_YOUNG"] == 1
    assert [item["relpath"] for item in plan(window_days + 2)["runtime"]] == ["market_microstructure_1s.jsonl.1"]


def test_shipper_calls_prune_only_between_cycles():
    source = open(os.path.join(os.path.dirname(__file__), "research_segment_shipper.py"),
                  encoding="utf-8").read()
    tree = ast.parse(source)
    cycle = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "cycle")
    assert "prune" not in ast.unparse(cycle)
