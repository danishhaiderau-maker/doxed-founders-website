import ast
import hashlib
import hmac
import json
import math
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parent
BOT_SOURCE = (ROOT / "bot.py").read_text(encoding="utf-8")
ANALYZER_SOURCE = (ROOT / "analyzer_research_engine_v62.py").read_text(encoding="utf-8")


def _compile(source, name, namespace):
    tree = ast.parse(source)
    node = next(
        item for item in tree.body
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == name
    )
    module = ast.Module(body=[node], type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, name, "exec"), namespace)
    return namespace[name]


def _publication_fixture():
    fly_revision = "a" * 12
    analyzer_revision = "d" * 40
    epoch = "epoch-v22-current"
    tile_signature = "b" * 64
    evidence_revision = "c" * 64
    fresh_epoch = "epoch-fresh-current"
    settings_signature = "gap=3|chase=2,3,4"
    provenance = {
        "generation_revision": analyzer_revision,
        "analyzer_revision": analyzer_revision,
        "source_revision": fly_revision,
        "deployed_revision": fly_revision,
        "dataset_epoch": epoch,
        "config_signature": tile_signature,
        "source_data_revision": evidence_revision,
        "fresh_epoch_id": fresh_epoch,
    }
    report_manifest = {
        "schema": "report_manifest_v1",
        "analyzer_sync_id": "analyzer-v1",
        "analyzer_version": "v62",
        "generated_at": "2026-09-21T01:00:00+00:00",
        "generation_revision": analyzer_revision,
        "analyzer_revision": analyzer_revision,
        "source_revision": fly_revision,
        "deployed_revision": fly_revision,
        "dataset_epoch": epoch,
        "config_signature": tile_signature,
        "source_data_revision": evidence_revision,
        "analysis_provenance": {"cohort_schema": "analysis_cohorts_v1", **provenance},
        "fresh_epoch": {
            "status": "BOUND",
            "epoch_id": fresh_epoch,
            "cutoff_utc": "2026-09-20T00:00:00+00:00",
        },
        "data_scope": "session",
        "report_count": 2,
        "reports": [
            {"file": "chase_effectiveness_report.json"},
            {"file": "chase_attribution_report.json"},
        ],
        "text_artifacts": ["analysis_dashboard.html"],
    }
    buckets = {}
    for index, key in enumerate(("0", "1", "2", "3", "4", "5+")):
        trades = index + 1
        wins = index // 2
        pnl = round((index - 2) * 1.25, 2)
        buckets[key] = {
            "trades": trades,
            "wins": wins,
            "win_rate_pct": round(100.0 * wins / trades, 1),
            "sum_pnl_usd": pnl,
            "ev_usd": round(pnl / trades, 2),
        }
    report = {
        "schema": "chase_effectiveness_v1",
        "generated_at": "2026-09-21T00:59:00+00:00",
        "metric_basis": {
            "pnl_field": "net_pnl_usd",
            "pnl_basis": "AFTER_COST_NET_PNL",
            "ev_denominator": "current_settings_bucket_attributions_with_finite_net_pnl",
            "bucket_field": "chase_count",
        },
        "metrics_status": "VERIFIED_CURRENT_SETTINGS_COHORT",
        "execution_settings_binding": {
            "schema": "execution_settings_binding_v1",
            "signature": settings_signature,
            "effective_epoch": 1789940000.0,
            "gap_buckets": ["3"],
            "chase_buckets": ["2", "3", "4"],
        },
        "analysis_provenance": provenance,
        **{key: value for key, value in provenance.items() if key != "fresh_epoch_id"},
        "epoch_id": fresh_epoch,
        "buckets": buckets,
    }
    attribution = {
        "schema": "chase_attribution_v1",
        "generated_at": report["generated_at"],
        "analysis_provenance": provenance,
        **{key: value for key, value in provenance.items() if key != "fresh_epoch_id"},
        "epoch_id": fresh_epoch,
        "totals": {"chase_assisted_fills": 4, "saved_fills_heuristic": 2, "ttl_expired": 1},
        "overnight_watch": {"total_fills": 9},
    }
    bundle = {
        "schema": "analyzer_mirror_bundle_v2",
        "snapshot_id": "snapshot-current",
        "analyzer_run_id": "analyzer-v1",
        "analyzer_generated_at": report_manifest["generated_at"],
        "source_data_revision": fly_revision,
        "analyzer_generation_revision": analyzer_revision,
        "analyzer_version": "v62",
        "cohort_schema": "analysis_cohorts_v1",
        "data_scope": "session",
        "source_report_manifest_sha256": "pending",
    }
    return {
        "bundle": bundle,
        "report_manifest": report_manifest,
        "report": report,
        "attribution": attribution,
        "fly_revision": fly_revision,
        "analyzer_revision": analyzer_revision,
        "epoch": epoch,
        "tile_signature": tile_signature,
        "settings_signature": settings_signature,
    }


def _install_valid_bundle(root: Path, fixture: dict):
    generation = root / "analyzer_generations" / "generation-test"
    reports = generation / "reports"
    reports.mkdir(parents=True)
    payloads = {
        "analysis_dashboard.html": b"<html>current</html>",
        "report_manifest.json": json.dumps(fixture["report_manifest"]).encode(),
        "reports/chase_effectiveness_report.json": json.dumps(fixture["report"]).encode(),
        "reports/chase_attribution_report.json": json.dumps(fixture["attribution"]).encode(),
    }
    for relative, payload in payloads.items():
        target = generation / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
    bundle = dict(fixture["bundle"])
    bundle["source_report_manifest_sha256"] = fixture.get(
        "source_report_hash_override"
    ) or hashlib.sha256(payloads["report_manifest.json"]).hexdigest()
    bundle["files"] = [
        {"path": relative, "size_bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}
        for relative, payload in payloads.items()
    ]
    (generation / "bundle_manifest.json").write_text(json.dumps(bundle), encoding="utf-8")
    status = {
        **{key: value for key, value in bundle.items() if key != "files"},
        "complete": True,
        "generation": generation.name,
    }
    (generation / "status.json").write_text(json.dumps(status), encoding="utf-8")
    (root / "analyzer_current.json").write_text(
        json.dumps({"schema": "analyzer_current_v1", "generation": generation.name}),
        encoding="utf-8",
    )


def _loader(tmp_path, fixture=None):
    fixture = fixture or _publication_fixture()
    namespace = {
        "os": os,
        "json": json,
        "math": math,
        "hashlib": hashlib,
        "hmac": hmac,
        "re": re,
        "Path": Path,
        "datetime": datetime,
        "time": SimpleNamespace(time=lambda: fixture.get(
            "now", datetime.fromisoformat("2026-09-21T01:01:00+00:00").timestamp()
        )),
        "_ANALYZER_BUNDLE_MANIFEST": "bundle_manifest.json",
        "_ANALYZER_BUNDLE_SCHEMA": "analyzer_mirror_bundle_v2",
        "_ANALYZER_BUNDLE_ALLOWED_SUFFIXES": frozenset((".html", ".txt", ".json", ".log")),
        "_ANALYZER_BUNDLE_MAX_MEMBER_BYTES": 50 * 1024 * 1024,
        "CHASE_EFFECTIVENESS_REPORT_FILE": "chase_effectiveness_report.json",
        "CHASE_ATTRIBUTION_REPORT_FILE": "chase_attribution_report.json",
        "CHASE_EXECUTION_BUCKET_ORDER": (
            "0_chases", "1_chase", "2_chases", "3_chases", "4_chases", "5+_chases",
        ),
        "_data_sync_volume_root": lambda: tmp_path,
        "_runtime_git_rev": lambda: fixture["fly_revision"],
        "_collector_v22_epoch_id": lambda: fixture["epoch"],
        "active_tile_registry_signature": lambda: fixture["tile_signature"],
        "_enabled_execution_settings": lambda: {"gap_buckets": ["3"], "chase_buckets": ["2", "3", "4"]},
        "_execution_settings_signature": lambda value: (
            "gap=" + ",".join(value["gap_buckets"]) + "|chase=" + ",".join(value["chase_buckets"])
        ),
    }
    for name in (
        "_analyzer_generations_dir", "_analyzer_current_pointer_path",
        "_valid_analyzer_generation", "_recover_latest_analyzer_generation",
        "_active_analyzer_mirror_dir", "_chase_analytics_identity_error",
        "_load_chase_analytics_snapshot",
    ):
        namespace[name] = _compile(BOT_SOURCE, name, namespace)
    if fixture.get("install", True):
        _install_valid_bundle(tmp_path, fixture)
    return namespace["_load_chase_analytics_snapshot"]()


def test_absent_malformed_and_loose_orphan_reports_are_unavailable(tmp_path):
    absent_fixture = _publication_fixture()
    absent_fixture["install"] = False
    absent = _loader(tmp_path / "absent", absent_fixture)
    assert absent["status"] == "UNAVAILABLE"
    assert absent["unavailable_reason"] == "NO_VALIDATED_ANALYZER_BUNDLE"
    assert all(row["trades"] is None for row in absent["buckets"])

    orphan_root = tmp_path / "orphan"
    orphan_root.mkdir()
    (orphan_root / "chase_effectiveness_report.json").write_text("{}", encoding="utf-8")
    orphan_fixture = _publication_fixture()
    orphan_fixture["install"] = False
    orphan = _loader(orphan_root, orphan_fixture)
    assert orphan["unavailable_reason"] == "NO_VALIDATED_ANALYZER_BUNDLE"

    malformed_fixture = _publication_fixture()
    malformed_fixture["report"]["buckets"]["2"]["ev_usd"] = float("nan")
    malformed = _loader(tmp_path / "malformed", malformed_fixture)
    assert malformed["status"] == "UNAVAILABLE"
    assert malformed["unavailable_reason"] == "BUCKET_METRICS_INVALID"

    hash_fixture = _publication_fixture()
    hash_fixture["source_report_hash_override"] = "0" * 64
    bad_hash = _loader(tmp_path / "hash", hash_fixture)
    assert bad_hash["status"] == "UNAVAILABLE"
    assert bad_hash["unavailable_reason"] == "SOURCE_REPORT_MANIFEST_HASH_MISMATCH"


def test_source_analyzer_epoch_tile_settings_and_stale_mismatches_fail_closed(tmp_path):
    cases = []
    source = _publication_fixture()
    source["bundle"]["source_data_revision"] = "e" * 12
    cases.append(("source", source, "FLY_SOURCE_REVISION_MISMATCH"))
    analyzer = _publication_fixture()
    analyzer["bundle"]["analyzer_generation_revision"] = "f" * 40
    cases.append(("analyzer", analyzer, "ANALYZER_GENERATION_REVISION_MISMATCH"))
    epoch = _publication_fixture()
    epoch["report_manifest"]["dataset_epoch"] = "epoch-old"
    cases.append(("epoch", epoch, "DATASET_EPOCH_MISMATCH"))
    tile = _publication_fixture()
    tile["report_manifest"]["config_signature"] = "e" * 64
    cases.append(("tile", tile, "CONFIG_SIGNATURE_MISMATCH"))
    settings = _publication_fixture()
    settings["report"]["execution_settings_binding"]["signature"] = "gap=5+|chase=5+"
    cases.append(("settings", settings, "EXECUTION_SETTINGS_MISMATCH_OR_UNBOUND"))
    stale = _publication_fixture()
    stale["report"]["generated_at"] = "2026-09-19T23:59:00+00:00"
    cases.append(("stale", stale, "REPORT_TIMESTAMP_OUTSIDE_CURRENT_GENERATION"))
    for label, fixture, expected in cases:
        snapshot = _loader(tmp_path / label, fixture)
        assert snapshot["status"] == "UNAVAILABLE"
        assert snapshot["unavailable_reason"] == expected
        assert all(row["ev_usd"] is None for row in snapshot["buckets"])


def test_verified_bundle_keeps_fly_and_analyzer_revisions_distinct_and_metrics_real(tmp_path):
    fixture = _publication_fixture()
    snapshot = _loader(tmp_path, fixture)
    assert fixture["fly_revision"] != fixture["analyzer_revision"]
    assert snapshot["status"] == "VERIFIED_RECENT_SNAPSHOT"
    assert snapshot["live_data_coverage_verified"] is False
    assert snapshot["buckets"][3] == {
        "bucket": "3_chases", "trades": 4, "win_rate_pct": 25.0,
        "sum_pnl_usd": 1.25, "ev_usd": 0.31,
    }
    assert snapshot["assisted"] == 4 and snapshot["assisted_total"] == 9
    assert snapshot["provenance"]["fly_source_revision"] == fixture["fly_revision"]
    assert snapshot["provenance"]["analyzer_generation_revision"] == fixture["analyzer_revision"]
    assert snapshot["provenance"]["execution_settings_signature"] == fixture["settings_signature"]


def test_after_cost_stats_exclude_missing_and_nonfinite_pnl_and_ignore_win_flag():
    namespace = {"math": math}
    namespace["_chase_count_bucket"] = _compile(ANALYZER_SOURCE, "_chase_count_bucket", namespace)
    stats = _compile(ANALYZER_SOURCE, "_chase_bucket_stats", namespace)([
        {"chase_count": 2, "net_pnl_usd": None, "win": True},
        {"chase_count": 2, "net_pnl_usd": float("nan"), "win": True},
        {"chase_count": 2, "net_pnl_usd": float("inf"), "win": True},
        {"chase_count": 2, "net_pnl_usd": -2.0, "win": True},
        {"chase_count": 2, "net_pnl_usd": 1.0, "win": False},
    ])
    assert stats["2"]["trades"] == 2
    assert stats["2"]["wins"] == 1
    assert stats["2"]["win_rate_pct"] == 50.0
    assert stats["2"]["sum_pnl_usd"] == -1.0
    assert stats["2"]["ev_usd"] == -0.5


def test_same_identity_publication_expires_and_future_or_naive_dates_fail_closed(tmp_path):
    old = _publication_fixture()
    old["now"] = datetime.fromisoformat("2026-09-21T02:00:01+00:00").timestamp()
    assert _loader(tmp_path / "old", old)["unavailable_reason"] == "ANALYZER_PUBLICATION_STALE"

    future = _publication_fixture()
    future["now"] = datetime.fromisoformat("2026-09-21T00:58:00+00:00").timestamp()
    assert _loader(tmp_path / "future", future)["unavailable_reason"] == "REPORT_TIMESTAMP_IN_FUTURE"

    naive = _publication_fixture()
    naive["report"]["generated_at"] = "2026-09-21T00:59:00"
    assert _loader(tmp_path / "naive", naive)["unavailable_reason"] == "REPORT_TIMESTAMP_INVALID"

    slow_publication = _publication_fixture()
    slow_publication["report"]["generated_at"] = "2026-09-20T23:59:00+00:00"
    assert _loader(tmp_path / "slow", slow_publication)["unavailable_reason"] == "ANALYZER_PUBLICATION_STALE"


def test_report_cohort_requires_finite_cost_and_current_settings_epoch(tmp_path):
    binding = {
        "schema": "execution_settings_binding_v1",
        "signature": "gap=3|chase=2,3,4",
        "effective_epoch": 100.0,
        "gap_buckets": ["3"],
        "chase_buckets": ["2", "3", "4"],
    }
    namespace = {
        "os": os,
        "json": json,
        "math": math,
        "datetime": datetime,
        "timezone": timezone,
        "ANALYZER_SYNC_ID": "analyzer-v1",
        "EXPECTED_BOT_VERSION": "test",
        "PIPELINE_ENFORCEMENT_TAG": "[TEST]",
        "CHASE_ATTRIBUTION_REPORT_FILE": "chase_attribution_report.json",
        "CHASE_EFFECTIVENESS_REPORT_FILE": "chase_effectiveness_report.json",
        "load_research_session": lambda: {},
        "_shadow_scope_label": lambda _session: "SESSION",
        "_current_execution_settings_binding": lambda _session: binding,
        "analyzer_report_path": lambda _name: str(tmp_path / _name),
    }
    namespace["_chase_count_bucket"] = _compile(ANALYZER_SOURCE, "_chase_count_bucket", namespace)
    namespace["_chase_bucket_stats"] = _compile(ANALYZER_SOURCE, "_chase_bucket_stats", namespace)
    report = _compile(ANALYZER_SOURCE, "chase_effectiveness_report", namespace)(
        session={},
        chase_payload={"trades": [
            {"chase_count": 2, "net_pnl_usd": -2.0, "win": True, "settings_observation_epoch": 101.0},
            {"chase_count": 2, "net_pnl_usd": 1.0, "win": False, "settings_observation_epoch": 102.0},
            {"chase_count": 2, "net_pnl_usd": None, "win": True, "settings_observation_epoch": 103.0},
            {"chase_count": 2, "net_pnl_usd": float("nan"), "win": True, "settings_observation_epoch": 104.0},
            {"chase_count": 2, "net_pnl_usd": 20.0, "win": True, "settings_observation_epoch": 99.0},
        ]},
    )
    assert report["metrics_status"] == "VERIFIED_CURRENT_SETTINGS_COHORT"
    assert report["execution_settings_binding"] == binding
    assert report["cohort_counts"] == {
        "input_attributions": 5,
        "included_finite_net_pnl": 2,
        "exclusions": {
            "MISSING_OR_NONFINITE_NET_PNL": 2,
            "MISSING_OR_INVALID_SETTINGS_TIME": 0,
            "BEFORE_CURRENT_SETTINGS_EPOCH": 1,
        },
    }
    assert report["buckets"]["2"]["trades"] == 2
    assert report["buckets"]["2"]["wins"] == 1
    assert report["buckets"]["2"]["ev_usd"] == -0.5


def test_settings_binding_recomputes_canonical_runtime_signature():
    class FakePandas:
        @staticmethod
        def isna(_value):
            return False

    rows = [
        {"epoch": 20.0, "signature": "forged", "gap_buckets": ["3"], "chase_buckets": ["2"]},
        {"epoch": 10.0, "signature": "gap=3|chase=2,3,4", "gap_buckets": ["3"], "chase_buckets": ["2", "3", "4"]},
    ]
    namespace = {
        "math": math,
        "pd": FakePandas,
        "_session_start_ts": lambda _session: datetime.fromtimestamp(1, tz=timezone.utc),
        "_load_jsonl_rows": lambda _path: rows,
    }
    binding = _compile(ANALYZER_SOURCE, "_current_execution_settings_binding", namespace)({})
    assert binding["signature"] == "gap=3|chase=2,3,4"
    assert binding["effective_epoch"] == 10.0


def test_producer_and_ui_disclose_finite_after_cost_basis_and_unavailable_state():
    assert '"ev_denominator": "current_settings_bucket_attributions_with_finite_net_pnl"' in ANALYZER_SOURCE
    assert '"metrics_status": (' in ANALYZER_SOURCE
    assert 'id="chaseAnalyticsStatus"' in BOT_SOURCE
    assert "ch.status === 'VERIFIED_RECENT_SNAPSHOT'" in BOT_SOURCE
    assert "<strong style=\"color:#f59e0b\">UNAVAILABLE</strong>" in BOT_SOURCE
