"""Analyzer-mirror status label — Fix A + seal ca14dba80fca (unreadable 200).

Seal: docs/card-analyzer-mirror-status-label-20261031.md ca14dba80fca
Prior Fix A: docs/plan-shipping-analyzer-fix-20261031.md sha e448f5f67f8a
Rules R0–R48 1120edc5b538. Keep /api/analyzer/summary and /analysis/* at 503
when no validated bundle. Unreadable status.json → HTTP 200 RED
ANALYZER_STATUS_UNREADABLE (never 404). Mutation tests required (R23).
"""
from __future__ import annotations

import ast
import json
import os
import tempfile
from pathlib import Path

import pytest
from flask import Flask, jsonify, make_response, request

ROOT = Path(__file__).resolve().parent
BOT = (ROOT / "bot.py").read_text(encoding="utf-8")

_REASON = "NO_VALIDATED_ANALYZER_BUNDLE"
_UNREADABLE = "ANALYZER_STATUS_UNREADABLE"
_NO_VALID = "no valid bundle"
_SCHEMA = "analyzer_mirror_bundle_v2"

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

_UNREADABLE_200 = """\
        resp = jsonify(_analyzer_mirror_unreadable_status_payload(
            legacy_preserved=legacy_preserved,
        ))
        resp.headers['Cache-Control'] = 'no-store'
        return resp, 200"""

_UNREADABLE_404_MUTANT = """\
        return jsonify({"available": False, "reason": "no local analyzer report uploaded yet"}), 404"""


def _status_fns(source: str = BOT):
    tree = ast.parse(source)
    wanted = {
        "api_analyzer_mirror_status",
        "_analyzer_mirror_no_validated_bundle_payload",
        "_analyzer_mirror_unreadable_status_payload",
        "classify_analyzer_mirror_status_for_monitor",
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
        "_ANALYZER_BUNDLE_SCHEMA": _SCHEMA,
        "_ANALYZER_BUNDLE_NO_VALIDATED_REASON": reason_const,
        "_ANALYZER_STATUS_UNREADABLE_REASON": _UNREADABLE,
    }
    exec(
        compile(ast.Module(body=_status_fns(source), type_ignores=[]), "bot.py", "exec"),
        namespace,
    )
    return app.test_client(), namespace


def _client(*, active, legacy: Path, admin: bool = True, monitor: bool = False):
    client, _ns = _client_from_source(
        BOT, active=active, legacy=legacy, admin=admin, monitor=monitor
    )
    return client


def _assert_no_bundle_red(client, *, legacy_preserved: bool = True):
    response = client.get("/api/analyzer-mirror/status")
    assert response.status_code == 200
    assert response.status_code != 404
    body = response.json
    assert body["available"] is False
    assert body["status"] == "RED"
    assert body["health"] == "RED"
    assert body["reason"] == _REASON
    assert body["required_schema"] == _SCHEMA
    assert body["legacy_data_preserved"] is legacy_preserved
    assert body["alarms"][0]["code"] == _REASON
    assert body["alarms"][0]["severity"] == "critical"
    assert response.headers.get("Cache-Control") == "no-store"


def _assert_unreadable_red(client, *, legacy_preserved: bool = True):
    response = client.get("/api/analyzer-mirror/status")
    assert response.status_code == 200
    assert response.status_code != 404
    body = response.json
    assert body["available"] is False
    assert body["status"] == "RED"
    assert body["health"] == "RED"
    assert body["reason"] == _UNREADABLE
    assert body["required_schema"] == _SCHEMA
    assert body["legacy_data_preserved"] is legacy_preserved
    assert body["alarms"][0]["code"] == _UNREADABLE
    assert response.headers.get("Cache-Control") == "no-store"


def _extract_fn(name: str, source: str = BOT) -> str:
    tree = ast.parse(source)
    fn = next(
        n for n in tree.body
        if isinstance(n, ast.FunctionDef) and n.name == name
    )
    text = ast.get_source_segment(source, fn)
    assert text is not None
    return text


def _summary_client(*, active, fly: bool = True):
    app = Flask("analyzer-summary-fix-a")
    namespace = {
        "app": app,
        "request": request,
        "json": json,
        "jsonify": jsonify,
        "os": os,
        "_active_analyzer_mirror_dir": lambda: active,
        "_ANALYZER_BUNDLE_NO_VALID": _NO_VALID,
        "_external_analyzer_response": lambda endpoint: (jsonify({"ok": True, "bundle": True}), 200),
        "_analyzer_proxy_fetch": lambda path, timeout=5.0: (None, b""),
    }
    exec(compile(_extract_fn("api_analyzer_summary"), "bot.py", "exec"), namespace)
    app.add_url_rule(
        "/api/analyzer/summary",
        view_func=namespace["api_analyzer_summary"],
    )
    client = app.test_client()
    prev = os.environ.get("FLY_APP_NAME")
    if fly:
        os.environ["FLY_APP_NAME"] = "fix-a-fly"
    elif "FLY_APP_NAME" in os.environ:
        del os.environ["FLY_APP_NAME"]
    return client, prev


def _analysis_client(*, active, authed: bool = True):
    app = Flask("analyzer-analysis-fix-a")
    namespace = {
        "app": app,
        "request": request,
        "jsonify": jsonify,
        "make_response": make_response,
        "_analyzer_view_authed": lambda: authed,
        "_active_analyzer_mirror_dir": lambda: active,
        "_ANALYZER_BUNDLE_NO_VALID": _NO_VALID,
    }
    for name in (
        "analyzer_mirror_dashboard_index",
        "analyzer_mirror_artifact",
    ):
        exec(compile(_extract_fn(name), "bot.py", "exec"), namespace)
    app.add_url_rule(
        "/analysis/",
        view_func=namespace["analyzer_mirror_dashboard_index"],
    )
    app.add_url_rule(
        "/analysis/<path:artifact_path>",
        view_func=namespace["analyzer_mirror_artifact"],
    )
    return app.test_client()


def test_no_validated_bundle_returns_red_non_404_on_status_health_alarm():
    """S1: active is None → 200 available=false NO_VALIDATED_ANALYZER_BUNDLE."""
    with tempfile.TemporaryDirectory() as tmp:
        legacy = Path(tmp) / "legacy"
        legacy.mkdir()
        _assert_no_bundle_red(_client(active=None, legacy=legacy), legacy_preserved=True)


def test_legacy_data_preserved_false_when_no_legacy_dir():
    with tempfile.TemporaryDirectory() as tmp:
        missing = Path(tmp) / "no-such-legacy-dir"
        assert not missing.exists()
        _assert_no_bundle_red(_client(active=None, legacy=missing), legacy_preserved=False)


def test_valid_bundle_status_still_200_available_true():
    """S2: valid v2 status.json → 200 available=true schema v2."""
    with tempfile.TemporaryDirectory() as tmp:
        gen = Path(tmp) / "generation-ok"
        gen.mkdir()
        (gen / "status.json").write_text(
            json.dumps({
                "schema": _SCHEMA,
                "complete": True,
                "generation": "g1",
            }),
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
        assert body["schema"] == _SCHEMA
        assert body.get("reason") != _REASON
        assert body.get("reason") != _UNREADABLE
        assert response.headers.get("Cache-Control") == "no-store"


def test_wrong_schema_while_pointer_set_is_unreadable_red():
    """S3: wrong/missing schema on disk while pointer set → not soft-GREEN."""
    with tempfile.TemporaryDirectory() as tmp:
        gen = Path(tmp) / "generation-wrong-schema"
        gen.mkdir()
        (gen / "status.json").write_text(
            json.dumps({"schema": "analyzer_mirror_bundle_v1", "complete": True}),
            encoding="utf-8",
        )
        legacy = Path(tmp) / "legacy"
        legacy.mkdir()
        _assert_unreadable_red(_client(active=gen, legacy=legacy), legacy_preserved=True)


def test_unreadable_status_json_returns_200_analyzer_status_unreadable():
    """S4/B1: unreadable status.json → 200 available=false ANALYZER_STATUS_UNREADABLE."""
    with tempfile.TemporaryDirectory() as tmp:
        gen = Path(tmp) / "generation-broken"
        gen.mkdir()
        (gen / "status.json").write_text("{not-json", encoding="utf-8")
        legacy = Path(tmp) / "legacy"
        legacy.mkdir()
        _assert_unreadable_red(_client(active=gen, legacy=legacy), legacy_preserved=True)


def test_unauth_still_401():
    """S5."""
    with tempfile.TemporaryDirectory() as tmp:
        legacy = Path(tmp)
        client = _client(active=None, legacy=legacy, admin=False, monitor=False)
        response = client.get("/api/analyzer-mirror/status")
        assert response.status_code == 401


def test_monitor_classifier_200_available_false_is_red():
    """S6/B3: monitor sees 200 + available=false as RED (not GREEN / not route-missing)."""
    _, ns = _client_from_source(
        BOT,
        active=None,
        legacy=Path("/tmp"),
    )
    classify = ns["classify_analyzer_mirror_status_for_monitor"]
    assert classify(200, {"available": False, "status": "RED", "reason": _REASON}) == "RED"
    assert classify(200, {"available": False, "status": "RED", "reason": _UNREADABLE}) == "RED"
    assert classify(404, {"available": False}) == "RED_MISLABEL_404"
    assert classify(404, {"available": False}) != "GREEN"
    # Mutation: treating available=false as healthy must fail this contract.
    with pytest.raises(AssertionError):
        assert classify(200, {"available": False}) == "GREEN"
    with pytest.raises(AssertionError):
        assert classify(200, {"available": False}) == "ROUTE_MISSING"


def test_mutation_proof_restore_404_fails_red_contract():
    """Recorded mutant run: restore HTTP 404 on `active is None` → RED contract assert fails."""
    assert _NO_BUNDLE_200 in BOT
    mutant = BOT.replace(_NO_BUNDLE_200, _NO_BUNDLE_404_MUTANT, 1)
    assert mutant != BOT
    with tempfile.TemporaryDirectory() as tmp:
        legacy = Path(tmp) / "legacy"
        legacy.mkdir()
        client, _ns = _client_from_source(mutant, active=None, legacy=legacy)
        with pytest.raises(AssertionError):
            _assert_no_bundle_red(client)
        response = client.get("/api/analyzer-mirror/status")
        assert response.status_code == 404
        assert response.json.get("status") != "RED"


def test_mutation_proof_unreadable_404_restored_fails():
    """S4 mutation: restore HTTP 404 for unreadable → contract FAIL (B1)."""
    assert _UNREADABLE_200 in BOT
    mutant = BOT.replace(_UNREADABLE_200, _UNREADABLE_404_MUTANT, 1)
    assert mutant != BOT
    with tempfile.TemporaryDirectory() as tmp:
        gen = Path(tmp) / "generation-broken"
        gen.mkdir()
        (gen / "status.json").write_text("{not-json", encoding="utf-8")
        legacy = Path(tmp) / "legacy"
        legacy.mkdir()
        client, _ns = _client_from_source(mutant, active=gen, legacy=legacy)
        with pytest.raises(AssertionError):
            _assert_unreadable_red(client)
        response = client.get("/api/analyzer-mirror/status")
        assert response.status_code == 404


def test_mutation_proof_health_amber_fails_red_contract():
    """Recorded mutant run: payload health AMBER → health==RED assert fails (M3)."""
    needle = '"health": "RED",'
    assert BOT.count(needle) >= 1
    mutant = BOT.replace(needle, '"health": "AMBER",', 1)
    assert mutant != BOT
    with tempfile.TemporaryDirectory() as tmp:
        legacy = Path(tmp) / "legacy"
        legacy.mkdir()
        client, _ns = _client_from_source(mutant, active=None, legacy=legacy)
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
        client, _ns = _client_from_source(mutant, active=None, legacy=legacy)
        with pytest.raises(AssertionError):
            _assert_no_bundle_red(client)
        assert client.get("/api/analyzer-mirror/status").json["reason"] == "NO_BUNDLE"


def test_summary_no_bundle_returns_503():
    """Behaviour: /api/analyzer/summary stays 503 with no validated bundle (Fly)."""
    client, prev = _summary_client(active=None, fly=True)
    try:
        response = client.get("/api/analyzer/summary")
        assert response.status_code == 503
        body = response.json
        assert body.get("ok") is False
        assert body.get("error") == _NO_VALID
        assert "bundle" not in body
        assert response.headers.get("Cache-Control") == "no-store"
    finally:
        if prev is None:
            os.environ.pop("FLY_APP_NAME", None)
        else:
            os.environ["FLY_APP_NAME"] = prev


def test_analysis_routes_no_bundle_return_503():
    """Behaviour: /analysis/ and /analysis/<path> stay 503 with no bundle (no HTML)."""
    client = _analysis_client(active=None, authed=True)
    for path in ("/analysis/", "/analysis/index.html"):
        response = client.get(path)
        assert response.status_code == 503, path
        body = response.json
        assert "error" in body
        assert "no complete validated analyzer bundle" in body["error"] or body[
            "error"
        ] == _NO_VALID
        assert b"<!DOCTYPE" not in (response.data or b"")
        assert b"<html" not in (response.data or b"").lower()


def test_mutation_proof_summary_503_softened_is_caught():
    """Recorded mutant run: soften summary 503 → 200 must be detected."""
    original = _extract_fn("api_analyzer_summary")
    assert "return resp, 503" in original
    mutant_src = original.replace("return resp, 503", "return resp, 200", 1)
    assert mutant_src != original

    app = Flask("summary-mutant")
    namespace = {
        "app": app,
        "request": request,
        "jsonify": jsonify,
        "os": os,
        "_active_analyzer_mirror_dir": lambda: None,
        "_ANALYZER_BUNDLE_NO_VALID": _NO_VALID,
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


def test_mutation_proof_analysis_503_softened_is_caught():
    """Recorded mutant run: soften /analysis/ 503 → 200 must fail the behaviour assert."""
    original = _extract_fn("analyzer_mirror_dashboard_index")
    assert "), 503" in original
    mutant_src = original.replace("), 503", "), 200", 1)
    assert mutant_src != original

    app = Flask("analysis-mutant")
    namespace = {
        "app": app,
        "request": request,
        "jsonify": jsonify,
        "make_response": make_response,
        "_analyzer_view_authed": lambda: True,
        "_active_analyzer_mirror_dir": lambda: None,
        "_ANALYZER_BUNDLE_NO_VALID": _NO_VALID,
    }
    exec(compile(mutant_src, "mutant_analysis", "exec"), namespace)
    app.add_url_rule(
        "/analysis/",
        view_func=namespace["analyzer_mirror_dashboard_index"],
    )
    response = app.test_client().get("/analysis/")
    assert response.status_code == 200  # mutant
    with pytest.raises(AssertionError):
        assert response.status_code == 503


def test_mutation_proof_legacy_false_flipped_is_caught():
    """Recorded mutant run: force legacy_data_preserved True → false-path assert fails."""
    needle = "legacy_preserved = _analyzer_mirror_dir().exists()"
    assert needle in BOT
    mutant = BOT.replace(
        needle,
        "legacy_preserved = True  # mutant: ignore missing legacy dir",
        1,
    )
    assert mutant != BOT
    with tempfile.TemporaryDirectory() as tmp:
        missing = Path(tmp) / "no-such-legacy-dir"
        assert not missing.exists()
        client, _ns = _client_from_source(mutant, active=None, legacy=missing)
        with pytest.raises(AssertionError):
            _assert_no_bundle_red(client, legacy_preserved=False)
        assert client.get("/api/analyzer-mirror/status").json[
            "legacy_data_preserved"
        ] is True


def test_s7_reverted_pre_b1_unreadable_404_fails_new_contract():
    """S7/B3: real reverted pre-B1 unreadable→404 code fails the new 200 contract."""
    # Revert only the unreadable branch to the pre-B1 404 shape (real code, not string-search pass).
    pre_b1 = BOT.replace(_UNREADABLE_200, _UNREADABLE_404_MUTANT, 1)
    assert pre_b1 != BOT
    assert "ANALYZER_STATUS_UNREADABLE" in BOT
    with tempfile.TemporaryDirectory() as tmp:
        gen = Path(tmp) / "generation-broken"
        gen.mkdir()
        (gen / "status.json").write_text("{not-json", encoding="utf-8")
        legacy = Path(tmp) / "legacy"
        legacy.mkdir()
        client, _ns = _client_from_source(pre_b1, active=gen, legacy=legacy)
        with pytest.raises(AssertionError):
            _assert_unreadable_red(client)
        assert client.get("/api/analyzer-mirror/status").status_code == 404


if __name__ == "__main__":
    test_no_validated_bundle_returns_red_non_404_on_status_health_alarm()
    test_legacy_data_preserved_false_when_no_legacy_dir()
    test_valid_bundle_status_still_200_available_true()
    test_wrong_schema_while_pointer_set_is_unreadable_red()
    test_unreadable_status_json_returns_200_analyzer_status_unreadable()
    test_unauth_still_401()
    test_monitor_classifier_200_available_false_is_red()
    test_summary_no_bundle_returns_503()
    test_analysis_routes_no_bundle_return_503()
    test_mutation_proof_restore_404_fails_red_contract()
    test_mutation_proof_unreadable_404_restored_fails()
    test_mutation_proof_health_amber_fails_red_contract()
    test_mutation_proof_reason_drift_fails_red_contract()
    test_mutation_proof_summary_503_softened_is_caught()
    test_mutation_proof_analysis_503_softened_is_caught()
    test_mutation_proof_legacy_false_flipped_is_caught()
    test_s7_reverted_pre_b1_unreadable_404_fails_new_contract()
    print("analyzer mirror status label (ca14dba80fca) checks passed")
