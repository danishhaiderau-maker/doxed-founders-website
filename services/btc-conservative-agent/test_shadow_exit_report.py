"""Shadow-exit analyzer: cohort separation, stats, market-context join, backfill, dashboard route and loader."""
import json
import shutil
import subprocess

import pytest

import combo_pathway_config as registry
import shadow_exit_paths as sxp
from research import shadow_exit_backfill as backfill
from research import shadow_exit_report as report
from test_shadow_exit_paths import ENTRY, SET, T0, _replay_row, _ticks


def _record(i, source, pnls, lane="FAMILY_COMMITTED_FADE_TAKER_90"):
    ticks = _ticks(pnls, start=T0 + 3600 * i)
    return sxp.build_record(source=source, trade_id=f"ntt-{source[:3]}-{i}", direction="LONG", ticks=ticks,
                            signal_ts=T0 + 3600 * i - 2, fill_ts=T0 + 3600 * i, entry_price=ENTRY,
                            shadow_set=SET, research_lane=lane, exit_ts=T0 + 3600 * i + 6, atr_pct=0.1,
                            horizon_sec=60)


def _write(path, rows):
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


@pytest.fixture
def stores(tmp_path):
    mirror, bf = tmp_path / "mirror", tmp_path / "backfill"
    mirror.mkdir()
    bf.mkdir()
    winner = [0, 10, 22, 26, 18, 8, 2, -3] + [-3] * 60
    loser = [0, -4, -8, -13, -15] + [-30] * 60
    live = [_record(i, sxp.SOURCE_RUNTIME, winner if i % 2 else loser) for i in range(6)]
    _write(mirror / "shadow_exit_paths.jsonl", live[:4])
    _write(mirror / "shadow_exit_paths.jsonl.1", live[3:])
    archived = [_record(i, sxp.SOURCE_BACKFILL_REPLAY, winner) for i in range(3)]
    smuggled = _record(9, sxp.SOURCE_BACKFILL_REPLAY, winner)
    _write(bf / "signal_replay_backfill.jsonl", archived)
    _write(mirror / "shadow_exit_paths.jsonl.2", [smuggled])
    mc = [{"minute_ts": int(T0 // 60) * 60 + 60 * m,
           "derivatives": {"bitfinex": {"status": "OK", "funding_rate": 1e-4, "oi_btc": 100.0}},
           "liquidations": {}, "regime": {"rank_pct": 80.0, "label": "NORMAL", "rv15_bps": 5.0}}
          for m in range(0, 6 * 60 + 5)]
    _write(mirror / "market_context_1m.jsonl", mc)
    return mirror, bf


def test_report_separates_live_headline_from_backfill_archive(stores):
    mirror, bf = stores
    out = report.build_report(mirror, bf)
    assert out["schema"] == report.SCHEMA and out["observation_only"] is True
    live = out["cohorts"][report.COHORT_LIVE]
    archive = out["cohorts"][report.COHORT_BACKFILL]
    assert live["records"] == 6 and live["role"] == "HEADLINE"
    assert archive["records"] == 3 and archive["role"].startswith("DESCRIPTIVE")
    assert out["fill_model"]["fill_model"].startswith("REALISTIC_V1")
    assert out["coverage"]["market_context_joined"] == 9
    assert sxp.shadow_exit_set_id(SET) in out["shadow_exit_sets"]


def test_exit_stats_have_ev_ci_win_and_giveback(stores):
    out = report.build_report(*stores)
    group = out["cohorts"][report.COHORT_LIVE]["groups"][0]
    assert group["group"] == "FAMILY_COMMITTED_FADE_TAKER_90" and group["filled"] == 6
    exits = {row["id"]: row for row in group["exits"]}
    assert set(exits) >= {"actual", "LATE_BE_20_5", "COND_CUT_12_5M_MFE2", "COMPOSITE_LATE_BE20_5_TRAIL1.5_ARM2"}
    hold = exits["hold_to_horizon"]
    assert hold["n"] == 6 and hold["win_rate"] == 0.0
    assert hold["giveback_rate_of_meaningful"] == 1.0
    lo, hi = hold["ci_bp"]
    assert lo <= hold["ev_bp"] <= hi
    assert exits["COND_CUT_12_5M_MFE2"]["ev_bp"] > hold["ev_bp"]
    assert exits["LATE_BE_20_5"]["delta_vs_actual_n"] == 6 and "delta_vs_hold_to_horizon_bp" in exits["LATE_BE_20_5"]
    assert group["horizons"]["1"]["n"] == 6


def test_report_cli_writes_atomically(stores, tmp_path):
    mirror, bf = stores
    out_dir = tmp_path / "out"
    assert report.main(["--mirror", str(mirror), "--out-dir", str(out_dir), "--backfill-dir", str(bf)]) == 0
    payload = json.loads((out_dir / report.REPORT_NAME).read_text(encoding="utf-8"))
    assert payload["sources"]["records"] == 9
    assert not list(out_dir.glob("*.tmp"))


def test_signal_replay_backfill_is_read_only_and_tile_attributed(tmp_path):
    mirror = tmp_path / "mirror"
    mirror.mkdir()
    closed = {**_replay_row(), "dump_reason": "BUFFER_CLOSED"}
    opened = {**_replay_row(), "trade_id": "ntt-2", "dump_reason": "SNAPSHOT"}
    source = mirror / "signal_replay.jsonl"
    _write(source, [closed, closed, opened])
    before = source.read_bytes()
    out = backfill._AtomicJsonl(tmp_path / "bf" / "signal_replay_backfill.jsonl")
    stats = backfill.backfill_signal_replay(mirror, out)
    out.commit()
    assert source.read_bytes() == before
    assert stats["written"] == 1 and stats["skipped_duplicate"] == 1 and stats["skipped_open"] == 1
    row = json.loads((tmp_path / "bf" / "signal_replay_backfill.jsonl").read_text(encoding="utf-8"))
    assert row["source"] == sxp.SOURCE_BACKFILL_REPLAY
    assert row["tile"] == registry.tile_lane_for_trade_id("ntt-1")


def test_backfill_refuses_onedrive_output(tmp_path):
    with pytest.raises(SystemExit):
        backfill.main(["--mirror", str(tmp_path), "--out-dir", r"C:\Users\x\OneDrive\out", "--skip-tape"])


def test_dashboard_route_reports_missing_then_ok(monkeypatch, tmp_path):
    from research import research_dashboard as dashboard

    path = tmp_path / report.REPORT_NAME
    monkeypatch.setattr(dashboard, "SHADOW_EXIT_REPORT_PATH", path)
    monkeypatch.setattr(dashboard, "_API_RESPONSE_CACHE", {})
    client = dashboard.app.test_client()
    missing = client.get("/api/research/shadow_exits").get_json()
    assert missing["status"] == "MISSING"
    dashboard._API_RESPONSE_CACHE.clear()
    path.write_text(json.dumps({"schema": report.SCHEMA, "generated_at": "2026-10-04T00:00:00Z", "cohorts": {}}),
                    encoding="utf-8")
    ok = client.get("/api/research/shadow_exits").get_json()
    assert ok["status"] == "OK" and ok["age_sec"] is not None
    assert 'id="sec-shadow-exits"' in dashboard.DASHBOARD_HTML


def _render_shadow_loader(payload, tmp_path):
    """Run the real loader in node from a script file (Windows caps ``node -e`` length)."""
    from research import research_dashboard as dashboard

    page = dashboard.DASHBOARD_HTML
    start = page.index("async function loadShadowExits()")
    body = page[start:min(page.index("async function ", start + 15), page.index("const SECTION_LOADERS", start))]
    helpers = page[page.index("function missingResearchSource()"):page.index("async function loadFindings()")]
    script = helpers + body + """
const elements = {};
const element = id => elements[id] ||= {innerHTML:'', textContent:'', querySelector: s => element(id + s)};
const document = {getElementById: element};
""" + f"const fetch = async () => ({{ok:true, json:async () => ({json.dumps(payload)})}});\n"
    script += "loadShadowExits().then(() => console.log(JSON.stringify(elements)));"
    path = tmp_path / "loader.js"
    path.write_text(script, encoding="utf-8")
    result = subprocess.run([shutil.which("node"), str(path)], capture_output=True, text=True, encoding="utf-8",
                            timeout=15)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_dashboard_loader_renders_side_by_side_tables(stores, tmp_path):
    payload = {**report.build_report(*stores), "status": "OK", "age_sec": 5}
    html = json.dumps(_render_shadow_loader(payload, tmp_path))
    assert "FAMILY_COMMITTED_FADE_TAKER_90" in html and "LATE_BE_20_5" in html
    assert "HEADLINE" in html and "REALISTIC_V1" in html
    missing = _render_shadow_loader({"status": "MISSING", "cohorts": {}}, tmp_path)
    assert "MISSING" in json.dumps(missing) or "No shadow-exit report" in json.dumps(missing)
