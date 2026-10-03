"""Laptop analyzer -> Fly mirror publisher (analyzer_mirror_bundle_v2)."""

import ast
import hashlib
import io
import json
import re
import sys
import urllib.error
import zipfile
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import publish_analyzer_mirror as pub  # noqa: E402

TOKEN = "test-admin-token-never-printed"
GENERATED_AT = "2026-10-03T00:57:40.516531+00:00"


def _fly_validator():
    """The receiver's own validation functions, extracted from bot.py without importing it."""
    tree = ast.parse((ROOT / "services" / "btc-conservative-agent" / "bot.py").read_text(encoding="utf-8"))
    wanted = {"_safe_analyzer_bundle_members", "_validated_analyzer_bundle_manifest"}
    nodes = [
        node for node in tree.body
        if (isinstance(node, ast.FunctionDef) and node.name in wanted)
        or (isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id.startswith("_ANALYZER_BUNDLE_"))
    ]
    namespace = {"re": re, "json": json, "hashlib": hashlib, "zipfile": zipfile, "Path": Path, "datetime": datetime}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "bot.py", "exec"), namespace)  # noqa: S102
    return namespace


def _report_root(tmp_path, *, extra_reports=()):
    root = tmp_path / "analyzer"
    (root / "reports").mkdir(parents=True)
    text = ["executive_summary.txt", "analysis_dashboard.html", "analyzer_run.log"]
    for name in text:
        (root / name).write_text(f"{name} body", encoding="utf-8")
    reports = []
    for name, body in (("tile_report.json", b'{"ok": true}'), ("shadow.jsonl.gz", b"\x1f\x8b"), *extra_reports):
        (root / "reports" / name).write_bytes(body)
        reports.append({"file": name, "size_bytes": len(body)})
    manifest = {
        "schema": "report_manifest_v1", "analyzer_sync_id": "v31-test", "analyzer_version": "v31-test",
        "generated_at": GENERATED_AT, "data_scope": "session", "session_scope": "session",
        "generation_id": "gen-123", "source_revision": "29742de53a5cacc91d7386a96351e0a2b3a15d32",
        "analysis_provenance": {"cohort_schema": "analysis_cohorts_v1",
                                "generation_revision": "07e607a38c8bb2627d58ed9b9da5fb76f15b13c5"},
        "report_count": len(reports), "reports": reports, "text_artifacts": text,
    }
    (root / "report_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return root


def _validate(bundle_path):
    ns = _fly_validator()
    with zipfile.ZipFile(bundle_path) as archive:
        members = ns["_safe_analyzer_bundle_members"](archive)
        return ns["_validated_analyzer_bundle_manifest"](archive, members), archive.read("report_manifest.json")


def test_bundle_passes_the_fly_receiver_and_discloses_every_exclusion(tmp_path, monkeypatch):
    monkeypatch.setattr(pub, "MAX_MEMBER_BYTES", 64)
    root = _report_root(tmp_path, extra_reports=(("huge_report.json", b"x" * 100),))
    genome = tmp_path / "genome_grid_report.json"
    genome.write_text('{"rows": []}', encoding="utf-8")
    work = tmp_path / "work"
    work.mkdir()
    built = pub.build_bundle(root, work, supplemental=[genome])
    manifest, embedded = _validate(built["bundle_path"])
    paths = {row["path"] for row in manifest["files"]}
    assert "reports/tile_report.json" in paths and "reports/genome_grid_report.json" in paths
    assert "reports/shadow.jsonl.gz" not in paths and "reports/huge_report.json" not in paths
    subset = json.loads(embedded)["mirror_publication"]
    reasons = {row["path"]: row["reason"] for row in subset["excluded"]}
    assert reasons == {"reports/shadow.jsonl.gz": "SUFFIX_NOT_ACCEPTED_BY_FLY", "reports/huge_report.json": "MEMBER_OVER_50MB"}
    source_sha = hashlib.sha256((root / "report_manifest.json").read_bytes()).hexdigest()
    assert subset["source_report_manifest_sha256"] == source_sha == manifest["original_report_manifest_sha256"]
    assert manifest["mirror_subset"] is True and manifest["analyzer_generated_at"] == GENERATED_AT


def test_a_generation_rewritten_during_snapshot_is_refused(tmp_path):
    root = _report_root(tmp_path)
    (root / "reports" / "tile_report.json").write_bytes(b'{"ok": false, "longer": 1}')
    work = tmp_path / "work"
    work.mkdir()
    with pytest.raises(pub.PublishError) as exc:
        pub.build_bundle(root, work)
    assert exc.value.code == "GENERATION_CHANGED_DURING_SNAPSHOT"


class _Response(io.BytesIO):
    def __init__(self, status, payload):
        super().__init__(json.dumps(payload).encode())
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _Fly:
    """Scripted Fly: upload statuses in order, then panels for one generation."""

    def __init__(self, upload_statuses, served_generated_at=GENERATED_AT):
        self.upload_statuses = list(upload_statuses)
        self.served = served_generated_at
        self.requests = []

    def __call__(self, request, timeout):
        self.requests.append(request)
        url = request.full_url
        if url.endswith("/api/data-sync/analyzer-report"):
            status = self.upload_statuses.pop(0)
            if status != 200:
                raise urllib.error.HTTPError(url, status, "err", {}, io.BytesIO(b'{"error":"x"}'))
            return _Response(200, {"ok": True, "generation": "generation-abc"})
        if url.endswith("/api/analyzer-mirror/status"):
            return _Response(200, {"available": True})
        return _Response(200, {"mirror_available": True,
                               "mirror_status": {"analyzer_generated_at": self.served, "generation": "generation-abc"}})


def _args(root, tmp_path, **overrides):
    values = {"report_root": str(root), "base_url": "https://fly.example", "vault_env": "",
              "receipt": str(tmp_path / "receipt.json"), "source_data_revision": "", "supplemental": [],
              "work_dir": str(tmp_path), "attempts": 3, "verify_attempts": 2, "force": False, "dry_run": False}
    values.update(overrides)
    return type("Args", (), values)()


def test_publish_retries_transport_failures_and_verifies_both_panels(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("BOT_ADMIN_TOKEN", TOKEN)
    root = _report_root(tmp_path)
    fly = _Fly([503, 200])
    receipt = pub.publish(_args(root, tmp_path), opener=fly, sleep=lambda _s: None)
    assert receipt["state"] == "PUBLISHED" and receipt["upload"]["attempt"] == 2
    assert receipt["verification"]["panels"]["genome"]["current"] is True
    uploads = [r for r in fly.requests if r.full_url.endswith("/analyzer-report")]
    assert all(r.get_header("X-bot-admin-token") == TOKEN for r in uploads)
    on_disk = (tmp_path / "receipt.json").read_text(encoding="utf-8")
    assert TOKEN not in on_disk and TOKEN not in capsys.readouterr().out
    again = pub.publish(_args(root, tmp_path), opener=_Fly([]), sleep=lambda _s: None)
    assert again["state"] == "ALREADY_PUBLISHED"


def test_a_rejected_bundle_is_not_retried_and_keeps_the_last_published(tmp_path, monkeypatch):
    monkeypatch.setenv("BOT_ADMIN_TOKEN", TOKEN)
    root = _report_root(tmp_path)
    (tmp_path / "receipt.json").write_text(json.dumps({"state": "PUBLISHED", "analyzer_generated_at": "old",
                                                       "published_at": "t0"}), encoding="utf-8")
    fly = _Fly([400, 200])
    receipt = pub.publish(_args(root, tmp_path), opener=fly, sleep=lambda _s: None)
    assert receipt["state"] == "FAILED" and receipt["error"] == "UPLOAD_FAILED" and receipt["exit_code"] == 3
    assert len([r for r in fly.requests if r.full_url.endswith("/analyzer-report")]) == 1
    assert receipt["last_published"]["analyzer_generated_at"] == "old"


def test_panels_still_serving_an_older_generation_fail_verification(tmp_path, monkeypatch):
    monkeypatch.setenv("BOT_ADMIN_TOKEN", TOKEN)
    root = _report_root(tmp_path)
    receipt = pub.publish(_args(root, tmp_path), opener=_Fly([200], served_generated_at="2026-10-02T00:00:00+00:00"),
                          sleep=lambda _s: None)
    assert receipt["state"] == "UPLOADED_UNVERIFIED" and receipt["error"] == "FLY_PANELS_NOT_CURRENT"
    assert receipt["exit_code"] == 4


def test_missing_token_fails_closed_without_uploading(tmp_path, monkeypatch):
    monkeypatch.delenv("BOT_ADMIN_TOKEN", raising=False)
    root = _report_root(tmp_path)
    fly = _Fly([200])
    receipt = pub.publish(_args(root, tmp_path), opener=fly, sleep=lambda _s: None)
    assert receipt["error"] == "BOT_ADMIN_TOKEN_UNAVAILABLE" and fly.requests == []
