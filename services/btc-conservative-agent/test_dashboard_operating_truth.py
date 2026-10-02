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
           "_dashboard_operating_truth", "_operating_pause_truth", "_public_dashboard_truth", "_dashboard_tile_view",
           "_dashboard_tile_offsets_text", "_dashboard_entry_rule")
CONSTANTS = ("_DASHBOARD_DISK_ALARM_PCT", "_DASHBOARD_SEGMENT_STATUS_STALE_SEC", "_DASHBOARD_SEGMENT_SEQ_LAG")
NOW = 1_790_000_000.0
DAY = 86400.0
Usage = namedtuple("Usage", "total used free")
WAL_OK = {"available": True, "alarms": [], "incident_alarms": []}
SEGMENTS_OK = {"segments_enabled": True, "segment_status_present": True, "shipped_seq": 50,
               "laptop_acked_seq": 44, "unshipped_bytes": 300_000, "segment_status_age_sec": 120.0}


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
        "_volume_transfer_snapshot": lambda _root, _now: dict(transfer or {}),
        "_format_melbourne_hm": lambda ts: f"T{int(float(ts))}",
        "active_tile_lifecycle_manifest": active_tile_lifecycle_manifest,
        "_PUBLIC_OWNER_ONLY_REASON": "owner-only field (public sanitized view)",
    }
    source = _top_level_source(set(HELPERS) | set(CONSTANTS))
    exec(compile("from __future__ import annotations\n" + source, "bot_helpers", "exec"), ns)
    return ns


def test_paper_disarmed_mode_revision_and_pause_owner(tmp_path):
    ns = _namespace(tmp_path, transfer=SEGMENTS_OK)
    snap = {"source_git_rev": "abc1234", "execution_paused": True, "manual_admin_pause": True,
            "pause_owner": "OPERATOR"}
    op = ns["_dashboard_operating_truth"](snap, NOW, WAL_OK)
    assert op["mode"] == {"paper": True, "bitfinex_armed": False, "label": "PAPER \u2014 Bitfinex DISARMED"}
    assert op["revision"] == "abc1234"
    assert op["pause"]["label"] == "PAUSED (owner OPERATOR)"
    assert op["disk"]["label"] == "40.0% used, 60.00 GB free"
    assert op["alarms"] == []
    assert op["transfer"]["label"].startswith("segment 50 published, laptop ACKed 44 (6 behind)")


def test_missing_pause_state_is_not_reported_as_running(tmp_path):
    op = _namespace(tmp_path)["_dashboard_operating_truth"]({}, NOW, WAL_OK)
    assert op["pause"]["available"] is False and "not available" in op["pause"]["label"]
    assert op["revision"] is None


def test_armed_runtime_is_labelled_live_copy(tmp_path):
    op = _namespace(tmp_path, live_armed=True)["_dashboard_operating_truth"]({}, NOW, WAL_OK)
    assert op["mode"]["label"] == "LIVE COPY \u2014 Bitfinex ARMED"


def test_disabled_segment_shipping_is_critical_and_never_no_alarm(tmp_path):
    ns = _namespace(tmp_path, transfer={"segments_enabled": False, "legacy_ack_age_sec": 3.4 * DAY})
    op = ns["_dashboard_operating_truth"]({}, NOW, WAL_OK)
    label = op["transfer"]["label"]
    assert label.startswith("segment shipping disabled")
    assert label.endswith("laptop-side failures: see analyzer")
    assert [a["code"] for a in op["alarms"]] == ["TRANSFER_SEGMENTS_DISABLED"]


def test_retired_legacy_ack_and_bundle_producer_never_reach_the_strip(tmp_path):
    transfer, alarms = _namespace(tmp_path)["_dashboard_transfer_truth"](
        {**SEGMENTS_OK, "legacy_ack_age_sec": 3 * DAY}, NOW)
    assert alarms == []
    for retired in ("ACK T", "CRITICAL lag", "bundle producer", "no laptop ACK"):
        assert retired not in transfer["label"], retired
    assert not {"legacy_ack_age_sec", "legacy_ack_state", "bundle_status"} & set(transfer)
    op_source = ast.get_source_segment(BOT_SOURCE, next(
        n for n in BOT_TREE.body if isinstance(n, ast.FunctionDef) and n.name == "_dashboard_operating_truth"))
    assert "_data_sync_bundle_public_status" not in op_source


def test_live_segments_use_shipper_rules(tmp_path):
    truth = _namespace(tmp_path)["_dashboard_transfer_truth"]
    healthy, alarms = truth(SEGMENTS_OK, NOW)
    assert alarms == []
    assert healthy["label"] == ("segment 50 published, laptop ACKed 44 (6 behind) \u00b7 0.3 MB unshipped"
                                " \u00b7 shipper updated 2 min ago \u00b7 laptop-side failures: see analyzer")
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
    assert ns["_DASHBOARD_SEGMENT_STATUS_STALE_SEC"] == rules.SEGMENT_STATUS_STALE_SEC
    assert ns["_DASHBOARD_SEGMENT_SEQ_LAG"] == rules.SEGMENT_SEQ_LAG


def test_disk_and_wal_alarms_are_visible(tmp_path):
    ns = _namespace(tmp_path, used=91, transfer=SEGMENTS_OK)
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
        assert _tile_entry_text(tile) in text
    assert ns["_dashboard_tile_offsets_text"]([]) == "no registered tiles"


def _tile_entry_text(tile, *, html=False):
    rule = tile.get("entry_rule")
    if rule:
        rule = rule.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;") if html else rule
        return f"{tile['label']} {rule}"
    return f"{tile['label']} {tile['offset_pct']:.2f}%"


def test_taker_tiles_publish_their_rule_not_a_zero_offset(tmp_path):
    ns = _namespace(tmp_path)
    tiles = ns["_dashboard_tile_view"]()
    for tile in tiles:
        entry = ACTIVE_TILE_REGISTRY[tile["lane"]]["entry_policy"]
        if entry.get("mode") != "TAKER_AT_SIGNAL":
            assert tile["entry_rule"] is None
            continue
        rule = tile["entry_rule"]
        assert f"cap {entry['taker_protection_bps']:g}bps, {entry['taker_ttl_sec']}s" in rule
        assert f"spread >{entry['max_spread_bps']:g}bps" in rule
        assert f"BBO >{entry['max_bbo_age_sec']:g}s old" in rule
        assert f"{tile['label']} 0.00%" not in ns["_dashboard_tile_offsets_text"]([tile])
    assert ns["_dashboard_entry_rule"]({"mode": "TAKER_AT_SIGNAL"}) == "taker-at-signal entry (rule not published)"
    assert ns["_dashboard_entry_rule"]({"mode": "ADAPTIVE_REGIME"}) is None
    assert ns["_dashboard_entry_rule"]({"offset_pct": 0.3}) is None


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
  transfer:{label:'segment 50 published, laptop ACKed 10 (40 behind)'}, alarms:[{code:'TRANSFER_SEGMENTS_LAGGING', severity:'critical', detail:'laptop ACK is 40 segments behind'}]}});
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
    assert "Continuous" not in out["other"][0] and ">CONTINUOUS</span>" in out["other"][0]
    assert ">UNKNOWN_LANE</span>" in out["other"][1]
    assert out["mode"] == "PAPER \u2014 Bitfinex DISARMED"
    assert "Transfer segment 50 published, laptop ACKed 10 (40 behind)" in out["details"]
    assert out["alarms"] == ["CRITICAL TRANSFER_SEGMENTS_LAGGING: laptop ACK is 40 segments behind"]


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


def _render_gate_panel(tiles, gates, runtime_state) -> str:
    page = _template("DASHBOARD_JS")
    badge = page[page.index("const TILE_REGISTRY_VIEW = "):page.index("let executionControlsBusyUntil")]
    panel = page[page.index("function renderUltimateGatePanel(gates, runtimeState)"):page.index("function renderAiBandGateStatus")]
    script = """
const els = {};
const document = {getElementById: id => (els[id] = els[id] || {innerHTML: ''})};
""" + badge.replace("__TILE_REGISTRY_JSON__", json.dumps(tiles)) + panel + f"""
renderUltimateGatePanel({json.dumps(gates)}, {json.dumps(runtime_state)});
console.log(JSON.stringify(els.ultimateGatePanel.innerHTML));
"""
    result = subprocess.run([shutil.which("node"), "-"], input=script, capture_output=True, text=True,
                            encoding="utf-8", timeout=15, check=True)
    return json.loads(result.stdout)


def test_gate_panel_never_claims_readiness_or_arming_it_cannot_see(tmp_path):
    tiles = _namespace(tmp_path)["_dashboard_tile_view"]()
    public = _render_gate_panel(tiles, {}, {})
    assert "PAPER ENTRIES:</strong> <span style=\"color:#8b949e;font-weight:700;\">not available in this view" in public
    assert "BITFINEX LIVE:</strong> <span style=\"color:#8b949e;font-weight:700;\">not available in this view" in public
    assert "NOT READY" not in public and "DISARMED" not in public
    owner = _render_gate_panel(tiles, {}, {"signal_generation_ready": True, "execution_paused": False,
                                            "live_armed": False, "bitfinex_live_enabled": False})
    assert ">ALLOWED<" in owner and "BLOCKED \u2014 DISARMED" in owner
    paused = _render_gate_panel(tiles, {}, {"signal_generation_ready": False, "execution_paused": True,
                                             "execution_reason": "ADMIN_MANUAL", "pause_owner": "DEPLOY_MAINTENANCE",
                                             "live_armed": False})
    assert "ADMIN_MANUAL (DEPLOY_MAINTENANCE)" in paused


def test_dashboard_trade_rows_keep_the_stop_evidence_inputs():
    ns = {"_enrich_melbourne_time_fields": dict}
    source = _top_level_source({"_DASHBOARD_TRADE_API_KEYS", "_slim_trade_for_dashboard"})
    exec(compile(source, "bot_helpers", "exec"), ns)
    row = ns["_slim_trade_for_dashboard"]({
        "trade_id": "ftf-1", "research_lane": "FAMILY_TREND_FADE_60",
        "pnl_accounting_schema": "terminal_single_count_v1", "margin_usdt": 0.2, "leverage": 100,
        "features_velocity": 1.0,
    })
    assert row["pnl_accounting_schema"] == "terminal_single_count_v1"
    assert row["margin_usdt"] == 0.2 and row["leverage"] == 100
    assert "features_velocity" not in row


def test_gate_panel_entry_offsets_come_from_the_registry_not_a_fixed_anchor(tmp_path):
    tiles = _namespace(tmp_path)["_dashboard_tile_view"]()
    html = _render_gate_panel(tiles, {"entry_limit_policy": "deterministic_0.1pct_offset_v1"}, {})
    assert "0.1% offset" not in html and "deterministic_0.1pct" not in html
    for tile in tiles:
        assert _tile_entry_text(tile, html=True) in html
