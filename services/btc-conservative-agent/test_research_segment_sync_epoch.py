"""HM (b): fresh-start epoch in manifest/keys, laptop beacon, RED sync verdict, wipe marker."""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

import research_segment_epoch as ep
import research_segment_format as fmt
import research_segment_server as server_mod
import research_segment_shipper as shipper_mod
from research_segment_store import VolumeStore
from test_research_segments import Env, _rows

TOKEN = "t0ken"
NOW = 1_792_130_000.0


def _stamp(volume: Path, ts: float = 1_792_126_000.0):
    (volume / "fresh_start_epoch.json").write_text(json.dumps({"epoch_ts": ts}))


def _beacon(**kw) -> bytes:
    doc = {"schema": ep.BEACON_SCHEMA, "applied_seq": 7073, "applied_prefix": "v1", "pull_exit": 0}
    doc.update(kw)
    return json.dumps(doc).encode()


def _call(app, route, *, method="GET", body=b"", prefix="v1"):
    out = {}

    def start(status, headers):
        out["status"] = int(status.split()[0])

    environ = {"REQUEST_METHOD": method, "PATH_INFO": f"/api/research-segments/{prefix}/{route}",
               "HTTP_X_BOT_ADMIN_TOKEN": TOKEN, "CONTENT_LENGTH": str(len(body)), "wsgi.input": io.BytesIO(body)}
    raw = b"".join(app(environ, start))
    return out["status"], json.loads(raw)


class VEnv(Env):
    def __init__(self, tmp_path, **kw):
        super().__init__(tmp_path, **kw)
        self.store_root = self.volume / "segment-store"
        self.store = VolumeStore(self.store_root)

    def app(self, **kw):
        return server_mod.SegmentServer(store_root=self.store_root, state_dir=self.state_dir, admin_token=TOKEN,
                                        volume_root=self.volume, clock=lambda: NOW, **kw)


# ----------------------------------------------------------------- pure verdict
def _v(**kw):
    base = dict(published_seq=2456, acked_seq=0, ack_received_at=None, beacon=None, beacon_received_at=NOW - 60,
                prefix="v3", fresh_start_epoch_id="", prefix_epoch_id="", reset_marker=None, now=NOW)
    base.update(kw)
    return ep.sync_verdict(**base)


def test_13_oct_state_is_red_with_both_reasons():
    v = _v(beacon={"schema": ep.BEACON_SCHEMA, "applied_seq": 7073, "pull_exit": 0})
    assert v["status"] == "RED" and v["alert"] is True
    joined = " ".join(v["reasons"])
    assert "SEQ_REGRESSION" in joined and "ACK_ZERO_WHILE_APPLIED" in joined


def test_healthy_state_is_green_and_lag_thresholds():
    ok = _v(published_seq=100, acked_seq=100, beacon={"applied_seq": 100})
    assert ok["status"] == "GREEN" and ok["reasons"] == []
    ack_at = lambda age: ep.time.strftime("%Y-%m-%dT%H:%M:%SZ", ep.time.gmtime(NOW - age))  # noqa: E731
    amber = _v(published_seq=100, acked_seq=90, ack_received_at=ack_at(6 * 60), beacon={"applied_seq": 100})
    red = _v(published_seq=100, acked_seq=90, ack_received_at=ack_at(20 * 60), beacon={"applied_seq": 100})
    fine = _v(published_seq=100, acked_seq=90, ack_received_at=ack_at(60), beacon={"applied_seq": 100})
    assert (amber["status"], red["status"], fine["status"]) == ("AMBER", "RED", "GREEN")


def test_no_beacon_is_unknown_not_alerting_and_stale_beacon_is_amber():
    assert _v(beacon=None)["status"] == "UNKNOWN" and _v(beacon=None)["alert"] is False
    stale = _v(published_seq=5, acked_seq=5, beacon={"applied_seq": 5}, beacon_received_at=NOW - 3600)
    assert stale["status"] == "AMBER" and "BEACON_STALE" in stale["reasons"][0]


def test_epoch_and_prefix_mismatch_are_red():
    v = _v(published_seq=10, acked_seq=10, fresh_start_epoch_id="fse-2", prefix_epoch_id="fse-2",
           beacon={"applied_seq": 10, "fresh_start_epoch_id": "fse-1", "applied_prefix": "v3"}, prefix="v3-w2")
    text = " ".join(v["reasons"])
    assert v["status"] == "RED" and "EPOCH_MISMATCH" in text and "PREFIX_MISMATCH" in text


def test_cycle_stop_reason_is_surfaced():
    v = _v(published_seq=9, acked_seq=9, beacon={"applied_seq": 9, "cycle_exit": 3,
                                                  "cycle_stop_reason": "PROMOTION_HEAD_KEPT_MOVING"})
    assert v["status"] == "AMBER" and v["laptop_cycle_stop_reason"] == "PROMOTION_HEAD_KEPT_MOVING"
    assert any("CYCLE_STOPPED:PROMOTION_HEAD_KEPT_MOVING" in r for r in v["reasons"])


def test_reset_marker_keeps_red_until_laptop_reports_the_new_epoch():
    marker = {"schema": ep.RESET_MARKER_SCHEMA, "status": "REQUIRED"}
    pending = _v(published_seq=5, acked_seq=5, beacon={"applied_seq": 5, "fresh_start_epoch_id": "fse-1"},
                 fresh_start_epoch_id="fse-2", prefix_epoch_id="fse-2", reset_marker=marker)
    assert pending["status"] == "RED" and any("LAPTOP_RESET_REQUIRED" in r for r in pending["reasons"])
    done = _v(published_seq=5, acked_seq=5, beacon={"applied_seq": 5, "fresh_start_epoch_id": "fse-2"},
              fresh_start_epoch_id="fse-2", prefix_epoch_id="fse-2", reset_marker=marker)
    assert done["status"] == "GREEN"
    no_stamp = _v(published_seq=5, acked_seq=5, beacon={"applied_seq": 5}, reset_marker=marker)
    assert no_stamp["status"] == "RED"


def test_rotation_pending_when_epoch_stamped_after_boot():
    v = _v(published_seq=5, acked_seq=5, beacon={"applied_seq": 5, "fresh_start_epoch_id": "fse-9"},
           fresh_start_epoch_id="fse-9", prefix_epoch_id="")
    assert v["status"] == "AMBER" and "EPOCH_ROTATION_PENDING" in v["reasons"][0]


# ----------------------------------------------------------------- prefix / manifest
def test_per_wipe_prefix_is_opt_in_and_valid(tmp_path):
    assert ep.resolve_prefix("v3", "fse-1792126000", per_wipe=False) == "v3"
    assert ep.resolve_prefix("v3", "", per_wipe=True) == "v3"
    assert ep.resolve_prefix("v3", "fse-1792126000", per_wipe=True) == "v3-w1792126000"
    assert ep.resolve_state_dir("/app/data/segment-shipper-v3", "fse-1792126000", per_wipe=True) == Path(
        "/app/data/segment-shipper-v3-w1792126000")
    assert ep.fresh_start_epoch_id(tmp_path) == ""
    _stamp(tmp_path)
    assert ep.fresh_start_epoch_id(tmp_path) == "fse-1792126000"


def test_shipper_from_env_uses_per_wipe_prefix_and_state_dir_only_when_enabled(tmp_path):
    _stamp(tmp_path)
    base = {"RESEARCH_SEGMENTS_SINK": "volume", "BOT_DATA_DIR": str(tmp_path), "RESEARCH_SEGMENTS_PREFIX": "v3",
            "RESEARCH_SEGMENTS_STATE_DIR": str(tmp_path / "segment-shipper-v3")}
    off = shipper_mod.shipper_from_env(base)
    assert off.prefix == "v3" and off.prefix_epoch_id == ""
    on = shipper_mod.shipper_from_env({**base, "RESEARCH_SEGMENTS_PREFIX_PER_WIPE": "1"})
    assert on.prefix == "v3-w1792126000" and on.prefix_epoch_id == "fse-1792126000"
    assert on.state_dir.name == "segment-shipper-v3-w1792126000"
    srv = server_mod.server_from_env({**base, "RESEARCH_SEGMENTS_PREFIX_PER_WIPE": "1", "BOT_ADMIN_TOKEN": TOKEN})
    assert srv.prefix == on.prefix and srv.state_dir == on.state_dir


def test_manifest_carries_fresh_start_epoch_only_when_stamped(tmp_path):
    env = VEnv(tmp_path)
    env.write("a.jsonl", _rows(0, 3))
    env.ship_all()
    m1 = json.loads(env.store.get(fmt.manifest_key("v1", 1)))
    assert "fresh_start_epoch_id" not in m1            # old format byte-identical before the first stamp
    _stamp(env.volume)
    env.write("a.jsonl", _rows(3, 3), append=True)
    env.ship_all()
    m2 = json.loads(env.store.get(fmt.manifest_key("v1", 2)))
    assert m2["fresh_start_epoch_id"] == "fse-1792126000"


# ----------------------------------------------------------------- server + shipper
def test_beacon_post_get_validation_and_head_verdict(tmp_path):
    env = VEnv(tmp_path)
    env.write("a.jsonl", _rows(0, 3))
    env.ship_all()
    app = env.app()
    st, head = _call(app, "head")
    assert st == 200 and head["sync_verdict"]["status"] == "UNKNOWN" and head["laptop_status"] is None
    assert _call(app, "laptop-status", method="POST", body=b"{bad")[0] == 400
    assert _call(app, "laptop-status", method="POST", body=_beacon(schema="x"))[0] == 400
    assert _call(app, "laptop-status", method="POST", body=b"x" * 5000)[0] == 413
    st, resp = _call(app, "laptop-status", method="POST",
                     body=_beacon(cycle_exit=3, cycle_stop_reason="PROMOTION_HEAD_KEPT_MOVING", junk="drop"))
    assert st == 200 and resp["sync_verdict"]["status"] == "RED"
    st, head = _call(app, "head")
    assert head["laptop_status"]["applied_seq"] == 7073 and "junk" not in head["laptop_status"]
    reasons = " ".join(head["sync_verdict"]["reasons"])
    assert "SEQ_REGRESSION" in reasons and "ACK_ZERO_WHILE_APPLIED" in reasons
    assert head["sync_verdict"]["laptop_cycle_stop_reason"] == "PROMOTION_HEAD_KEPT_MOVING"
    st, got = _call(app, "laptop-status")
    assert st == 200 and got["beacon"]["applied_seq"] == 7073


def test_unauthorized_beacon_post_is_refused(tmp_path):
    env = VEnv(tmp_path)
    app = server_mod.SegmentServer(store_root=env.store_root, state_dir=env.state_dir, admin_token="other",
                                   volume_root=env.volume)
    assert _call(app, "laptop-status", method="POST", body=_beacon())[0] in (401, 403)
    assert not (env.state_dir / "laptop-status.json").exists()


def test_shipper_status_publishes_the_verdict_for_health(tmp_path):
    env = VEnv(tmp_path)
    env.write("a.jsonl", _rows(0, 3))
    env.ship_all()
    _call(env.app(), "laptop-status", method="POST", body=_beacon())
    env.clock[0] = NOW
    env.shipper().poll_laptop_ack()
    status = json.loads((env.state_dir / "status.json").read_text())
    assert status["sync_verdict"] == "RED" and status["laptop_applied_seq"] == 7073
    assert any("SEQ_REGRESSION" in r for r in status["sync_verdict_reasons"])


def test_bot_transfer_truth_raises_critical_alarm_on_red():
    import bot
    snap = {"segments_enabled": True, "segment_status_present": True, "shipped_seq": 2456, "laptop_acked_seq": 0,
            "segment_status_age_sec": 5.0, "sync_verdict": "RED",
            "sync_verdict_reasons": ["SEQ_REGRESSION: laptop applied 7073 > Fly published 2456"]}
    _t, alarms = bot._dashboard_transfer_truth(snap, NOW)
    codes = {a["code"] for a in alarms}
    assert "TRANSFER_SYNC_INCONSISTENT" in codes
    assert "sync_verdict" in bot._SEGMENT_STATUS_FIELDS and "laptop_cycle_stop_reason" in bot._SEGMENT_STATUS_FIELDS
    snap["sync_verdict"] = "GREEN"
    assert "TRANSFER_SYNC_INCONSISTENT" not in {a["code"] for a in bot._dashboard_transfer_truth(snap, NOW)[1]}


def test_fly_monitor_rules_flags_red_verdict():
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
    import fly_monitor_rules as rules
    health = {"volume": {"transfer": {"segments_enabled": True, "segment_status_present": True, "shipped_seq": 5,
                                      "laptop_acked_seq": 5, "segment_status_age_sec": 3,
                                      "sync_verdict": "RED", "sync_verdict_reasons": ["ACK_ZERO_WHILE_APPLIED"]}}}
    assert "sync_inconsistent" in rules.transfer_findings(health)
    health["volume"]["transfer"]["sync_verdict"] = "GREEN"
    assert "sync_inconsistent" not in rules.transfer_findings(health)


# ----------------------------------------------------------------- wipe marker
def _plan(tmp_path):
    f = tmp_path / "x.log"
    f.write_text("hi")
    st = f.stat()
    return {"epoch_id": "ce-test", "plan_sha256": "ab" * 32, "by_root": {}, "kept_by_reason": {},
            "candidates": [{"path": str(f), "rel": "x.log", "bytes": st.st_size, "mtime_ns": st.st_mtime_ns,
                            "reason": "ARCHIVED_SEGMENT_PREFIX_test"}]}


def test_wipe_execute_writes_laptop_reset_marker_for_fly_only(tmp_path):
    import clean_epoch_wipe as cew
    fly = cew.execute(_plan(tmp_path), receipts_dir=tmp_path / "r-fly", scope="fly", cert_sha8="t")
    marker = ep.read_reset_marker(tmp_path / "r-fly")
    assert marker and marker["status"] == "REQUIRED" and marker["wipe_epoch_id"] == "ce-test"
    assert fly["laptop_reset_marker"].endswith(ep.RESET_MARKER_NAME)
    lap = cew.execute(_plan(tmp_path), receipts_dir=tmp_path / "r-lap", scope="laptop", cert_sha8="t")
    assert ep.read_reset_marker(tmp_path / "r-lap") is None and "laptop_reset_marker" not in lap
