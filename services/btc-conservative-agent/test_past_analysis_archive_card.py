import io
import json
import os
import stat
import zipfile
from datetime import datetime, timezone

import analysis_archive as aa
from research import research_dashboard as dashboard


def _snapshot(tmp_path, monkeypatch, now):
    archive = tmp_path / "analysis-archive"
    reports = tmp_path / "reports"
    data = tmp_path / "data"
    reports.mkdir(exist_ok=True)
    data.mkdir(exist_ok=True)
    (reports / "report_manifest.json").write_text(json.dumps({"generation_id": "gen1", "analyzer_revision": "abc"}))
    monkeypatch.setenv("DOXXED_ANALYSIS_ARCHIVE_DIR", str(archive))
    trades = [{"trade_id": "t1", "research_lane": "FAMILY_A", "net_pnl_usd": -0.25, "close_ts": "2026-10-02T10:00:00Z"},
              {"trade_id": "t2", "research_lane": "FAMILY_B", "net_pnl_usd": 0.05, "close_ts": "2026-10-02T11:00:00Z"}]
    ts = datetime.fromisoformat(now).replace(tzinfo=timezone.utc).timestamp()
    return aa.write_generation_snapshot(report_dir=str(reports), data_dir=str(data), trades=trades, now=ts)


def _make_writable(path):
    for directory, _dirs, files in os.walk(path):
        for name in files:
            os.chmod(os.path.join(directory, name), stat.S_IWRITE | stat.S_IREAD)


def test_past_analysis_card_reads_the_analysis_archive(tmp_path, monkeypatch):
    monkeypatch.setattr(dashboard, "_API_RESPONSE_CACHE", {})
    monkeypatch.setattr(dashboard, "ROOT", tmp_path / "legacy-root")
    try:
        first = _snapshot(tmp_path, monkeypatch, "2026-10-02T12:00:00")
        second = _snapshot(tmp_path, monkeypatch, "2026-10-02T13:00:00")
        client = dashboard.app.test_client()
        payload = client.get("/api/past-analysis").get_json()
        ids = [row["archive_id"] for row in payload["analyses"]]
        assert ids == [second["snapshot_id"], first["snapshot_id"]]
        assert payload["status"] == "AVAILABLE"
        perf = payload["analyses"][0]["performance"]
        assert (perf["trades"], perf["net_pnl_usd"], perf["tiles"]) == (2, -0.2, 2)

        response = client.get(f"/download/past-analysis/{first['snapshot_id']}")
        assert response.status_code == 200
        names = zipfile.ZipFile(io.BytesIO(response.data)).namelist()
        assert {"snapshot.json", "receipt.json"} <= set(names)
        assert client.get("/download/past-analysis").status_code == 200
        assert client.get("/download/past-analysis/..%2F..%2Fetc").status_code == 404
    finally:
        _make_writable(tmp_path)


def test_empty_archive_declares_why(tmp_path, monkeypatch):
    monkeypatch.setattr(dashboard, "_API_RESPONSE_CACHE", {})
    monkeypatch.setattr(dashboard, "ROOT", tmp_path)
    monkeypatch.setenv("DOXXED_ANALYSIS_ARCHIVE_DIR", str(tmp_path / "none"))
    payload = dashboard.app.test_client().get("/api/past-analysis").get_json()
    assert payload["analyses"] == []
    assert payload["status"] == "EMPTY_NO_COMPLETED_GENERATION_SNAPSHOT"
