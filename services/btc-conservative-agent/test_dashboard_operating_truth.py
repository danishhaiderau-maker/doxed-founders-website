"""Fly dashboard operating truth: mode, revision, pause owner, disk, transfer, alarms.

bot.py cannot be imported in unit tests (exchange/WS side effects at import),
so the helpers are extracted by AST and executed against stubs.
"""
from __future__ import annotations

import ast
import importlib.util
import json
import shutil
import subprocess
from collections import namedtuple
from pathlib import Path

from combo_pathway_config import ACTIVE_TILE_ORDER, ACTIVE_TILE_REGISTRY, active_tile_lifecycle_manifest

AGENT = Path(__file__).resolve().parent
BOT_PATH = AGENT / "bot.py"
BOT_SOURCE = BOT_PATH.read_text(encoding="utf-8")
BOT_TREE = ast.parse(BOT_SOURCE)
HELPERS = ("_unavailable", "_dashboard_seq", "_dashboard_age_text", "_dashboard_transfer_truth",
           "_dashboard_operating_truth", "_public_dashboard_truth", "_dashboard_tile_view",
           "_dashboard_tile_offsets_text")
CONSTANTS = ("_DASHBOARD_DISK_ALARM_PCT", "_DASHBOARD_LEGACY_ACK_STALE_SEC",
             "_DASHBOARD_SEGMENT_STATUS_STALE_SEC", "_DASHBOARD_SEGMENT_SEQ_LAG")
NOW = 1_790_000_000.0
DAY = 86400.0
Usage = namedtuple("Usage", "total used free")
WAL_OK = {"available": True, "alarms": [], "incident_alarms": []}


def _top_level_source(names):
    parts = []
    for node in BOT_TREE.body:
        if isinstance(node, ast.FunctionDef) and node.name in names:
            parts.append(ast.get_source_segment(BOT_SOURCE, node))
        elif isinstance(node, ast.Assign) and any(getattr(t, "id", None) in names for t in node.targets):
            parts.append(ast.get_source_segment(BOT_SOURCE, node))
    return "\n\n".join(parts)


def _namespace(tmp_path, *, live_armed=False, used=40, transfer=None):
    fake_shutil = type("S", (), {"disk_usage": staticmethod(
        lambda _p: Usage(100 * 10**9, used * 10**9, (100 - used) * 10**9))})
    ns = {
        "shutil": fake_shutil, "Path": Path,
        "state": {"live_armed": live_armed},
        "_force_paper_mode_active": lambda: False,
        "_data_sync_volume_root": lambda: tmp_path,
        "_data_sync_bundle_public_status": lambda: {"coordinator": {"status": "IDLE"}},
        "_volume_transfer_snapshot": lambda _root, _now: dict(transfer or {}),
        "_format_melbourne_hm": lambda ts: f"T{int(float(ts))}",
        "active_tile_lifecycle_manifest": active_tile_lifecycle_manifest,
        "_PUBLIC_OWNER_ONLY_REASON": "owner-only field (public sanitized view)",
    }
    source = _top_level_source(set(HELPERS) | set(CONSTANTS))
    exec(compile("from __future__ import annotations\n" + source, "bot_helpers", "exec"), ns)
    return ns


def test_paper_disarmed_mode_revision_and_pause_owner(tmp_path):
    ns = _namespace(tmp_path, transfer={"segments_enabled": False, "legacy_ack_age_sec": 600.0})
    snap = {"source_git_rev": "abc1234", "execution_paused": True, "manual_admin_pause": True,
            "pause_owner": "OPERATOR"}
    op = ns["_dashboard_operating_truth"](snap, NOW, WAL_OK)
    assert op["mode"] == {"paper": True, "bitfinex_armed": False, "label": "PAPER \u2014 Bitfinex DISARMED"}
    assert op["revision"] == "abc1234"
    assert op["pause"]["label"] == "PAUSED (owner OPERATOR)"
    assert op["disk"]["label"] == "40.0% used, 60.00 GB free"
    assert op["alarms"] == []
    assert op["transfer"]["label"].startswith("last ACK T1789999400 (10 min ago)")


def test_missing_pause_state_is_not_reported_as_running(tmp_path):
    op = _namespace(tmp_path)["_dashboard_operating_truth"]({}, NOW, WAL_OK)
    assert op["pause"]["available"] is False and "not available" in op["pause"]["label"]
    assert op["revision"] is None


def test_armed_runtime_is_labelled_live_copy(tmp_path):
    op = _namespace(tmp_path, live_armed=True)["_dashboard_operating_truth"]({}, NOW, WAL_OK)
    assert op["mode"]["label"] == "LIVE COPY \u2014 Bitfinex ARMED"


def test_days_old_legacy_ack_is_critical_and_never_no_alarm(tmp_path):
    ns = _namespace(tmp_path, transfer={"segments_enabled": False, "legacy_ack_age_sec": 3.4 * DAY})
    op = ns["_dashboard_operating_truth"]({}, NOW, WAL_OK)
    label = op["transfer"]["label"]
    assert "(3.4 days ago) \u2014 CRITICAL lag" in label
    assert label.endswith("laptop-side failures: see analyzer")
    assert [a["code"] for a in op["alarms"]] == ["TRANSFER_ACK_LAG"]
    assert op["transfer"]["legacy_ack_state"] == "CRITICAL_LAG"


def test_missing_ack_is_critical(tmp_path):
    transfer, alarms = _namespace(tmp_path)["_dashboard_transfer_truth"]({"segments_enabled": False}, NOW)
    assert transfer["label"].startswith("no laptop ACK on record")
    assert [a["code"] for a in alarms] == ["TRANSFER_NO_ACK"]


def test_live_segments_use_shipper_rules_and_keep_ack_visible(tmp_path):
    truth = _namespace(tmp_path)["_dashboard_transfer_truth"]
    healthy, alarms = truth({"segments_enabled": True, "segment_status_present": True, "shipped_seq": 50,
                             "laptop_acked_seq": 44, "segment_status_age_sec": 120.0,
                             "legacy_ack_age_sec": 3 * DAY}, NOW, "IDLE")
    assert alarms == []
    assert "segment 50 shipped, laptop ACKed 44 (6 behind)" in healthy["label"]
    assert "CRITICAL lag" in healthy["label"] and "bundle producer IDLE" in healthy["label"]
    _, lagging = truth({"segments_enabled": True, "segment_status_present": True, "shipped_seq": 100,
                        "laptop_acked_seq": 10, "segment_status_age_sec": 3600.0}, NOW)
    assert [a["code"] for a in lagging] == ["TRANSFER_SEGMENTS_LAGGING"]
    assert "90 segments behind" in lagging[0]["detail"] and "60 min old" in lagging[0]["detail"]
    _, missing = truth({"segments_enabled": True, "segment_status_present": False}, NOW)
    assert [a["code"] for a in missing] == ["TRANSFER_SEGMENTS_UNAVAILABLE"]


def test_transfer_thresholds_match_the_fly_monitor(tmp_path):
    spec = importlib.util.spec_from_file_location("fly_monitor_rules", AGENT.parents[1] / "scripts" / "fly_monitor_rules.py")
    rules = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rules)
    ns = _namespace(tmp_path)
    assert ns["_DASHBOARD_LEGACY_ACK_STALE_SEC"] == rules.LEGACY_ACK_STALE_SEC
    assert ns["_DASHBOARD_SEGMENT_STATUS_STALE_SEC"] == rules.SEGMENT_STATUS_STALE_SEC
    assert ns["_DASHBOARD_SEGMENT_SEQ_LAG"] == rules.SEGMENT_SEQ_LAG


def test_disk_and_wal_alarms_are_visible(tmp_path):
    ns = _namespace(tmp_path, used=91, transfer={"segments_enabled": False, "legacy_ack_age_sec": 60.0})
    wal = {"available": True, "alarms": [{"code": "EMERGENCY_WAL_RESERVE_LOW", "explanation": "reserve low"}]}
    op = ns["_dashboard_operating_truth"]({}, NOW, wal)
    assert [a["code"] for a in op["alarms"]] == ["DISK_PRESSURE", "EMERGENCY_WAL_RESERVE_LOW"]
    missing = ns["_dashboard_operating_truth"]({}, NOW, {"available": False, "reason": "not published"})
    assert "EMERGENCY_WAL_TELEMETRY_UNAVAILABLE" in {a["code"] for a in missing["alarms"]}


def test_public_and_owner_views_share_one_operating_block(tmp_path):
    ns = _namespace(tmp_path)
    op = ns["_dashboard_operating_truth"]({"source_git_rev": "abc"}, NOW, WAL_OK)
    truth = {"schema": "dashboard_truth_v1", "fields": {}, "emergency_wal": WAL_OK, "operating": op}
    assert ns["_public_dashboard_truth"](truth)["operating"] is op
    build = next(n for n in BOT_TREE.body if isinstance(n, ast.FunctionDef) and n.name == "_build_dashboard_truth")
    assert "_dashboard_operating_truth" in ast.get_source_segment(BOT_SOURCE, build)


def _template(name: str) -> str:
    node = next(n for n in BOT_TREE.body if isinstance(n, ast.Assign)
                and any(getattr(t, "id", None) == name for t in n.targets))
    return ast.literal_eval(node.value)


def test_page_template_has_no_hard_coded_tile_roster():
    page = _template("HTML") + _template("DASHBOARD_JS")
    for lane in ACTIVE_TILE_ORDER:
        assert lane not in page, lane
        label = ACTIVE_TILE_REGISTRY[lane]["label"]
        assert label not in page, label
    for phrase in ("five family", "Five family", "Fixed 0.27%", "other four", "'Scenario C'"):
        assert phrase not in page, phrase
    assert "__TILE_ENTRY_OFFSETS__" in _template("HTML")
    assert "__TILE_REGISTRY_JSON__" in _template("DASHBOARD_JS")
    build = next(n for n in BOT_TREE.body if isinstance(n, ast.FunctionDef) and n.name == "build_dashboard_js")
    assert "_dashboard_tile_view()" in ast.get_source_segment(BOT_SOURCE, build)


def test_tile_view_and_offsets_come_from_the_registry(tmp_path):
    ns = _namespace(tmp_path)
    tiles = ns["_dashboard_tile_view"]()
    assert [t["lane"] for t in tiles] == list(ACTIVE_TILE_ORDER)
    assert [t["label"] for t in tiles] == [ACTIVE_TILE_REGISTRY[lane]["label"] for lane in ACTIVE_TILE_ORDER]
    text = ns["_dashboard_tile_offsets_text"](tiles)
    for tile in tiles:
        assert f"{tile['label']} {tile['offset_pct']:.2f}%" in text
    assert ns["_dashboard_tile_offsets_text"]([]) == "no registered tiles"


def test_lane_badge_and_strip_render_from_registry_payload(tmp_path):
    tiles = _namespace(tmp_path)["_dashboard_tile_view"]()
    page = _template("DASHBOARD_JS")
    badge_start = page.index("const TILE_REGISTRY_VIEW = ")
    badge_end = page.index("let executionControlsBusyUntil", badge_start)
    strip_start = page.index("function renderOperatingTruth(truth)")
    strip_end = page.index("async function refresh()", strip_start)
    script = """
const els = {};
function mk(){ return {textContent:'', style:{}, innerHTML:'', children:[], appendChild(c){ this.children.push(c); }}; }
const document = {getElementById: id => (els[id] = els[id] || mk()), createElement: () => mk()};
""" + page[badge_start:badge_end].replace("__TILE_REGISTRY_JSON__", json.dumps(tiles)) + page[strip_start:strip_end] + """
renderOperatingTruth({operating:{mode:{label:'PAPER \\u2014 Bitfinex DISARMED', bitfinex_armed:false},
  revision:'abc1234', pause:{label:'Execution running (no pause)'}, disk:{label:'40.0% used'},
  transfer:{label:'last ACK 26 Sep \\u2014 CRITICAL lag'}, alarms:[{code:'TRANSFER_ACK_LAG', severity:'critical', detail:'3.4 days'}]}});
console.log(JSON.stringify({
  badges: TILE_REGISTRY_VIEW.map(t => laneBadge(t.lane)),
  other: [laneBadge('CONTINUOUS'), laneBadge('UNKNOWN_LANE')],
  mode: els.operatingMode.textContent, details: els.operatingDetails.textContent,
  alarms: els.operatingAlarms.children.map(c => c.textContent)}));
"""
    result = subprocess.run([shutil.which("node"), "-"], input=script, capture_output=True, text=True,
                            encoding="utf-8", timeout=15, check=True)
    out = json.loads(result.stdout)
    for badge, tile in zip(out["badges"], tiles):
        assert f">{tile['label']}</span>" in badge
    assert "Continuous (analysis only)" in out["other"][0] and ">UNKNOWN_LANE</span>" in out["other"][1]
    assert out["mode"] == "PAPER \u2014 Bitfinex DISARMED"
    assert "Transfer last ACK 26 Sep \u2014 CRITICAL lag" in out["details"]
    assert out["alarms"] == ["CRITICAL TRANSFER_ACK_LAG: 3.4 days"]


def test_missing_operating_block_is_not_available_and_never_live_in_paper():
    start = BOT_SOURCE.index("function renderOperatingTruth(truth)")
    end = BOT_SOURCE.index("async function refresh()", start)
    script = """
const els = {};
function mk(){ return {textContent:'', style:{}, innerHTML:'', children:[], appendChild(c){ this.children.push(c); }}; }
const document = {getElementById: id => (els[id] = els[id] || mk()), createElement: () => mk()};
""" + BOT_SOURCE[start:end] + """
renderOperatingTruth({});
console.log(JSON.stringify(els.operatingMode.textContent));
"""
    result = subprocess.run([shutil.which("node"), "-"], input=script, capture_output=True, text=True,
                            encoding="utf-8", timeout=15, check=True)
    assert json.loads(result.stdout).startswith("Mode: not available")
    assert "'LIVE Python bot PID '" not in BOT_SOURCE
    assert "modeLabel + ' \u00b7 bot PID '" in BOT_SOURCE
    assert 'id="operatingTruth"' in BOT_SOURCE
