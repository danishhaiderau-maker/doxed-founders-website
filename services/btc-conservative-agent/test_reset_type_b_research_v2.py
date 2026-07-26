from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import reset_type_b_research_v2 as reset_module
from reset_type_b_research_v2 import CONFIRMATION, reset


def run() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "bot.py").write_text("# marker\n", encoding="utf-8")
        (root / "research_opportunity_v2.py").write_text("# marker\n", encoding="utf-8")
        (root / "research").mkdir()
        (root / "research" / "research_dashboard.py").write_text("# source\n", encoding="utf-8")
        (root / "research" / "old_report.json").write_text("{}", encoding="utf-8")
        (root / "research" / "research_session_archives").mkdir()
        (root / "research" / "research_session_archives" / "old.json").write_text("{}")
        (root / "trades_3factor.csv").write_text("old\n", encoding="utf-8")
        (root / "shadow_outcome.jsonl").write_text("{}\n", encoding="utf-8")
        (root / "type_b_research_v2.jsonl.1").write_text('{"legacy":true}\n', encoding="utf-8")
        for rotated_name in (
            "signal_snapshot.jsonl.1",
            "signal_replay.jsonl.15",
            "post_exit_replay.jsonl.3",
            "trades_3factor.csv.2",
            "replay.db-wal",
            "replay.db-shm",
        ):
            (root / rotated_name).write_text("old\n", encoding="utf-8")
        (root / "research" / "counterfactual.jsonl.4").write_text("old\n", encoding="utf-8")
        (root / "lane_pnl_ledger.json").write_text('{"old":true}\n', encoding="utf-8")
        (root / "lane_lab_pnl_ledger.json").write_text('{"old":true}\n', encoding="utf-8")
        for stale_name in (
            ".research_retention_last_run.json",
            "report_manifest.json",
            "research_session_index.json",
            "tile2_counters.json",
            "bot_analyzer_sync.json",
            "repo_version_sync.json",
        ):
            (root / stale_name).write_text('{"old":true}\n', encoding="utf-8")
        (root / "open_positions.json").write_text('{"positions":[]}\n', encoding="utf-8")
        (root / "bitfinex_live_state.json").write_text('{"armed":false}\n', encoding="utf-8")
        (root / "config-7002.json").write_text('{"manual_admin_pause":true}\n', encoding="utf-8")
        (root / "relay_ledger.json").write_text('{"keep":true}\n', encoding="utf-8")
        (root / ".home-stack-user-stopped").write_text("1\n", encoding="utf-8")

        dry = reset(
            root,
            execute=False,
            confirmation="",
            writers_stopped=False,
        )
        assert dry["targets"]
        assert (root / "trades_3factor.csv").exists()

        (root / "open_positions.json").write_text(
            '{"positions":[{"trade_id":"carryover","status":"OPEN"}]}\n',
            encoding="utf-8",
        )
        with patch.object(reset_module, "_port_open", return_value=False):
            try:
                reset(
                    root,
                    execute=True,
                    confirmation=CONFIRMATION,
                    writers_stopped=True,
                    repo_root=root,
                )
                raise AssertionError("non-flat reset did not fail closed")
            except RuntimeError as exc:
                assert "carryover position" in str(exc)
        (root / "open_positions.json").write_text('{"positions":[]}\n', encoding="utf-8")
        with patch.object(reset_module, "_port_open", return_value=False):
            result = reset(
                root,
                execute=True,
                confirmation=CONFIRMATION,
                writers_stopped=True,
                repo_root=root,
            )
        assert result["initialized"] is True
        assert not (root / "trades_3factor.csv").exists()
        assert not (root / "shadow_outcome.jsonl").exists()
        assert not (root / "type_b_research_v2.jsonl.1").exists()
        assert not (root / "signal_snapshot.jsonl.1").exists()
        assert not (root / "signal_replay.jsonl.15").exists()
        assert not (root / "post_exit_replay.jsonl.3").exists()
        assert not (root / "trades_3factor.csv.2").exists()
        assert not (root / "replay.db-wal").exists()
        assert not (root / "replay.db-shm").exists()
        assert not (root / "research" / "counterfactual.jsonl.4").exists()
        assert not (root / "repo_version_sync.json").exists()
        assert not (root / "lane_pnl_ledger.json").exists()
        assert not (root / "lane_lab_pnl_ledger.json").exists()
        assert not (root / "report_manifest.json").exists()
        assert not (root / "research_session_index.json").exists()
        assert not (root / "tile2_counters.json").exists()
        assert not (root / "bot_analyzer_sync.json").exists()
        assert not (root / "research" / "old_report.json").exists()
        assert (root / "research" / "research_dashboard.py").exists()
        assert (root / "open_positions.json").exists()
        assert (root / "bitfinex_live_state.json").exists()
        assert (root / "config-7002.json").exists()
        assert (root / "relay_ledger.json").exists()
        session = json.loads((root / "research_session.json").read_text(encoding="utf-8"))
        assert session["collection_id"] == "TYPE_B_RESEARCH_V2"
        assert session["legacy_data_retained"] is False
        assert (root / "type_b_research_v2.jsonl").read_text(encoding="utf-8") == ""
    print("PASS: Type-B V2 reset deletes research data and preserves execution state")


if __name__ == "__main__":
    run()
