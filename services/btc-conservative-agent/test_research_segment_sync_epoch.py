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


def _iso(ts):
    return ep.time.strftime("%Y-%m-%dT%H:%M:%SZ", ep.time.gmtime(ts))


def test_ack_zero_while_applied_is_amber_first_then_red_after_the_amber_window():
    fresh = _v(published_seq=100, acked_seq=0, beacon={"applied_seq": 100, "applied_at": _iso(NOW - 60)})
    assert fresh["status"] == "AMBER" and any("ACK_ZERO_WHILE_APPLIED" in r for r in fresh["reasons"])
    old = _v(published_seq=100, acked_seq=0,
             beacon={"applied_seq": 100, "applied_at": _iso(NOW - ep.ACK_LAG_AMBER_SEC - 5)})
    assert old["status"] == "RED"
    unknown_age = _v(published_seq=100, acked_seq=0, beacon={"applied_seq": 100})
    assert unknown_age["status"] == "AMBER"


def test_13_oct_state_is_red_with_both_reasons():
    v = _v(beacon={"schema": ep.BEACON_SCHEMA, "applied_seq": 7073, "pull_exit": 0, "applied_at": _iso(NOW - 3 * 86400)})
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


def test_no_beacon_is_red_and_stale_tiers_use_shared_amber10_red30():
    """Card 9fd17232b95b: missing → RED; AMBER@10m / RED@30m from shared constant family."""
    missing = _v(beacon=None)
    assert missing["status"] == "RED" and missing["alert"] is True
    assert "NO_LAPTOP_BEACON_NO_ACK" in missing["reason_codes"]
    # Shared family: RED = 3 × AMBER base
    assert ep.BEACON_STALE_AMBER_SEC == 10 * 60
    assert ep.BEACON_STALE_RED_SEC == 3 * ep.BEACON_STALE_AMBER_SEC
    assert ep.BEACON_STALE_SEC == ep.BEACON_STALE_RED_SEC
    amber = _v(published_seq=5, acked_seq=5, beacon={"applied_seq": 5},
               beacon_received_at=NOW - ep.BEACON_STALE_AMBER_SEC)
    assert amber["status"] == "AMBER" and "BEACON_STALE" in amber["reason_codes"]
    red = _v(published_seq=5, acked_seq=5, beacon={"applied_seq": 5},
             beacon_received_at=NOW - ep.BEACON_STALE_RED_SEC)
    assert red["status"] == "RED" and "BEACON_STALE" in red["reason_codes"]
    # Fresh (<10m) with matched seqs is not beacon-stale
    fresh = _v(published_seq=5, acked_seq=5, beacon={"applied_seq": 5},
               beacon_received_at=NOW - 60)
    assert fresh["status"] == "GREEN" and "BEACON_STALE" not in fresh["reason_codes"]


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


def _marker(**kw):
    m = {"schema": ep.RESET_MARKER_SCHEMA, "status": "REQUIRED", "created_at": NOW - 600, "plan_sha256": "ab" * 32,
         "wipe_epoch_id": "ce-2026", "prefix_before": "v3", "epoch_before": "fse-1", "published_at_wipe": 2456}
    m.update(kw)
    return m


def test_reset_marker_clears_only_on_a_post_wipe_beacon_with_different_prefix_or_epoch():
    kw = dict(published_seq=5, acked_seq=5, fresh_start_epoch_id="fse-2", prefix="v3-w2", prefix_epoch_id="fse-2")
    still = _v(beacon={"applied_seq": 5, "fresh_start_epoch_id": "fse-1", "applied_prefix": "v3"},
               beacon_received_at=NOW - 10, reset_marker=_marker(), **kw)
    assert still["status"] == "RED" and any("LAPTOP_RESET_REQUIRED" in r for r in still["reasons"])
    # a beacon from BEFORE the wipe can never clear it, even when it looks different
    old = _v(beacon={"applied_seq": 5, "fresh_start_epoch_id": "fse-2", "applied_prefix": "v3-w2"},
             beacon_received_at=NOW - 3600, reset_marker=_marker(), **kw)
    assert old["status"] == "RED"
    by_epoch = _v(beacon={"applied_seq": 5, "fresh_start_epoch_id": "fse-2", "applied_prefix": "v3-w2"},
                  beacon_received_at=NOW - 10, reset_marker=_marker(), **kw)
    assert by_epoch["status"] == "GREEN"
    by_prefix = _v(beacon={"applied_seq": 5, "applied_prefix": "v3-w2"}, beacon_received_at=NOW - 10,
                   reset_marker=_marker(epoch_before=""), **dict(kw, fresh_start_epoch_id=""))
    assert by_prefix["status"] == "GREEN"


def test_reset_marker_clears_by_reset_ack_and_never_compares_a_ce_id():
    kw = dict(published_seq=5, acked_seq=5, prefix="v3", fresh_start_epoch_id="", prefix_epoch_id="")
    marker = _marker(prefix_before="v3", epoch_before="")
    same = _v(beacon={"applied_seq": 5, "applied_prefix": "v3", "fresh_start_epoch_id": "ce-2026"},
              beacon_received_at=NOW - 10, reset_marker=marker, **kw)
    assert same["status"] == "RED"         # reporting the ce-... id proves nothing
    acked = _v(beacon={"applied_seq": 5, "applied_prefix": "v3", "reset_ack_plan_sha256": "ab" * 32},
               beacon_received_at=NOW - 10, reset_marker=marker, **kw)
    assert acked["status"] == "GREEN"
    ahead = _v(beacon={"applied_seq": 9, "applied_prefix": "v3", "reset_ack_plan_sha256": "ab" * 32},
               beacon_received_at=NOW - 10, reset_marker=marker, **kw)
    assert ahead["status"] == "RED"
    legacy = _v(beacon={"applied_seq": 5, "applied_prefix": "v4"}, beacon_received_at=NOW - 10,
                reset_marker={"schema": ep.RESET_MARKER_SCHEMA, "status": "REQUIRED", "created_at": NOW - 600}, **kw)
    assert any("LAPTOP_RESET_REQUIRED" in r for r in legacy["reasons"])


def test_rotation_pending_only_when_per_wipe_is_on_and_epoch_file_exists():
    on = _v(published_seq=5, acked_seq=5, beacon={"applied_seq": 5, "fresh_start_epoch_id": "fse-9"},
            fresh_start_epoch_id="fse-9", prefix_epoch_id="fse-8")
    assert on["status"] == "AMBER" and any("EPOCH_ROTATION_PENDING" in r for r in on["reasons"])
    off = _v(published_seq=5, acked_seq=5, beacon={"applied_seq": 5, "fresh_start_epoch_id": "fse-9"},
             fresh_start_epoch_id="fse-9", prefix_epoch_id="")
    assert off["status"] == "GREEN"                      # per-wipe disabled: silent
    absent = _v(published_seq=5, acked_seq=5, beacon={"applied_seq": 5}, fresh_start_epoch_id="",
                prefix_epoch_id="fse-8")
    assert absent["status"] == "GREEN"                   # epoch file absent: silent


def test_marker_not_cleared_while_fly_epoch_still_equals_epoch_before():
    kw = dict(published_seq=5, acked_seq=5, prefix="v3-w1", prefix_epoch_id="fse-1")
    beacon = {"applied_seq": 5, "applied_prefix": "v3-w1", "fresh_start_epoch_id": "fse-2"}
    stuck = _v(beacon=beacon, beacon_received_at=NOW - 10, reset_marker=_marker(prefix_before="v3-w0"),
               fresh_start_epoch_id="fse-1", **kw)          # Fly never re-stamped: still epoch_before
    assert any("LAPTOP_RESET_REQUIRED" in r for r in stuck["reasons"])
    rotated = _v(beacon=dict(beacon, fresh_start_epoch_id="fse-2"), beacon_received_at=NOW - 10,
                 reset_marker=_marker(prefix_before="v3-w0"), fresh_start_epoch_id="fse-2",
                 **dict(kw, prefix_epoch_id="fse-2"))
    assert not any("LAPTOP_RESET_REQUIRED" in r for r in rotated["reasons"])


def test_order_dependent_wipe_then_beacon_flow_is_self_contained(tmp_path):
    """wipe -> pre-wipe beacon (still RED) -> post-wipe beacon w/ new prefix+epoch (cleared); no shared state."""
    import clean_epoch_wipe as cew
    vol = tmp_path / "vol"
    (vol / "segment-shipper-w1").mkdir(parents=True)
    (vol / "segment-shipper-w1" / "status.json").write_text(json.dumps({"shipped_seq": 2456}))
    (vol / "fresh_start_epoch.json").write_text(json.dumps({"epoch_ts": 1}))
    receipts = vol / "runtime" / "clean-epoch-receipts"
    env = {"RESEARCH_SEGMENTS_PREFIX_PER_WIPE": "1", "RESEARCH_SEGMENTS_PREFIX": "v3"}
    t_wipe = ep.time.time()
    cew.execute(_plan(tmp_path), receipts_dir=receipts, scope="fly", cert_sha8="t", env=env)
    marker = ep.read_reset_marker(receipts)
    (vol / "fresh_start_epoch.json").write_text(json.dumps({"epoch_ts": 2}))        # bot re-stamps after the wipe
    kw = dict(published_seq=3, acked_seq=3, prefix="v3-w2", fresh_start_epoch_id=ep.fresh_start_epoch_id(vol),
              prefix_epoch_id="fse-2", reset_marker=marker, now=t_wipe + 600)
    old = ep.sync_verdict(ack_received_at=None, beacon={"applied_seq": 2456, "applied_prefix": "v3-w1",
                          "fresh_start_epoch_id": "fse-1"}, beacon_received_at=t_wipe - 60, **kw)
    assert any("LAPTOP_RESET_REQUIRED" in r for r in old["reasons"]) and old["status"] == "RED"
    new = ep.sync_verdict(ack_received_at=None, beacon={"applied_seq": 3, "applied_prefix": "v3-w2",
                          "fresh_start_epoch_id": "fse-2", "applied_at": ep.time.strftime("%Y-%m-%dT%H:%M:%SZ", ep.time.gmtime(t_wipe + 590))},
                          beacon_received_at=t_wipe + 595, **kw)
    assert not any("LAPTOP_RESET_REQUIRED" in r for r in new["reasons"])


def test_unstamped_laptop_is_amber_not_red():
    v = _v(published_seq=5, acked_seq=5, beacon={"applied_seq": 5}, fresh_start_epoch_id="fse-9")
    assert v["status"] == "AMBER" and any("EPOCH_UNREPORTED" in r for r in v["reasons"])


def test_beacon_and_marker_writers_use_unique_temp_names(tmp_path):
    names = []
    real = ep.os.replace
    ep.os.replace = lambda a, b: (names.append(Path(a).name), real(a, b))[1]
    try:
        for _ in range(3):
            ep.write_beacon_doc(tmp_path / "laptop-status.json", {"x": 1})
        ep.write_reset_marker(tmp_path, epoch_id="ce-1", plan_sha256="a", scope="fly")
    finally:
        ep.os.replace = real
    assert len(set(names)) == 4 and not any(n == "laptop-status.tmp" for n in names)
    assert not list(tmp_path.glob("*.tmp"))


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
    # Evaluator 1 (_sync_view /head): missing beacon → RED (R27), not UNKNOWN soft-OK
    assert st == 200 and head["sync_verdict"]["status"] == "RED" and head["laptop_status"] is None
    assert "NO_LAPTOP_BEACON_NO_ACK" in head["sync_verdict"]["reason_codes"]
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
    vol = tmp_path / "vol"
    (vol / "segment-shipper-w1").mkdir(parents=True)     # per-wipe state dir of the pre-wipe epoch
    (vol / "segment-shipper-w1" / "status.json").write_text(json.dumps({"shipped_seq": 2456}))
    (vol / "fresh_start_epoch.json").write_text(json.dumps({"epoch_ts": 1}))
    receipts = vol / "runtime" / "clean-epoch-receipts"
    env = {"RESEARCH_SEGMENTS_PREFIX_PER_WIPE": "1", "RESEARCH_SEGMENTS_PREFIX": "v3"}
    fly = cew.execute(_plan(tmp_path), receipts_dir=receipts, scope="fly", cert_sha8="t", env=env)
    marker = ep.read_reset_marker(receipts)
    assert marker and marker["status"] == "REQUIRED" and marker["wipe_epoch_id"] == "ce-test"
    assert (marker["prefix_before"], marker["epoch_before"], marker["published_at_wipe"]) == ("v3-w1", "fse-1", 2456)
    assert fly["laptop_reset_marker"].endswith(ep.RESET_MARKER_NAME)
    lap = cew.execute(_plan(tmp_path), receipts_dir=tmp_path / "r-lap", scope="laptop", cert_sha8="t", env=env)
    assert ep.read_reset_marker(tmp_path / "r-lap") is None and "laptop_reset_marker" not in lap


def test_fly_wipe_refuses_before_deleting_unless_per_wipe_is_on(tmp_path):
    import clean_epoch_wipe as cew
    plan = _plan(tmp_path)
    victim = Path(plan["candidates"][0]["path"])
    with pytest.raises(cew.WipeRefused, match="PER_WIPE_PREFIX_REQUIRED"):
        cew.execute(plan, receipts_dir=tmp_path / "r", scope="fly", cert_sha8="t", env={})
    assert victim.exists() and not (tmp_path / "r").exists()
    # laptop scope never needed the Fly flag
    cew.execute(plan, receipts_dir=tmp_path / "r-lap", scope="laptop", cert_sha8="t", env={})
    assert not victim.exists()


def test_promotion_denies_seq_regression_alone_and_cycle_does_not_retry_it():
    import research_segment_promotion as promo
    reasons = promo.deny_reasons({"schema": promo.STATE_SCHEMA, "applied_seq": 7073, "last_manifest_sha256": "x"},
                                 {"published_seq": 2456, "last_manifest_sha256": "y"}, {}, Path("/nonexistent"))
    assert len(reasons) == 1 and reasons[0].startswith("SEQ_REGRESSION")
    ps1 = (Path(__file__).resolve().parents[2] / "scripts" / "run-segment-analyzer-cycle.ps1").read_text("utf-8")
    assert "SEQ_" in ps1 and "SHADOW_BEHIND_PUBLISHED|HEAD_MANIFEST_MISMATCH" in ps1
    assert not any(tok in reasons[0] for tok in ("SHADOW_BEHIND_PUBLISHED", "HEAD_MANIFEST_MISMATCH"))


def _iso(ts):
    import datetime as _d
    return _d.datetime.fromtimestamp(ts, _d.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def test_no_beacon_but_fresh_ack_receipt_gives_applied_seq_and_coded_red_missing_beacon():
    v = _v(published_seq=622, acked_seq=622, ack_received_at=_iso(NOW - 90))
    assert v["status"] == "RED" and v["laptop_applied_seq"] == 622 and v["laptop_applied_source"] == "ACK_RECEIPT"
    assert v["reason_codes"] == ["NO_LAPTOP_BEACON"] and v["ack_age_sec"] == 90 and v["ack_lag_seq"] == 0


def test_no_beacon_stale_ack_receipt_while_behind_is_red_with_a_reason_and_ages():
    v = _v(published_seq=700, acked_seq=622, ack_received_at=_iso(NOW - 40 * 60))
    assert v["status"] == "RED" and "ACK_STALE" in v["reason_codes"] and v["ack_lag_seq"] == 78
    assert "NO_LAPTOP_BEACON" in v["reason_codes"]
    assert "78 behind" in " ".join(v["reasons"])


def test_no_beacon_and_no_ack_is_red_with_its_own_code_and_red_always_has_a_reason():
    v = _v(published_seq=5, acked_seq=0)
    assert v["status"] == "RED" and v["reason_codes"] == ["NO_LAPTOP_BEACON_NO_ACK"] and v["alert"] is True
    for kw in ({"published_seq": 1, "acked_seq": 0, "beacon": {"applied_seq": 9}}, ):
        r = _v(**kw)
        assert r["status"] != "RED" or r["reasons"]


# ----------------------------------------------------------------- card 9fd17232b95b dual evaluators + cadence
def test_beacon_staleness_enforced_on_server_sync_view_and_shipper_status_fields(tmp_path):
    """Both evaluators must see missing → RED and RED@30m from the shared constant (not CustodyLag)."""
    env = VEnv(tmp_path)
    env.write("a.jsonl", _rows(0, 3))
    env.ship_all()
    app = env.app()

    # Evaluator 1 — server _sync_view via /head (no beacon yet)
    st, head = _call(app, "head")
    assert st == 200 and head["sync_verdict"]["status"] == "RED"
    assert "NO_LAPTOP_BEACON_NO_ACK" in head["sync_verdict"]["reason_codes"]

    # Evaluator 2 — shipper sync_status_fields (no beacon doc)
    fields = env.shipper().sync_status_fields(0)
    assert fields["sync_verdict"] == "RED"
    assert any(r.startswith("NO_LAPTOP_BEACON") for r in fields["sync_verdict_reasons"])

    # Fresh beacon → both GREEN when seqs match
    body = _beacon(applied_seq=1, applied_prefix="v1")
    assert _call(app, "laptop-status", method="POST", body=body)[0] == 200
    # Align shipper clock with server NOW used by POST
    env.clock[0] = NOW
    st, head = _call(app, "head")
    # published may be 1 after one segment; applied 1 should not SEQ_REGRESS
    assert "NO_LAPTOP_BEACON" not in head["sync_verdict"]["reason_codes"]
    fields = env.shipper().sync_status_fields(1)
    assert "NO_LAPTOP_BEACON" not in "".join(fields.get("sync_verdict_reasons") or [])

    # Age the beacon to RED tier on disk; both evaluators must report BEACON_STALE RED
    doc_path = env.state_dir / "laptop-status.json"
    doc = json.loads(doc_path.read_text())
    doc["received_ts"] = NOW - ep.BEACON_STALE_RED_SEC
    doc_path.write_text(json.dumps(doc))
    st, head = _call(app, "head")
    assert head["sync_verdict"]["status"] == "RED"
    assert "BEACON_STALE" in head["sync_verdict"]["reason_codes"]
    fields = env.shipper().sync_status_fields(1)
    assert fields["sync_verdict"] == "RED"
    assert any("BEACON_STALE" in r for r in fields["sync_verdict_reasons"])


def test_beacon_amber_tier_on_both_evaluators_at_shared_10m(tmp_path):
    env = VEnv(tmp_path)
    env.write("a.jsonl", _rows(0, 3))
    env.ship_all()
    app = env.app()
    pub = int(json.loads((env.state_dir / "status.json").read_text()).get("shipped_seq") or 0)
    assert _call(app, "laptop-status", method="POST",
                 body=_beacon(applied_seq=pub, applied_prefix="v1"))[0] == 200
    env.clock[0] = NOW
    doc_path = env.state_dir / "laptop-status.json"
    doc = json.loads(doc_path.read_text())
    doc["received_ts"] = NOW - ep.BEACON_STALE_AMBER_SEC
    doc_path.write_text(json.dumps(doc))
    st, head = _call(app, "head")
    assert head["sync_verdict"]["status"] == "AMBER"
    assert "BEACON_STALE" in head["sync_verdict"]["reason_codes"]
    fields = env.shipper().sync_status_fields(pub)
    assert fields["sync_verdict"] == "AMBER"
    assert any("BEACON_STALE" in r for r in fields["sync_verdict_reasons"])


def test_beacon_post_cadence_default_at_most_300_seconds():
    """Repo pull-loop default ≤ BEACON_POST_CADENCE_MAX_SEC; shared constant binds the ceiling."""
    assert ep.BEACON_POST_CADENCE_DEFAULT_SEC <= ep.BEACON_POST_CADENCE_MAX_SEC == 300
    loop = (Path(__file__).resolve().parents[2] / "scripts" / "research-segment-pull-loop.ps1").read_text("utf-8")
    assert f"[int]$PullIntervalSec = {ep.BEACON_POST_CADENCE_DEFAULT_SEC}" in loop
    # Raising the binding ceiling above 300 would fail this contract
    assert ep.BEACON_POST_CADENCE_MAX_SEC <= 300


def test_mutation_soft_amber_missing_beacon_must_fail():
    """R23 mutation: mapping missing beacon to AMBER/GREEN must not satisfy the contract."""
    v = _v(beacon=None, published_seq=5, acked_seq=5, ack_received_at=_iso(NOW - 30))
    # Production path is RED. A soft-AMBER mutant would set status AMBER — prove we reject that.
    assert v["status"] == "RED" and "NO_LAPTOP_BEACON" in v["reason_codes"]
    soft = dict(v)
    soft["status"] = "AMBER"
    soft["alert"] = False
    assert soft["status"] != "RED"  # mutant shape
    # Contract gate: production verdict must stay RED (mutation proof against soft map)
    assert _v(beacon=None, published_seq=5, acked_seq=5, ack_received_at=_iso(NOW - 30))["status"] == "RED"


def test_mutation_soft_amber_only_at_30m_stale_must_fail():
    """R23 mutation: RED@30m soft-mapped to AMBER fails the shared-tier contract."""
    v = _v(published_seq=5, acked_seq=5, beacon={"applied_seq": 5},
           beacon_received_at=NOW - ep.BEACON_STALE_RED_SEC)
    assert v["status"] == "RED" and "BEACON_STALE" in v["reason_codes"]
    # Divergent hard-code of only AMBER at 30m would yield AMBER — production must not
    assert not (v["status"] == "AMBER" and "BEACON_STALE" in v["reason_codes"])
