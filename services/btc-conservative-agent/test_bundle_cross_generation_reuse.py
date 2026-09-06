"""Actual worker/API/client reuse across distinct source generations."""
import pytest
from flask import Flask

import data_sync_bundle_client as client
import test_data_sync_bundle_worker as fixtures
from data_sync_bundle_api import register_bundle_routes
from data_sync_bundle_worker import run_bundle_worker


def generation(root, generation_id, revision, payload, monkeypatch):
    monkeypatch.setattr(fixtures, "GEN", generation_id)
    source = root / "source"
    rows = [fixtures._row(source, "v3/market_segments/11/" + "1" * 64 + ".json", payload)]
    meta = fixtures._fixture(root, rows)
    meta["source_git_rev"] = revision
    output = root / "packages"
    run_bundle_worker(meta, source, output)
    app = Flask(__name__)
    register_bundle_routes(app, authenticated=lambda: True,
                           generation_lookup=lambda _: meta, output_root=output)
    http = app.test_client()
    index = http.get(f"/api/data-sync/bundles?generation_id={generation_id}").json
    calls = []
    def fetch(url, *, timeout):
        calls.append(url)
        response = http.get(url)
        return response.status_code, dict(response.headers), response.data
    return rows, index, fetch, calls


@pytest.mark.parametrize("changed", [False, True])
def test_new_generation_uses_current_descriptor_before_reusing_old_bytes(tmp_path, monkeypatch, changed):
    old_rows, old_index, old_fetch, _ = generation(tmp_path / "old", "a" * 64,
                                                 "1" * 40, b"old-data", monkeypatch)
    local = tmp_path / "local"
    first = client.fetch_verified_package(old_index["packages"][0],
        {**old_index["generation"], "ack_eligible": True}, old_rows,
        tmp_path / "first-stage", old_fetch)
    from pathlib import Path
    target = local / old_rows[0]["path"]
    target.parent.mkdir(parents=True)
    target.write_bytes(Path(first["members"][0]["staged_path"]).read_bytes())
    rows, index, fetch, calls = generation(tmp_path / "new", "b" * 64,
        "2" * 40, b"new-data" if changed else b"old-data", monkeypatch)
    result = client.fetch_verified_package(index["packages"][0],
        {**index["generation"], "ack_eligible": True}, rows,
        tmp_path / "new-stage", fetch, verified_local_root=local)
    assert "descriptor=1" in calls[0]
    assert result.get("reused_local", False) is (not changed)
    assert len(calls) == (2 if changed else 1)
    assert result["descriptor"]["source_git_rev"] == "2" * 40
    assert result["descriptor"]["inventory_generation_id"] == "b" * 64
    assert {key: result["members"][0][key] for key in client.ROW_FIELDS} == rows[0]
    assert result["ack_authority"] == "ORIGINAL_MANIFEST_ROWS_ONLY"
    assert target.read_bytes() == b"old-data"
