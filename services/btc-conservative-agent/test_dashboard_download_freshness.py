"""Focused regressions for honest, current analyzer dashboard downloads."""

from __future__ import annotations

import csv
import hashlib
import importlib.util
import io
import json
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
        bot_record = {
            "path": "source/bot.py",
            "bytes": bot_source.stat().st_size,
            "sha256_prefix": _sha(bot_source)[:16],
        }
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        payload["file_index"].append(bot_record)
        _write_json(manifest, payload)
        assert dashboard._gpt_audit_bundle_is_current(bundle, manifest, root)
        source.write_text("print('changed')\n", encoding="utf-8")
        assert not dashboard._gpt_audit_bundle_is_current(bundle, manifest, root)


def test_everything_includes_current_mirror_without_cache_files() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "agent"
        mirror = root / "fly-data-mirror"
        root.mkdir(parents=True)
        mirror.mkdir()
        (root / "research" / "genome" / "__pycache__").mkdir(parents=True)
        _write_csv(mirror / "trades_3factor.csv", [{"trade_id": "fly-current"}])
        _write_csv(root / "trades_3factor.csv", [{"trade_id": "history"}])
        _write_json(
            root / "report_manifest.json",
            {"generated_at": "now", "reports": []},
        )
        (root / "research" / "genome" / "__pycache__" / "bad.pyc").write_bytes(b"x")
        audit = root / "audit.zip"
        with zipfile.ZipFile(audit, "w") as zf:
            zf.writestr("README.txt", "audit")

        _set_dashboard_roots(root, mirror)
        original_ensure = dashboard._ensure_current_gpt_audit_bundle
        dashboard._ensure_current_gpt_audit_bundle = lambda _root: audit
        try:
            with dashboard.app.test_client() as client:
                response = client.get("/download/everything")
                assert response.status_code == 200
                with zipfile.ZipFile(io.BytesIO(response.data)) as zf:
                    assert zf.testzip() is None
                    names = zf.namelist()
                    assert "raw/current_fly_mirror/trades_3factor.csv" in names
                    assert "raw/research_history/trades_3factor.csv" in names
                    assert not any("__pycache__" in name or name.endswith(".pyc") for name in names)
                    manifest = json.loads(zf.read("MANIFEST.json"))
                    assert manifest["schema"] == "doxxed_everything_bundle_v2"
        finally:
            dashboard._ensure_current_gpt_audit_bundle = original_ensure


def test_single_loopback_dashboard_contract() -> None:
    launcher = (ROOT.parents[1] / "scripts" / "start-home-analyzer.ps1").read_text(
        encoding="utf-8"
    )
    restart = (ROOT.parents[1] / "scripts" / "analyzer-auto-restart.ps1").read_text(
        encoding="utf-8"
    )
    analyzer = (RESEARCH / "analyzer_research_engine_v62.py").read_text(
        encoding="utf-8"
    )
    for source in (launcher, restart):
        assert 'RESEARCH_DASHBOARD_BIND_HOST = "127.0.0.1"' in source
        assert 'ANALYZER_EMBEDDED_DASHBOARD = "0"' in source
    assert "Get-AnalyzerListenerPids" in launcher
    assert "$listenerPids.Count -eq 1" in launcher
    assert 'os.getenv("ANALYZER_EMBEDDED_DASHBOARD", "1")' in analyzer
    dashboard_source = (RESEARCH / "research_dashboard.py").read_text(encoding="utf-8")
    assert 'RESEARCH_DASHBOARD_BIND_HOST", "127.0.0.1"' in dashboard_source


def main() -> None:
    tests = (
        test_scope_aware_report_selection,
        test_complete_bundle_split_roots_and_freshness,
        test_chatgpt_bundle_split_roots_and_record_count,
        test_gpt_audit_source_hash_validation,
        test_everything_includes_current_mirror_without_cache_files,
        test_single_loopback_dashboard_contract,
    )
    for test in tests:
        test()
        print(f"[PASS] {test.__name__}")
    print(f"PASS: {len(tests)} dashboard download freshness checks")


if __name__ == "__main__":
    main()
