"""Research owner allows CSV export; execution-mirror still 404s warehouse routes."""
from __future__ import annotations

import os

from flask import Flask

from showcase_ui import (
    _should_block_research_warehouse,
    register_showcase_ui,
)


def _clear_mode_env():
    for name in (
        "HOME_RESEARCH_FULL",
        "BLOCK_RESEARCH_WAREHOUSE",
        "EXECUTION_MIRROR_ONLY",
        "SHOWCASE_AGENT",
        "HOME_BOT_LOCAL",
    ):
        os.environ.pop(name, None)


def _mini_app():
    app = Flask(__name__)

    @app.route("/api/export_csv")
    @app.route("/api/export.csv")
    def export_csv():
        return "ok-export", 200

    @app.route("/api/export_debug")
    def export_debug():
        return "ok-debug", 200

    return app


def test_fly_like_showcase_owner_does_not_block_warehouse():
    _clear_mode_env()
    os.environ["SHOWCASE_AGENT"] = "1"
    os.environ["HOME_BOT_LOCAL"] = "0"
    os.environ["HOME_RESEARCH_FULL"] = "1"
    os.environ["BLOCK_RESEARCH_WAREHOUSE"] = "0"
    assert _should_block_research_warehouse(None) is False


def test_execution_mirror_flag_blocks_warehouse():
    _clear_mode_env()
    os.environ["EXECUTION_MIRROR_ONLY"] = "1"
    os.environ["SHOWCASE_AGENT"] = "1"
    os.environ["HOME_RESEARCH_FULL"] = "0"
    os.environ["BLOCK_RESEARCH_WAREHOUSE"] = "1"
    assert _should_block_research_warehouse(None) is True


def test_owner_mode_serves_export_csv():
    _clear_mode_env()
    os.environ["SHOWCASE_AGENT"] = "1"
    os.environ["HOME_RESEARCH_FULL"] = "1"
    os.environ["BLOCK_RESEARCH_WAREHOUSE"] = "0"
    app = _mini_app()
    register_showcase_ui(app, block_warehouse=None)
    client = app.test_client()
    assert client.get("/api/export.csv").status_code == 200
    assert client.get("/api/export.csv").get_data(as_text=True) == "ok-export"
    assert client.get("/api/export_csv").status_code == 200


def test_execution_mirror_export_csv_is_404():
    _clear_mode_env()
    os.environ["EXECUTION_MIRROR_ONLY"] = "1"
    app = _mini_app()
    register_showcase_ui(app, block_warehouse=None)
    client = app.test_client()
    response = client.get("/api/export.csv")
    assert response.status_code == 404
    body = response.get_json()
    assert body["runtime_mode"] == "EXECUTION_MIRROR"
    assert "Research warehouse export not available" in body["error"]
    assert client.get("/api/export_csv").status_code == 404


if __name__ == "__main__":
    test_fly_like_showcase_owner_does_not_block_warehouse()
    test_execution_mirror_flag_blocks_warehouse()
    test_owner_mode_serves_export_csv()
    test_execution_mirror_export_csv_is_404()
    print("PASS: research owner export vs execution-mirror 404")
