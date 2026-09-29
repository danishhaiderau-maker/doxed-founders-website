"""Fly dashboard operating truth: mode, revision, pause owner, disk, transfer, alarms.

bot.py cannot be imported in unit tests (exchange/WS side effects at import),
so the helpers are extracted by AST and executed against stubs.
"""
from __future__ import annotations

import ast
import json
import os
import shutil
import subprocess
from collections import namedtuple
from pathlib import Path

BOT_PATH = Path(__file__).with_name("bot.py")
BOT_SOURCE = BOT_PATH.read_text(encoding="utf-8")
BOT_TREE = ast.parse(BOT_SOURCE)
HELPERS = ("_unavailable", "_dashboard_seq", "_dashboard_segment_shipper_status",
           "_dashboard_operating_truth", "_public_dashboard_truth")
NOW = 1_790_000_000.0
Usage = namedtuple("Usage", "total used free")


def _namespace(tmp_path, *, live_armed=False, used=40, env=None):
    source = "\n\n".join(
        ast.get_source_segment(BOT_SOURCE, node) for node in BOT_TREE.body
        if isinstance(node, ast.FunctionDef) and node.name in HELPERS
    )
    fake_shutil = type("S", (), {"disk_usage": staticmethod(lambda _p: Usage(100 * 10**9, used * 10**9, (100 - used) * 10**9))})
    fake_os = type("O", (), {"getenv": staticmethod(lambda key, default=None: (env or {}).get(key, default))})
    ns = {
        "os": fake_os, "json": json, "shutil": fake_shutil, "Path": Path,
        "state": {"live_armed": live_armed},
        "_force_paper_mode_active": lambda: False,
        "_data_sync_volume_root": lambda: tmp_path,
        "_data_sync_bundle_public_status": lambda: {"coordinator": {"status": "IDLE"}},
        "_format_melbourne_hm": lambda ts: f"T{int(float(ts))}",
        "_DASHBOARD_DISK_ALARM_PCT": 85.0, "_DASHBOARD_TRANSFER_STALE_SEC": 3600.0,
        "_PUBLIC_OWNER_ONLY_REASON": "owner-only field (public sanitized view)",
    }
    exec(compile("from __future__ import annotations\n" + source, "bot_helpers", "exec"), ns)
    return ns


WAL_OK = {"available": True, "alarms": [], "incident_alarms": []}


def test_paper_disarmed_mode_revision_and_pause_owner(tmp_path):
    ns = _namespace(tmp_path)
    snap = {"source_git_rev": "abc1234", "execution_paused": True, "manual_admin_pause": True,
            "pause_owner": "OPERATOR"}
    op = ns["_dashboard_operating_truth"](snap, NOW, WAL_OK)
    assert op["mode"] == {"paper": True, "bitfinex_armed": False, "label": "PAPER \u2014 Bitfinex DISARMED"}
    assert op["revision"] == "abc1234"
    assert op["pause"]["label"] == "PAUSED (owner OPERATOR)"
    assert op["disk"]["label"] == "40.0% used, 60.00 GB free"
    assert op["alarms"] == []


def test_missing_pause_state_is_not_reported_as_running(tmp_path):
    op = _namespace(tmp_path)["_dashboard_operating_truth"]({}, NOW, WAL_OK)
    assert op["pause"]["available"] is False and "not available" in op["pause"]["label"]
    assert op["revision"] is None


def test_armed_runtime_is_labelled_live_copy(tmp_path):
    op = _namespace(tmp_path, live_armed=True)["_dashboard_operating_truth"]({}, NOW, WAL_OK)
    assert op["mode"]["label"] == "LIVE COPY \u2014 Bitfinex ARMED"


def test_disk_wal_and_transfer_alarms_are_visible(tmp_path):
    shipper = tmp_path / "segment-shipper"
    shipper.mkdir()
    (shipper / "status.json").write_text(json.dumps(
        {"shipped_seq": 50, "laptop_acked_seq": 44, "updated_at": NOW - 7200}), encoding="utf-8")
    ns = _namespace(tmp_path, used=91, env={"RESEARCH_SEGMENTS_ENABLED": "1"})
    wal = {"available": True, "alarms": [{"code": "EMERGENCY_WAL_RESERVE_LOW", "explanation": "reserve low"}]}
    op = ns["_dashboard_operating_truth"]({}, NOW, wal)
    codes = [a["code"] for a in op["alarms"]]
    assert codes == ["DISK_PRESSURE", "TRANSFER_STALLED", "EMERGENCY_WAL_RESERVE_LOW"]
    assert op["transfer"]["unacked_segments"] == 6
    assert "stale since" in op["transfer"]["label"]


def test_missing_telemetry_is_no_data_not_zero(tmp_path):
    ns = _namespace(tmp_path, env={"RESEARCH_SEGMENTS_ENABLED": "1"})
    op = ns["_dashboard_operating_truth"]({}, NOW, {"available": False, "reason": "not published"})
    assert op["transfer"]["label"].startswith("no data yet")
    codes = {a["code"] for a in op["alarms"]}
    assert {"TRANSFER_STATUS_UNAVAILABLE", "EMERGENCY_WAL_TELEMETRY_UNAVAILABLE"} <= codes
    disabled = _namespace(tmp_path)["_dashboard_operating_truth"]({}, NOW, WAL_OK)
    assert disabled["transfer"]["label"].startswith("segment shipping disabled")


def test_public_and_owner_views_share_one_operating_block(tmp_path):
    ns = _namespace(tmp_path)
    op = ns["_dashboard_operating_truth"]({"source_git_rev": "abc"}, NOW, WAL_OK)
    truth = {"schema": "dashboard_truth_v1", "fields": {}, "emergency_wal": WAL_OK, "operating": op}
    assert ns["_public_dashboard_truth"](truth)["operating"] is op
    build = next(n for n in BOT_TREE.body if isinstance(n, ast.FunctionDef) and n.name == "_build_dashboard_truth")
    assert "_dashboard_operating_truth" in ast.get_source_segment(BOT_SOURCE, build)


def test_rendered_strip_and_banner_never_claim_live_in_paper():
    start = BOT_SOURCE.index("function renderOperatingTruth(truth)")
    end = BOT_SOURCE.index("async function refresh()", start)
    script = """
const els = {};
function mk(){ return {textContent:'', style:{}, innerHTML:'', children:[], appendChild(c){ this.children.push(c); }}; }
const document = {getElementById: id => (els[id] = els[id] || mk()), createElement: () => mk()};
""" + BOT_SOURCE[start:end] + """
renderOperatingTruth({operating:{mode:{label:'PAPER \\u2014 Bitfinex DISARMED', bitfinex_armed:false},
  revision:'abc1234', pause:{label:'Execution running (no pause)'}, disk:{label:'40.0% used'},
  transfer:{label:'no data yet'}, alarms:[{code:'DISK_PRESSURE', severity:'critical', detail:'91%'}]}});
const out1 = {mode: els.operatingMode.textContent, details: els.operatingDetails.textContent,
  alarms: els.operatingAlarms.children.map(c => c.textContent)};
renderOperatingTruth({});
console.log(JSON.stringify([out1, els.operatingMode.textContent]));
"""
    result = subprocess.run([shutil.which("node"), "-"], input=script, capture_output=True, text=True,
                            encoding="utf-8", timeout=15, check=True)
    rendered, missing = json.loads(result.stdout)
    assert rendered["mode"] == "PAPER \u2014 Bitfinex DISARMED"
    assert "Revision abc1234" in rendered["details"] and "Transfer no data yet" in rendered["details"]
    assert rendered["alarms"] == ["CRITICAL DISK_PRESSURE: 91%"]
    assert missing.startswith("Mode: not available")
    assert "'LIVE Python bot PID '" not in BOT_SOURCE
    assert "modeLabel + ' \u00b7 bot PID '" in BOT_SOURCE
    assert 'id="operatingTruth"' in BOT_SOURCE
