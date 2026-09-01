"""Focused regressions for honest, current analyzer dashboard downloads."""

from __future__ import annotations

import csv
import hashlib
import importlib.util
import io
import json
import sqlite3
import sys
import tempfile
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent
RESEARCH = ROOT / "research"
sys.path.insert(0, str(RESEARCH))

import research_dashboard as dashboard


def _load_agent_module(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / filename)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


build_chatgpt = _load_agent_module(
    "_test_chatgpt_builder", "build_chatgpt_research_bundle.py"
).build
build_bundle = _load_agent_module(
    "_test_complete_builder", "build_complete_session_bundle.py"
).build_bundle
gpt_audit_builder = _load_agent_module(
    "_test_gpt_audit_builder", "build_gpt_audit_bundle.py"
)


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _set_dashboard_roots(report_root: Path, data_root: Path) -> None:
    dashboard.ROOT = report_root
    dashboard.DATA_ROOT = data_root
    dashboard.HISTORY_ROOT = report_root
    dashboard._API_RESPONSE_CACHE.clear()


def test_source_bearing_downloads_resolve_service_root_not_report_root() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        derived_report_root = Path(tmp) / "canonical-research-data" / "analyzer"
        derived_report_root.mkdir(parents=True)
        original_root = dashboard.ROOT
        try:
            dashboard.ROOT = derived_report_root
            source_root = dashboard._agent_source_root()
            assert source_root == ROOT.resolve()
            assert (source_root / "bot.py").is_file()
            assert (source_root / "analyzer_research_engine_v62.py").is_file()
            assert (source_root / "research" / "research_dashboard.py").is_file()
        finally:
            dashboard.ROOT = original_root


def test_genome_download_uses_service_source_when_reports_are_canonical() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        derived_report_root = Path(tmp) / "canonical-research-data" / "analyzer"
        derived_report_root.mkdir(parents=True)
        original_root = dashboard.ROOT
        try:
            dashboard.ROOT = derived_report_root
            with dashboard.app.test_client() as client:
                response = client.get("/download/genome")
            assert response.status_code == 200, response.get_data(as_text=True)
            with zipfile.ZipFile(io.BytesIO(response.data)) as archive:
                assert archive.testzip() is None
                assert any(name.startswith("genome/") for name in archive.namelist())
        finally:
            dashboard.ROOT = original_root


def test_scope_aware_report_selection() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        reports = root / "reports"
        _write_json(
            root / "research_session.json",
            {"fresh_collection_mode": True},
        )
        _write_json(
            root / "lane_retirement_report.json",
            {"schema": "lane_retirement_v2", "session_scope": "ALL-TIME"},
        )
        _write_json(
            reports / "lane_retirement_report.json",
            {
                "schema": "lane_retirement_v2",
                "session_scope": "FRESH-COLLECTION",
            },
        )
        _write_json(
            root / "report_manifest.json",
            {
                "reports": [{"file": "lane_retirement_report.json"}],
                "generated_at": "2026-07-30T23:00:00+00:00",
            },
        )
        _set_dashboard_roots(root, root)

        selected = dashboard._best_report_path("lane_retirement_report.json")
        assert selected == reports / "lane_retirement_report.json"
        members = dict(dashboard._bundle_members())
        assert members["reports/lane_retirement_report.json"] == selected
        assert sum(path.name == "lane_retirement_report.json" for path in members.values()) == 1

        with dashboard.app.test_client() as client:
            response = client.get("/api/report/lane_retirement_report.json")
            assert response.status_code == 200
            assert response.get_json()["session_scope"] == "FRESH-COLLECTION"


def test_download_everything_includes_compressed_chase_reports_from_manifest() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        reports = root / "reports"
        names = (
            "chase_policy_lab_report.json",
            "missed_opportunity_proof_report.json",
        )
        manifest_rows = []
        for name in names:
            path = reports / name
            _write_json(path, {"schema": name.removesuffix(".json") + "_v1"})
            manifest_rows.append({"file": name, "size_bytes": path.stat().st_size})
        _write_json(
            root / "report_manifest.json",
            {
                "generated_at": "2026-08-27T13:00:00+00:00",
                "analyzer_sync_id": "compressed-chase-test",
                "reports": manifest_rows,
            },
        )
        _set_dashboard_roots(root, root)

        members = dict(dashboard._bundle_members())
        for name in names:
            assert members[f"reports/{name}"] == reports / name


def test_bundle_members_include_declared_gzip_and_checksum_sidecar() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        reports = root / "reports"
        reports.mkdir(parents=True)
        gzip_name = "safe_policy_genome_v3_exhaustive.jsonl.gz"
        sidecar_name = "safe_policy_genome_v3_exhaustive_manifest.json"
        gzip_path = reports / gzip_name
        gzip_bytes = b"\x1f\x8bdeclared-exhaustive-policy-fixture"
        gzip_path.write_bytes(gzip_bytes)
        checksum = hashlib.sha256(gzip_bytes).hexdigest()
        _write_json(
            reports / sidecar_name,
            {
                "artifact": gzip_name,
                "row_count": 1,
                "sha256": checksum,
                "size_bytes": len(gzip_bytes),
            },
        )
        _write_json(
            root / "report_manifest.json",
            {
                "generated_at": "2026-08-29T11:49:41+00:00",
                "reports": [
                    {"file": gzip_name, "size_bytes": len(gzip_bytes)},
                    {"file": sidecar_name},
                ],
            },
        )
        _set_dashboard_roots(root, root)

        members = dict(dashboard._bundle_members())

        assert members[f"reports/{gzip_name}"] == gzip_path
        assert members[f"reports/{sidecar_name}"] == reports / sidecar_name
        assert hashlib.sha256(
            members[f"reports/{gzip_name}"].read_bytes()
        ).hexdigest() == checksum


def test_complete_bundle_split_roots_and_freshness() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "agent"
        mirror = root / "fly-data-mirror"
        archive = root / "research_session_archives" / "session_001"
        downloads = root / "research" / "downloads"
        root.mkdir(parents=True)
        mirror.mkdir()
        downloads.mkdir(parents=True)
        _write_json(
            root / "report_manifest.json",
            {
                "generated_at": "2026-07-30T23:00:00+00:00",
                "analyzer_sync_id": "v-test",
                "reports": [],
            },
        )
        _write_json(
            archive / "session_meta.json",
            {"session_id": "session_001", "generated_at": "2026-07-30T22:00:00+00:00"},
        )
        _write_csv(
            root / "trades_3factor.csv",
            [{"trade_id": "history", "research_lane": "CONTINUOUS", "net_pnl_usd": 1}],
        )
        _write_csv(
            mirror / "trades_3factor.csv",
            [{"trade_id": "fly-current", "research_lane": "CONTINUOUS", "net_pnl_usd": 2}],
        )
        _write_csv(
            mirror / "decisions_3factor.csv",
            [{"trade_id": "fly-current", "decision": "APPROVE"}],
        )
        _write_csv(
            mirror / "blocked_signals_3factor.csv",
            [{"trade_id": "blocked-current", "reason": "gate"}],
        )

        out = downloads / "trading_sessions_complete.zip"
        built = build_bundle(root, root, out, data_root=mirror)
        with zipfile.ZipFile(built) as zf:
            assert zf.testzip() is None
            manifest = json.loads(zf.read("BUNDLE_MANIFEST.json"))
            assert manifest["schema"] == "trading_sessions_complete_manifest_v2"
            assert zf.read("csv/trades_3factor.csv") == (
                mirror / "trades_3factor.csv"
            ).read_bytes()
            assert "csv/blocked_signals_3factor.csv" in zf.namelist()

        _set_dashboard_roots(root, mirror)
        assert dashboard._complete_bundle_is_current(built)
        with (mirror / "trades_3factor.csv").open("a", encoding="utf-8") as handle:
            handle.write("\n")
        assert not dashboard._complete_bundle_is_current(built)


def test_chatgpt_bundle_split_roots_and_record_count() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "agent"
        mirror = root / "fly-data-mirror"
        root.mkdir(parents=True)
        mirror.mkdir()
        (root / "research").mkdir()
        _write_json(
            root / "report_manifest.json",
            {
                "generated_at": "2026-07-30T23:00:00+00:00",
                "analyzer_sync_id": "v-test",
                "reports": [],
            },
        )
        _write_json(
            root / "research_compact_summary.json",
            {"session_scope": "FRESH-COLLECTION"},
        )
        _write_csv(
            root / "trades_3factor.csv",
            [{"trade_id": "history", "research_lane": "CONTINUOUS", "net_pnl_usd": 1}],
        )
        _write_csv(
            mirror / "trades_3factor.csv",
            [
                {
                    "trade_id": "fly-1",
                    "research_lane": "CONTINUOUS",
                    "net_pnl_usd": 2,
                    "reason": "line one\nline two",
                }
            ],
        )
        _write_csv(
            mirror / "decisions_3factor.csv",
            [{"trade_id": "fly-1", "decision": "APPROVE"}],
        )
        accumulator = root / "research_accumulator" / "trades_accumulated.csv"
        _write_csv(
            accumulator,
            [
                {"trade_id": "a", "reason": "one\ncontinued"},
                {"trade_id": "b", "reason": "two"},
            ],
        )

        out, manifest = build_chatgpt(
            agent_root=root,
            data_root=mirror,
            report_root=root,
        )
        assert manifest["trade_stats"]["total_trades"] == 1
        assert manifest["accumulator_trades"] == 2
        assert manifest["source_contract"]["raw_sources"][0]["sha256"] == _sha(
            mirror / "trades_3factor.csv"
        )
        with zipfile.ZipFile(out) as zf:
            assert zf.testzip() is None
            assert zf.read("csv/trades_3factor.csv") == (
                mirror / "trades_3factor.csv"
            ).read_bytes()

        _set_dashboard_roots(root, mirror)
        assert dashboard._chatgpt_bundle_is_current(out)
        with (mirror / "trades_3factor.csv").open("a", encoding="utf-8") as handle:
            handle.write("\n")
        assert not dashboard._chatgpt_bundle_is_current(out)


def test_gpt_audit_source_hash_validation() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        source = root / "sample.py"
        source.write_text("print('current')\n", encoding="utf-8")
        bot_source = root / "bot.py"
        bot_source.write_text("print('bot')\n", encoding="utf-8")
        analyzer_source = root / "analyzer_research_engine_v62.py"
        analyzer_source.write_text("print('analyzer')\n", encoding="utf-8")
        dashboard_source = root / "research" / "research_dashboard.py"
        dashboard_source.parent.mkdir(parents=True)
        dashboard_source.write_text("print('dashboard')\n", encoding="utf-8")
        record = {
            "path": "source/sample.py",
            "bytes": source.stat().st_size,
            "sha256_prefix": _sha(source)[:16],
        }
        manifest = root / "GPT_AUDIT_MANIFEST.json"
        _write_json(
            manifest,
            {"generated_at": "2026-07-30T23:00:00+00:00", "file_index": [record]},
        )
        bundle = root / "audit.zip"
        with zipfile.ZipFile(bundle, "w") as zf:
            zf.writestr("README.txt", "audit")
            zf.write(source, "source/sample.py")
            zf.write(bot_source, "source/bot.py")
            zf.write(analyzer_source, "source/analyzer_research_engine_v62.py")
            zf.write(dashboard_source, "source/research/research_dashboard.py")
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        for path, member in (
            (bot_source, "source/bot.py"),
            (analyzer_source, "source/analyzer_research_engine_v62.py"),
            (dashboard_source, "source/research/research_dashboard.py"),
        ):
            payload["file_index"].append({
                "path": member,
                "bytes": path.stat().st_size,
                "sha256_prefix": _sha(path)[:16],
            })
        _write_json(manifest, payload)
        assert dashboard._gpt_audit_bundle_is_current(bundle, manifest, root)
        source.write_text("print('changed')\n", encoding="utf-8")
        assert not dashboard._gpt_audit_bundle_is_current(bundle, manifest, root)


def test_gpt_audit_builder_uses_real_analyzer_and_fails_closed() -> None:
    saved_roots = (
        gpt_audit_builder.AGENT_ROOT,
        gpt_audit_builder.RESEARCH_ROOT,
        gpt_audit_builder.OUT_DIR,
    )
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "agent"
        for rel in gpt_audit_builder.REQUIRED_SOURCE_FILES:
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"# {rel}\n", encoding="utf-8")
        try:
            bundle, manifest = gpt_audit_builder.build(agent_root=root)
            with zipfile.ZipFile(bundle) as zf:
                names = set(zf.namelist())
                assert zf.testzip() is None
                assert "source/analyzer_research_engine_v62.py" in names
                assert "source/research/analyzer_research_engine_v62.py" not in names
                assert set(manifest["required_members"]).issubset(names)
                assert set(manifest["start_here"]).issubset(names)
                assert "data/genome_analysis_report.json" not in manifest["start_here"]
                archived_manifest = json.loads(zf.read("GPT_AUDIT_MANIFEST.json"))
                readme = zf.read("README.txt").decode("utf-8")
                assert archived_manifest["bundle_type"] == "ARCHITECTURE_SOURCE_AUDIT"
                assert archived_manifest["complete_research_evidence"] is False
                assert "NOT the complete research-evidence download" in readme
                assert "Download Everything" in readme

            genome_report = root / "research" / "genome" / "genome_analysis_report.json"
            genome_report.parent.mkdir(parents=True, exist_ok=True)
            genome_report.write_text(
                json.dumps({"schema": "trading_genome_analysis_v1"}),
                encoding="utf-8",
            )
            bundle, manifest = gpt_audit_builder.build(agent_root=root)
            with zipfile.ZipFile(bundle) as zf:
                names = set(zf.namelist())
                assert "data/genome_analysis_report.json" in names
                assert "data/genome_analysis_report.json" in manifest["start_here"]
                assert set(manifest["start_here"]).issubset(names)

            missing = root / "research_v3_contract.py"
            missing.unlink()
            try:
                gpt_audit_builder.build(agent_root=root)
            except FileNotFoundError as exc:
                assert "research_v3_contract.py" in str(exc)
            else:
                raise AssertionError("missing required audit source did not fail closed")
        finally:
            (
                gpt_audit_builder.AGENT_ROOT,
                gpt_audit_builder.RESEARCH_ROOT,
                gpt_audit_builder.OUT_DIR,
            ) = saved_roots


def test_everything_includes_current_mirror_without_cache_files() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "agent"
        mirror = root / "fly-data-mirror"
        root.mkdir(parents=True)
        mirror.mkdir()
        (root / "bot.py").write_text("# fixture source\n", encoding="utf-8")
        (root / "analyzer_research_engine_v62.py").write_text(
            "# fixture source\n", encoding="utf-8"
        )
        (root / "research" / "genome" / "__pycache__").mkdir(parents=True)
        _write_json(
            root / "research" / "genome" / "genome_analysis_report.json",
            {"schema": "trading_genome_analysis_v1"},
        )
        _write_csv(mirror / "trades_3factor.csv", [{"trade_id": "fly-current"}])
        for name in dashboard.REQUIRED_ANALYZER_RAW_INPUTS:
            path = mirror / name
            if path.suffix == ".db":
                connection = sqlite3.connect(path)
                connection.execute(
                    "CREATE TABLE evidence (id INTEGER PRIMARY KEY, value TEXT)"
                )
                connection.execute(
                    "INSERT INTO evidence(value) VALUES ('required-input')"
                )
                connection.commit()
                connection.close()
            elif path.suffix == ".csv":
                _write_csv(path, [{"event_id": "required-input"}])
            else:
                path.write_text('{"event_id":"required-input"}\n', encoding="utf-8")
        _write_json(mirror / "relay_lifecycle_evidence_v1.json", {
            "schema": "relay_lifecycle_evidence_v1", "records": []
        })
        for name in (
            "research_session.json",
            "pathway_lane_specs.json",
            "research_events_v22.index.json",
            "paper_lifecycle_v1.json",
            "lane_lab_pnl_ledger.json",
            ".fly-sync-state.json",
            ".fly-sync-growth-state.json",
            "_size_report.json",
            "config-7002.json",
            "paper_lifecycle_emergency_test.json",
        ):
            _write_json(mirror / name, {"name": name})
        for name in dashboard.CURRENT_PATHWAY_RECEIPTS:
            if name != "exit_reports_validation.json":
                _write_json(mirror / name, {"schema": name, "verdict": "CURRENT"})
        _write_json(
            mirror / "exit_reports_validation.json",
            {"schema": "stale_mirror_exit_validation", "status": "STALE"},
        )
        for name in (
            "decision.jsonl",
            "execution.jsonl",
            "lifecycle.jsonl",
            "market_segment.jsonl",
            "opportunity.jsonl",
            "order_intent.jsonl",
        ):
            path = mirror / "v3" / "ledgers" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{}\n", encoding="utf-8")
        _write_json(
            mirror / "v3" / "market_segments" / "aa" / "segment.json",
            {"segment": "aa"},
        )
        (mirror / "signal_replay.jsonl.1").write_text("{}\n", encoding="utf-8")
        (mirror / "market_microstructure_1s.jsonl.2").write_text(
            "{}\n", encoding="utf-8"
        )
        (mirror / "signal_persist.log").write_text("stage=kept\n", encoding="utf-8")
        (mirror / "counterfactual.jsonl").write_text("{}\n", encoding="utf-8")
        (mirror / "chase_offset_touch_grid.jsonl").write_text(
            json.dumps({
                "schema": "chase_offset_touch_grid_v1",
                "event": "ARMED",
            }) + "\n",
            encoding="utf-8",
        )
        _write_csv(root / "trades_3factor.csv", [{"trade_id": "history"}])
        _write_json(root / "historical_trade_cohort_report.json", {"cohorts": {}})
        for db_path in (
            root / "research.db",
            root / "research_accumulator" / "research_trades.db",
        ):
            db_path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(db_path)
            connection.execute("CREATE TABLE evidence (id INTEGER PRIMARY KEY, value TEXT)")
            connection.execute("INSERT INTO evidence(value) VALUES ('complete')")
            connection.commit()
            connection.close()
        _write_json(
            root / "report_manifest.json",
            {
                "generated_at": "now",
                "generation_revision": "test-revision",
                "source_data_revision": "test-data-revision",
                "reports": [
                    {"file": "historical_trade_cohort_report.json"},
                    {"file": "exit_reports_validation.json"},
                ],
            },
        )
        _write_json(
            root / "exit_reports_validation.json",
            {
                "schema": "exit_reports_validation_v2",
                "generation_revision": "test-revision",
                "epoch_id": "epoch-test",
                "status": "EMPTY",
            },
        )
        _write_json(
            root / "safe_policy_genome_v3_report.json",
            {
                "epoch_id": "epoch-test",
                "generation_revision": "test-revision",
                "collection": {
                    "effective_paper_execution_identities": [
                        {"policy_signature": "policy-tile-4"},
                        {"policy_signature": "policy-tile-3"},
                        {"policy_signature": "policy-tile-4"},
                    ]
                },
            },
        )
        for name in dashboard.BUNDLE_FILES:
            path = root / name
            if path.is_file():
                continue
            if path.suffix == ".json":
                _write_json(path, {"generated_at": "now"})
            else:
                path.write_text("current analyzer artifact\n", encoding="utf-8")
        (root / "research" / "genome" / "__pycache__" / "bad.pyc").write_bytes(b"x")
        audit = root / "audit.zip"
        with zipfile.ZipFile(audit, "w") as zf:
            zf.writestr("README.txt", "audit")

        _set_dashboard_roots(root, mirror)
        original_ensure = dashboard._ensure_current_gpt_audit_bundle
        original_freshness = dashboard._generation_freshness_meta
        original_agent_root = dashboard._AGENT_ROOT
        dashboard._AGENT_ROOT = root
        dashboard._ensure_current_gpt_audit_bundle = lambda _root: audit
        dashboard._generation_freshness_meta = lambda: {
            "current": True,
            "reasons": [],
        }
        try:
            # Atomic mirror synchronization can create and rename these while
            # a user requests the bundle. They are implementation details, not
            # stable evidence members, and must never make the download fail.
            (mirror / "execution_funnel.jsonl.13980.deadbeef.download").write_text(
                "transient\n", encoding="utf-8"
            )
            with dashboard.app.test_client() as client:
                response = client.get("/download/everything")
                assert response.status_code == 200, response.data.decode(
                    "utf-8", errors="replace"
                )
                assert "complete_research_evidence_bundle_" in response.headers[
                    "Content-Disposition"
                ]
                with zipfile.ZipFile(io.BytesIO(response.data)) as zf:
                    assert zf.testzip() is None
                    names = zf.namelist()
                    assert "raw/current_fly_mirror/trades_3factor.csv" in names
                    assert "raw/current_fly_mirror/relay_lifecycle_evidence_v1.json" in names
                    assert "raw/current_fly_mirror/counterfactual.jsonl" in names
                    assert "raw/current_fly_mirror/research_session.json" in names
                    assert "raw/current_fly_mirror/pathway_lane_specs.json" in names
                    assert "raw/current_fly_mirror/research_events_v22.index.json" in names
                    assert "raw/current_fly_mirror/paper_lifecycle_v1.json" in names
                    assert "raw/current_fly_mirror/.fly-sync-state.json" in names
                    assert "raw/current_fly_mirror/signal_replay.jsonl.1" in names
                    assert (
                        "raw/current_fly_mirror/market_microstructure_1s.jsonl.2"
                        in names
                    )
                    assert "raw/current_fly_mirror/signal_persist.log" in names
                    assert not any(".download" in name for name in names)
                    assert "genome/research.db" in names
                    assert "accumulator/research_trades.db" in names
                    for receipt_name in dashboard.CURRENT_PATHWAY_RECEIPTS:
                        assert f"current_receipts/{receipt_name}" in names
                    exit_receipt = json.loads(
                        zf.read("current_receipts/exit_reports_validation.json")
                    )
                    assert exit_receipt["schema"] == "exit_reports_validation_v2"
                    assert (
                        "raw/current_fly_mirror/v3/ledgers/decision.jsonl"
                        in names
                    )
                    assert (
                        "raw/current_fly_mirror/v3/ledgers/execution.jsonl"
                        in names
                    )
                    assert (
                        "raw/current_fly_mirror/v3/market_segments/aa/segment.json"
                        in names
                    )
                    assert "raw/research_history/trades_3factor.csv" in names
                    assert not any("__pycache__" in name or name.endswith(".pyc") for name in names)
                    manifest = json.loads(zf.read("MANIFEST.json"))
                    assert manifest["schema"] == "doxxed_everything_bundle_v2"
                    assert manifest["generation_revision"] == "test-revision"
                    assert manifest["source_data_revision"] == "test-data-revision"
                    assert manifest["epoch_id"] == "epoch-test"
                    assert manifest["policy_signatures"] == [
                        "policy-tile-3",
                        "policy-tile-4",
                    ]
                    assert manifest["notes"]["source_revision"] == "test-revision"
                    coverage = manifest["notes"]["component_coverage"]
                    assert coverage["relay_lifecycle_evidence_v1"] is True
                    assert coverage["counterfactual_evidence"] is True
                    assert coverage["cohort_reports"] is True
                    assert coverage["genome_and_dna"] is True
                    assert coverage["report_manifest"] is True
                    assert coverage["canonical_v3_ledgers"] is True
                    assert coverage["canonical_v3_market_segments"] is True
                    assert coverage["replay_rotations"] is True
                    assert coverage["session_spec_index_receipts"] is True
                    assert coverage["paper_lifecycle_receipt"] is True
                    assert coverage["mirror_sync_receipt"] is True
                    assert coverage["gpt_audit_source_bundle"] is True
                    assert coverage["current_pathway_receipts_complete"] is True
                    assert all(coverage["current_pathway_receipts"].values())
                    assert manifest["notes"]["missing_current_receipts"] == []
                    assert set(manifest["notes"]["required_current_receipts"]) == {
                        f"current_receipts/{name}"
                        for name in dashboard.CURRENT_PATHWAY_RECEIPTS
                    }
                    indexed = {item["path"]: item for item in manifest["files"]}
                    assert "README.txt" in indexed
                    assert indexed["README.txt"]["capture_mode"] == (
                        "generated_bundle_member_v1"
                    )
                    assert set(names) - {"MANIFEST.json"} == set(indexed)
                    compressed_status = manifest["notes"][
                        "conditional_analyzer_raw_status"
                    ]["chase_offset_touch_grid.jsonl"]
                    assert compressed_status == {
                        "present": False,
                        "absence_status": "NO_COMPRESSED_SHADOW_SCHEDULE_EVENTS",
                        "valid_compressed_shadow_rows": 0,
                    }
                    assert manifest["notes"]["component_coverage"][
                        "signed_compressed_shadow_schedule"
                    ] == compressed_status
                    assert manifest["capture_contract"]["schema"] == (
                        "generation_fenced_bundle_capture_v2"
                    )
                    assert manifest["capture_contract"]["coherence_verified"] is True
                    assert manifest["notes"]["missing_current_reports"] == []
                    assert manifest["notes"]["accumulator_db_members"] == [
                        "accumulator/research_trades.db"
                    ]
                    assert manifest["notes"]["optional_analyzer_raw_presence"][
                        "signal_persist.log"
                    ] is True
                    assert manifest["notes"]["optional_analyzer_raw_presence"][
                        "near_edge.log"
                    ] is False
                    assert indexed["genome/research.db"]["capture_mode"] == (
                        "sqlite_online_backup_v1"
                    )
                    assert indexed["accumulator/research_trades.db"]["integrity_check"] == "ok"
                    assert indexed[
                        "raw/current_fly_mirror/research_events_v22.jsonl"
                    ]["capture_mode"] == "append_prefix_generation_fence_v1"
                    for name, record in indexed.items():
                        payload = zf.read(name)
                        assert record["bytes"] == len(payload)
                        assert record["sha256"] == hashlib.sha256(payload).hexdigest()
                        if name != "README.txt":
                            assert record["staging_mode"] == "bounded_memory_disk_v1"
                # Raw evidence remains downloadable even when the analyzer is
                # stale. The manifest must label the stale report snapshot
                # rather than pretending that it is current.
                dashboard._generation_freshness_meta = lambda: {
                    "current": False,
                    "reasons": ["newer raw mirror rows await analyzer publication"],
                }
                _write_json(
                    mirror / ".fly-sync-state.json",
                    {"source_git_rev": "newer-raw-revision", "epoch_id": "newer-raw-epoch"},
                )
                _write_json(
                    mirror / "research_session.json",
                    {"source_git_rev": "newer-raw-revision", "epoch_id": "newer-raw-epoch"},
                )
                stale_generation = client.get("/download/everything")
                assert stale_generation.status_code == 200
                with zipfile.ZipFile(io.BytesIO(stale_generation.data)) as zf:
                    stale_manifest = json.loads(zf.read("MANIFEST.json"))
                    assert stale_manifest["generation_current"] is False
                    assert stale_manifest["generation_freshness"]["reasons"] == [
                        "newer raw mirror rows await analyzer publication"
                    ]
                    assert stale_manifest["notes"]["current_report_scope"] == (
                        "STALE_ANALYZER_SNAPSHOT_WITH_CURRENT_RAW_EVIDENCE"
                    )
                    assert stale_manifest["provenance_coherent"] is False
                    assert {row["field"] for row in stale_manifest["provenance_conflicts"]} == {
                        "revision",
                        "epoch_id",
                    }
                    assert "raw/current_fly_mirror/trades_3factor.csv" in zf.namelist()
                _write_json(mirror / ".fly-sync-state.json", {"name": ".fly-sync-state.json"})
                _write_json(mirror / "research_session.json", {"name": "research_session.json"})
                dashboard._generation_freshness_meta = lambda: {
                    "current": True,
                    "reasons": [],
                }
                (mirror / "chase_offset_touch_grid.jsonl").write_text(
                    json.dumps({
                        "schema": "compressed_chase_shadow_v1",
                        "event": "STAGE",
                        "execution_class": "SHADOW_ONLY",
                        "places_order": False,
                        "relay_eligible": False,
                        "trade_id": "shadow-1",
                        "shared_ai_call_id": "ai-1",
                        "opportunity_id": "opp-1",
                        "episode_id": "episode-1",
                        "epoch_id": "epoch-test",
                        "policy_id": "compressed-policy",
                        "policy_signature": "policy-signature",
                    }) + "\n",
                    encoding="utf-8",
                )
                populated_shadow = client.get("/download/everything")
                assert populated_shadow.status_code == 200
                with zipfile.ZipFile(io.BytesIO(populated_shadow.data)) as zf:
                    populated_manifest = json.loads(zf.read("MANIFEST.json"))
                    populated_status = populated_manifest["notes"][
                        "conditional_analyzer_raw_status"
                    ]["chase_offset_touch_grid.jsonl"]
                    assert populated_status == {
                        "present": True,
                        "absence_status": None,
                        "valid_compressed_shadow_rows": 1,
                    }
                    assert populated_manifest["notes"]["component_coverage"][
                        "signed_compressed_shadow_schedule"
                    ] == populated_status
                (mirror / "v3" / "ledgers" / "execution.jsonl").unlink()
                empty_execution = client.get("/download/everything")
                assert empty_execution.status_code == 200
                with zipfile.ZipFile(io.BytesIO(empty_execution.data)) as zf:
                    empty_manifest = json.loads(zf.read("MANIFEST.json"))
                    assert empty_manifest["notes"]["conditional_v3_ledger_status"][
                        "execution.jsonl"
                    ] == {
                        "present": False,
                        "absence_status": "NO_EXECUTION_EVENTS",
                    }
                (mirror / "v3" / "ledgers" / "execution.jsonl").write_text(
                    "{}\n", encoding="utf-8"
                )
                (mirror / "ai_scan_role_validation.json").unlink()
                refused_receipt = client.get("/download/everything")
                assert refused_receipt.status_code == 500
                assert b"current_receipts/ai_scan_role_validation.json" in refused_receipt.data
                (mirror / "ai_scan_role_validation.json").write_text(
                    "{}", encoding="utf-8"
                )
                accumulator_db = root / "research_accumulator" / "research_trades.db"
                accumulator_bytes = accumulator_db.read_bytes()
                accumulator_db.unlink()
                refused_accumulator = client.get("/download/everything")
                assert refused_accumulator.status_code == 500
                assert b"accumulator SQLite database" in refused_accumulator.data
                accumulator_db.write_bytes(accumulator_bytes)
                missing_report = root / dashboard.EXECUTIVE_SUMMARY_FILE
                report_text = missing_report.read_text(encoding="utf-8")
                missing_report.unlink()
                refused_report = client.get("/download/everything")
                assert refused_report.status_code == 500
                assert dashboard.EXECUTIVE_SUMMARY_FILE.encode() in refused_report.data
                missing_report.write_text(report_text, encoding="utf-8")
                _write_json(
                    mirror / "research_session.json",
                    {"collector_v22_epoch_id": "epoch-conflict"},
                )
                refused_identity = client.get("/download/everything")
                assert refused_identity.status_code == 500
                assert b"incoherent epoch_id provenance" in refused_identity.data
                _write_json(mirror / "research_session.json", {"name": "research_session.json"})
                (mirror / dashboard.REQUIRED_ANALYZER_RAW_INPUTS[0]).unlink()
                refused_input = client.get("/download/everything")
                assert refused_input.status_code == 500
                assert dashboard.REQUIRED_ANALYZER_RAW_INPUTS[0].encode() in refused_input.data
        finally:
            dashboard._AGENT_ROOT = original_agent_root
            dashboard._ensure_current_gpt_audit_bundle = original_ensure
            dashboard._generation_freshness_meta = original_freshness


def test_disk_staging_cleans_partial_output_when_source_is_missing() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        missing = root / "vanished-during-sync.jsonl"
        staged = root / "member.capture"
        try:
            dashboard._stage_generation_fenced(missing, staged, attempts=2)
        except RuntimeError as exc:
            assert "could not capture stable generation" in str(exc)
        else:
            raise AssertionError("missing source unexpectedly produced a staged member")
        assert not staged.exists()


def test_sqlite_online_snapshot_includes_uncheckpointed_wal_commit() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "live.db"
        writer = sqlite3.connect(db)
        try:
            writer.execute("PRAGMA journal_mode=WAL")
            writer.execute("CREATE TABLE evidence (value TEXT)")
            writer.execute("INSERT INTO evidence(value) VALUES ('visible-in-backup')")
            writer.commit()
            payload, provenance = dashboard._sqlite_online_snapshot(db)
            snapshot = Path(tmp) / "snapshot.db"
            snapshot.write_bytes(payload)
            reader = sqlite3.connect(snapshot)
            try:
                values = [row[0] for row in reader.execute("SELECT value FROM evidence")]
            finally:
                reader.close()
            assert values == ["visible-in-backup"]
            assert provenance["capture_mode"] == "sqlite_online_backup_v1"
            assert provenance["integrity_check"] == "ok"
        finally:
            writer.close()


def test_single_loopback_dashboard_contract() -> None:
    launcher = (ROOT.parents[1] / "scripts" / "start-home-analyzer.ps1").read_text(
        encoding="utf-8"
    )
    restart = (ROOT.parents[1] / "scripts" / "analyzer-auto-restart.ps1").read_text(
        encoding="utf-8"
    )
    analyzer = (ROOT / "analyzer_research_engine_v62.py").read_text(
        encoding="utf-8"
    )
    assert 'RESEARCH_DASHBOARD_BIND_HOST = "127.0.0.1"' in launcher
    assert 'ANALYZER_EMBEDDED_DASHBOARD = "0"' in launcher
    assert "disabled fail-closed" in restart
    assert "Start-Process" not in restart
    assert "Get-AnalyzerListenerPids" in launcher
    assert "Get-CanonicalAnalyzerEnginePids" in launcher
    assert "multiple analyzer engines already exist" in launcher
    assert '"--owner-port=$AnalyzerPort"' in launcher
    assert launcher.index("Start-Process -FilePath \"python\"") < launcher.index(
        "Keep the exclusive start claim until the child PID is durably published."
    )
    assert "$listenerPids.Count -eq 1" in launcher
    dashboard_source = (RESEARCH / "research_dashboard.py").read_text(encoding="utf-8")
    assert 'RESEARCH_DASHBOARD_BIND_HOST", "127.0.0.1"' in dashboard_source


def main() -> None:
    tests = (
        test_scope_aware_report_selection,
        test_complete_bundle_split_roots_and_freshness,
        test_chatgpt_bundle_split_roots_and_record_count,
        test_gpt_audit_source_hash_validation,
        test_gpt_audit_builder_uses_real_analyzer_and_fails_closed,
        test_everything_includes_current_mirror_without_cache_files,
        test_single_loopback_dashboard_contract,
    )
    for test in tests:
        test()
        print(f"[PASS] {test.__name__}")
    print(f"PASS: {len(tests)} dashboard download freshness checks")


if __name__ == "__main__":
    main()
