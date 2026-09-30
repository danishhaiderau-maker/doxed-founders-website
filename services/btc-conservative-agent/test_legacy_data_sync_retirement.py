"""The whole-generation transfer path is retired; research ships as segments."""

import csv
from pathlib import Path

import pytest

import bot

ROOT = Path(__file__).resolve().parent
BOT_SOURCE = (ROOT / "bot.py").read_text(encoding="utf-8")


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(bot, "_DASHBOARD_BOOTSTRAP_COMPLETE", True)
    monkeypatch.setattr(bot, "_BOT_ADMIN_TOKEN", "t" * 32)
    return bot.app.test_client()


@pytest.mark.parametrize("method, path", [
    ("get", "/api/data-sync/manifest"),
    ("get", "/api/data-sync/identity"),
    ("post", "/api/data-sync/manifest/refresh"),
    ("get", "/api/data-sync/sqlite-snapshot"),
    ("get", "/api/data-sync/file"),
    ("post", "/api/data-sync/ack"),
    ("post", "/api/data-sync/lifecycle-ack"),
    ("post", "/api/data-sync/lifecycle-cleanup/prepare"),
    ("post", "/api/data-sync/lifecycle-purge/execute"),
    ("post", "/api/data-sync/raw-generation/purge"),
    ("get", "/api/data-sync/bundles"),
    ("get", "/api/data-sync/bundle"),
])
def test_retired_transfer_routes_answer_gone(client, method, path):
    response = getattr(client, method)(path, headers={"X-Bot-Admin-Token": "t" * 32})
    assert response.status_code == 410
    assert response.get_json()["errorCode"] == "LEGACY_DATA_SYNC_RETIRED"


def test_inbound_uploads_are_not_retired():
    assert bot._DATA_SYNC_INBOUND_ROUTES == {
        "/api/data-sync/platform-relay-evidence", "/api/data-sync/analyzer-report",
    }


def test_boot_starts_no_legacy_transfer_worker():
    main = BOT_SOURCE[BOT_SOURCE.index("\ndef main():"):BOT_SOURCE.index("\ndef _require_fly_runtime_for_direct_start")]
    assert "_start_data_sync_background_refresh()" not in main
    assert "_start_data_sync_bundle_reservation_hydration()" not in main
    assert "register_bundle_routes" not in BOT_SOURCE


def test_csv_schema_expansion_appends_new_columns_in_row_order(tmp_path):
    path = tmp_path / "expired_orders_3factor.csv"
    bot._dynamic_csv_writer_once(str(path), {"time": "t1", "trade_id": "a", "dir": "LONG"})
    bot._dynamic_csv_writer_once(str(path), {"zeta": "z", "time": "t2", "trade_id": "b",
                                             "dir": "SHORT", "alpha": "x"})
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.reader(handle))
    assert rows[0] == ["time", "trade_id", "dir", "zeta", "alpha"]
    assert rows[2] == ["t2", "b", "SHORT", "z", "x"]
