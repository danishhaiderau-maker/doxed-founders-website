"""Fix A — /api/analyzer-mirror/status no-bundle → non-404 RED NO_VALIDATED_ANALYZER_BUNDLE.

Plan: docs/plan-shipping-analyzer-fix-20261031.md (cite governing RC 6883c7e759df).
Keep /api/analyzer/summary and /analysis/* at 503 (not softened here).
Unreadable status.json stays HTTP 404 (plan covers only `active is None`).
"""
from __future__ import annotations

import ast
import json
import tempfile
from pathlib import Path

import pytest
from flask import Flask, jsonify, request

ROOT = Path(__file__).resolve().parent
BOT = (ROOT / "bot.py").read_text(encoding="utf-8")

_REASON = "NO_VALIDATED_ANALYZER_BUNDLE"

_NO_BUNDLE_200 = """\
        resp = jsonify(_analyzer_mirror_no_validated_bundle_payload(
            legacy_preserved=legacy_preserved,
        ))
        resp.headers['Cache-Control'] = 'no-store'
        return resp, 200"""

_NO_BUNDLE_404_MUTANT = """\
        return jsonify({
            "available": False,
            "reason": "no complete validated analyzer bundle is installed",
            "required_schema": _ANALYZER_BUNDLE_SCHEMA,
            "legacy_data_preserved": legacy_preserved,
        }), 404"""


def _status_fns(source: str = BOT):
    tree = ast.parse(source)
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


def _client_from_source(
    source: str,
    *,
    active,
    legacy: Path,
    admin: bool = True,
    monitor: bool = False,
    reason_const: str = _REASON,
):
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
        "_ANALYZER_BUNDLE_NO_VALIDATED_REASON": reason_const,
    }
    exec(
        compile(ast.Module(body=_status_fns(source), type_ignores=[]), "bot.py", "exec"),
        namespace,
    )
    return app.test_client()


def _client(*, active, legacy: Path, admin: bool = True, monitor: bool = False):
    return _client_from_source(
        BOT, active=active, legacy=legacy, admin=admin, monitor=monitor
    )


def _assert_no_bundle_red(client):
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


def test_no_validated_bundle_returns_red_non_404_on_status_health_alarm():
    with tempfile.TemporaryDirectory() as tmp:
        legacy = Path(tmp) / "legacy"
        legacy.mkdir()
        _assert_no_bundle_red(_client(active=None, legacy=legacy))


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


def test_unreadable_status_json_stays_404():
    """R38: Fix A does not rewrite the except branch — unreadable status.json → 404."""
    with tempfile.TemporaryDirectory() as tmp:
        gen = Path(tmp) / "generation-broken"
        gen.mkdir()
        (gen / "status.json").write_text("{not-json", encoding="utf-8")
        legacy = Path(tmp) / "legacy"
        legacy.mkdir()
        client = _client(active=gen, legacy=legacy)
        response = client.get("/api/analyzer-mirror/status")
        assert response.status_code == 404
        assert response.json["available"] is False
        assert response.json["reason"] == "no local analyzer report uploaded yet"


def test_unauth_still_401():
    with tempfile.TemporaryDirectory() as tmp:
        legacy = Path(tmp)
        client = _client(active=None, legacy=legacy, admin=False, monitor=False)
        response = client.get("/api/analyzer-mirror/status")
        assert response.status_code == 401


def test_mutation_proof_restore_404_fails_red_contract():
    """Recorded mutant run: restore HTTP 404 on `active is None` → RED contract assert fails."""
    assert _NO_BUNDLE_200 in BOT
    mutant = BOT.replace(_NO_BUNDLE_200, _NO_BUNDLE_404_MUTANT, 1)
    assert mutant != BOT
    with tempfile.TemporaryDirectory() as tmp:
        legacy = Path(tmp) / "legacy"
        legacy.mkdir()
        client = _client_from_source(mutant, active=None, legacy=legacy)
        with pytest.raises(AssertionError):
            _assert_no_bundle_red(client)
        # Mutant behaviour itself: 404, not RED body.
        response = client.get("/api/analyzer-mirror/status")
        assert response.status_code == 404
        assert response.json.get("status") != "RED"


def test_mutation_proof_health_amber_fails_red_contract():
    """Recorded mutant run: payload health AMBER → health==RED assert fails (M3)."""
    needle = '"health": "RED",'
    assert BOT.count(needle) >= 1
    mutant = BOT.replace(needle, '"health": "AMBER",', 1)
    assert mutant != BOT
    with tempfile.TemporaryDirectory() as tmp:
        legacy = Path(tmp) / "legacy"
        legacy.mkdir()
        client = _client_from_source(mutant, active=None, legacy=legacy)
        with pytest.raises(AssertionError):
            _assert_no_bundle_red(client)
        body = client.get("/api/analyzer-mirror/status").json
        assert body["health"] == "AMBER"


def test_mutation_proof_reason_drift_fails_red_contract():
    """Recorded mutant run: reason field drifts → reason assert fails (M4)."""
    needle = '"reason": _ANALYZER_BUNDLE_NO_VALIDATED_REASON,'
    assert needle in BOT
    mutant = BOT.replace(needle, '"reason": "NO_BUNDLE",', 1)
    assert mutant != BOT
    with tempfile.TemporaryDirectory() as tmp:
        legacy = Path(tmp) / "legacy"
        legacy.mkdir()
        client = _client_from_source(mutant, active=None, legacy=legacy)
        with pytest.raises(AssertionError):
            _assert_no_bundle_red(client)
        assert client.get("/api/analyzer-mirror/status").json["reason"] == "NO_BUNDLE"


def test_mutation_proof_summary_503_softened_is_caught():
    """Recorded mutant run: soften summary 503 → 200 must be detected."""
    tree = ast.parse(BOT)
    fn = next(
        n for n in tree.body
        if isinstance(n, ast.FunctionDef) and n.name == "api_analyzer_summary"
    )
    original = ast.get_source_segment(BOT, fn)
    assert original is not None
    assert "return resp, 503" in original
    mutant_src = original.replace("return resp, 503", "return resp, 200", 1)
    assert mutant_src != original

    import os

    app = Flask("summary-mutant")
    namespace = {
        "app": app,
        "request": request,
        "jsonify": jsonify,
        "os": os,
        "_active_analyzer_mirror_dir": lambda: None,
        "_ANALYZER_BUNDLE_NO_VALID": "no valid bundle",
        "_external_analyzer_response": lambda endpoint: (jsonify({"ok": True}), 200),
    }
    exec(compile(mutant_src, "mutant_summary", "exec"), namespace)
    app.add_url_rule(
        "/api/analyzer/summary",
        view_func=namespace["api_analyzer_summary"],
    )
    client = app.test_client()
    prev = os.environ.get("FLY_APP_NAME")
    os.environ["FLY_APP_NAME"] = "mutant-fly"
    try:
        response = client.get("/api/analyzer/summary")
        assert response.status_code == 200  # mutant behaviour
        with pytest.raises(AssertionError):
            assert response.status_code == 503
    finally:
        if prev is None:
            os.environ.pop("FLY_APP_NAME", None)
        else:
            os.environ["FLY_APP_NAME"] = prev


if __name__ == "__main__":
    test_no_validated_bundle_returns_red_non_404_on_status_health_alarm()
    test_valid_bundle_status_still_200_available_true()
    test_unreadable_status_json_stays_404()
    test_unauth_still_401()
    test_mutation_proof_restore_404_fails_red_contract()
    test_mutation_proof_health_amber_fails_red_contract()
    test_mutation_proof_reason_drift_fails_red_contract()
    test_mutation_proof_summary_503_softened_is_caught()
    print("analyzer mirror status Fix A checks passed")
