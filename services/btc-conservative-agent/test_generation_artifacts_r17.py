"""#865 follow-up: the generation writes the unplaced-rows list and tile_realism_table.json as artifacts, and the
receipt carries the mirror-publish freshness (ANALYZER_MIRROR_PUBLISH_STALE when the last publish is > 2 h old)."""
import csv
import json
import time
from datetime import datetime, timezone
from types import SimpleNamespace

import analyzer_research_engine_v62 as analyzer
from test_epoch_unplaced_rows_excluded import FLOOR, MANIFEST, make_root


def test_generation_writes_unplaced_rows_artifact_from_its_own_snapshot(tmp_path, monkeypatch):
    root = make_root(tmp_path / "data")
    out = tmp_path / "out"
    out.mkdir()
    monkeypatch.setattr(analyzer, "analyzer_report_path", lambda name: str(out / name))
    block = {}
    analyzer._write_unplaced_rows_artifact(SimpleNamespace(manifest=MANIFEST, floor_ts=FLOOR), str(root), block)
    doc = json.loads((out / analyzer.UNPLACED_ROWS_FILE).read_text())
    assert doc["count"] == 3 and block["unplaced_rows_list"]["count"] == 3
    assert block["unplaced_rows_list"]["list_sha256"] == doc["list_sha256"]
    assert doc["treatment"].startswith("EXCLUDED")


def test_generation_writes_tile_realism_table_artifact(tmp_path, monkeypatch):
    out = tmp_path / "out"
    out.mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(analyzer, "analyzer_report_path", lambda name: str(out / name))
    with open(tmp_path / analyzer.TRADES_FILE, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["trade_id", "tile_short", "net_pnl_bp", "trigger_price_source"])
        w.writeheader()
        w.writerow({"trade_id": "t1", "tile_short": "GS01", "net_pnl_bp": "2.5", "trigger_price_source": "ticker"})
    manifest = {}
    analyzer._write_tile_realism_artifact(manifest)
    table = json.loads((out / analyzer.TILE_REALISM_FILE).read_text())
    assert table["tiles"]["GS01"]["countable"] == 0  # non-book trigger is never counted
    assert manifest["tile_realism_table"]["file"] == analyzer.TILE_REALISM_FILE
    assert {analyzer.TILE_REALISM_FILE, analyzer.UNPLACED_ROWS_FILE} <= set(manifest["text_artifacts"])


def test_receipt_freshness_reads_mirror_publish_receipt(tmp_path, monkeypatch):
    receipt = tmp_path / "analyzer-mirror-publish.status.json"
    old = datetime.fromtimestamp(time.time() - 3 * 3600, timezone.utc).isoformat()
    receipt.write_text(json.dumps({"state": "PUBLISHED", "published_at": old}))
    monkeypatch.setenv("ANALYZER_MIRROR_PUBLISH_RECEIPT", str(receipt))
    fresh = analyzer._mirror_publish_freshness()
    assert fresh["publish_stale"] is True and fresh["alert"] == "ANALYZER_MIRROR_PUBLISH_STALE"
    monkeypatch.delenv("ANALYZER_MIRROR_PUBLISH_RECEIPT")
    assert analyzer._mirror_publish_freshness() is None
