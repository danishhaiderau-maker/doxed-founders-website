"""Fix A — /api/analyzer-mirror/status no-bundle → non-404 RED NO_VALIDATED_ANALYZER_BUNDLE.

Plan: docs/plan-shipping-analyzer-fix-20261031.md sha b2b3986939ec.
Keep /api/analyzer/summary and /analysis/* at 503 (not softened here).
"""
from __future__ import annotations

import ast
import json
import tempfile
from pathlib import Path

from flask import Flask, jsonify, request

ROOT = Path(__file__).resolve().parent
BOT = (ROOT / "bot.py").read_text(encoding="utf-8")

_REASON = "NO_VALIDATED_ANALYZER_BUNDLE"


def _status_fns():
    tree = ast.parse(BOT)
    wanted = {
        "api_analyzer_mirror_status",
        "_analyzer_mirror_no_validated_bundle_payload",
    }
    selected = [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in wanted
    ]
    assert {n.name for n in selected} == wanted
    return selected


def _client(*, active, legacy: Path, admin: bool = True, monitor: bool = False):
    app = Flask("analyzer-mirror-status-fix-a")
    namespace = {
        "app": app,
        "request": request,
        "json": json,
        "jsonify": jsonify,
        "_admin_authed": lambda: admin,
        "monitor_integrity_authorized": lambda: monitor,
        "_active_analyzer_mirror_dir": lambda: active,
        "_analyzer_mirror_dir": lambda: legacy,
        "_ANALYZER_BUNDLE_SCHEMA": "analyzer_mirror_bundle_v2",
        "_ANALYZER_BUNDLE_NO_VALIDATED_REASON": _REASON,
    }
    exec(compile(ast.Module(body=_status_fns(), type_ignores=[]), "bot.py", "exec"), namespace)
    return app.test_client()


def test_no_validated_bundle_returns_red_non_404_on_status_health_alarm():
    with tempfile.TemporaryDirectory() as tmp:
        legacy = Path(tmp) / "legacy"
        legacy.mkdir()
        client = _client(active=None, legacy=legacy)
        response = client.get("/api/analyzer-mirror/status")
        assert response.status_code == 200
        assert response.status_code != 404
        body = response.json
        assert body["available"] is False
        assert body["status"] == "RED"
        assert body["health"] == "RED"
        assert body["reason"] == _REASON
        assert body["required_schema"] == "analyzer_mirror_bundle_v2"
        assert body["legacy_data_preserved"] is True
        assert body["alarms"][0]["code"] == _REASON
        assert body["alarms"][0]["severity"] == "critical"
        assert response.headers.get("Cache-Control") == "no-store"


def test_valid_bundle_status_still_200_available_true():
    with tempfile.TemporaryDirectory() as tmp:
        gen = Path(tmp) / "generation-ok"
        gen.mkdir()
        (gen / "status.json").write_text(
            '{"schema":"analyzer_mirror_bundle_v2","complete":true,"generation":"g1"}',
            encoding="utf-8",
        )
        legacy = Path(tmp) / "legacy"
        legacy.mkdir()
        client = _client(active=gen, legacy=legacy)
        response = client.get("/api/analyzer-mirror/status")
        assert response.status_code == 200
        body = response.json
        assert body["available"] is True
        assert body["generation"] == "g1"
        assert body.get("reason") != _REASON
        assert response.headers.get("Cache-Control") == "no-store"


def test_unauth_still_401():
    with tempfile.TemporaryDirectory() as tmp:
        legacy = Path(tmp)
        client = _client(active=None, legacy=legacy, admin=False, monitor=False)
        response = client.get("/api/analyzer-mirror/status")
        assert response.status_code == 401


def test_mutation_proof_404_return_is_gone():
    """Mutant: restoring `, 404` on the no-bundle branch must fail this assert."""
    handler = BOT.split("def api_analyzer_mirror_status():", 1)[1].split(
        "\n@app.route('/analysis')", 1
    )[0]
    no_bundle = handler.split("if active is None:", 1)[1].split("try:", 1)[0]
    assert "return resp, 200" in no_bundle
    assert "), 404" not in no_bundle
    assert "_analyzer_mirror_no_validated_bundle_payload" in no_bundle
    assert "NO_VALIDATED_ANALYZER_BUNDLE" in BOT
    assert '"health": "RED"' in BOT
    assert '"status": "RED"' in BOT


def test_mutation_proof_reason_constant_wired():
    """Mutant: if reason string drifts off NO_VALIDATED_ANALYZER_BUNDLE, fail closed."""
    assert '_ANALYZER_BUNDLE_NO_VALIDATED_REASON = "NO_VALIDATED_ANALYZER_BUNDLE"' in BOT
    payload_fn = BOT.split("def _analyzer_mirror_no_validated_bundle_payload", 1)[1].split(
        "\ndef _analyzer_bundle_fresh_gate", 1
    )[0]
    assert "_ANALYZER_BUNDLE_NO_VALIDATED_REASON" in payload_fn
    assert '"status": "RED"' in payload_fn
    assert '"health": "RED"' in payload_fn
    assert '"alarms"' in payload_fn


def test_mutation_proof_summary_and_analysis_stay_503():
    """Fix A must not soften summary/analysis fail-closed 503."""
    summary = BOT.split("def api_analyzer_summary():", 1)[1].split(
        "\ndef api_analyzer_genome", 1
    )[0]
    assert "return resp, 503" in summary
    assert "_ANALYZER_BUNDLE_NO_VALID" in summary
    index = BOT.split("def analyzer_mirror_dashboard_index():", 1)[1].split(
        "\n@app.route('/analysis/<path:artifact_path>')", 1
    )[0]
    assert "), 503" in index


if __name__ == "__main__":
    test_no_validated_bundle_returns_red_non_404_on_status_health_alarm()
    test_valid_bundle_status_still_200_available_true()
    test_unauth_still_401()
    test_mutation_proof_404_return_is_gone()
    test_mutation_proof_reason_constant_wired()
    test_mutation_proof_summary_and_analysis_stay_503()
    print("analyzer mirror status Fix A checks passed")
