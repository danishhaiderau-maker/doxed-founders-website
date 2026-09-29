from pathlib import Path

import analyzer_research_engine_v62 as analyzer


def test_legacy_relative_inputs_bind_to_canonical_store_without_rebinding_reports(
    tmp_path, monkeypatch
):
    (tmp_path / "ai_input_log.jsonl").write_text("{}\n", encoding="utf-8")
    (tmp_path / "execution_funnel.jsonl").write_text("{}\n", encoding="utf-8")
    (tmp_path / "execution_funnel_summary.json").write_text("{}", encoding="utf-8")
    (tmp_path / "research_session.json").write_text("{}", encoding="utf-8")
    (tmp_path / "direction_report.json").write_text("{}", encoding="utf-8")

    monkeypatch.setattr(analyzer, "AI_INPUT_LOG_FILE", "ai_input_log.jsonl")
    monkeypatch.setattr(analyzer, "EXECUTION_FUNNEL_FILE", "execution_funnel.jsonl")
    monkeypatch.setattr(
        analyzer, "EXECUTION_FUNNEL_SUMMARY_FILE", "execution_funnel_summary.json"
    )
    monkeypatch.setattr(analyzer, "RESEARCH_SESSION_FILE", "research_session.json")
    monkeypatch.setattr(analyzer, "DIRECTION_REPORT_FILE", "direction_report.json")

    rebound = analyzer._bind_existing_canonical_input_paths(str(tmp_path))

    assert Path(analyzer.AI_INPUT_LOG_FILE) == tmp_path / "ai_input_log.jsonl"
    assert Path(analyzer.EXECUTION_FUNNEL_FILE) == tmp_path / "execution_funnel.jsonl"
    assert Path(analyzer.EXECUTION_FUNNEL_SUMMARY_FILE) == tmp_path / "execution_funnel_summary.json"
    assert Path(analyzer.RESEARCH_SESSION_FILE) == tmp_path / "research_session.json"
    assert analyzer.DIRECTION_REPORT_FILE == "direction_report.json"
    assert "DIRECTION_REPORT_FILE" not in rebound


def test_canonical_input_tuple_members_are_rebound(tmp_path, monkeypatch):
    (tmp_path / "chase_offset_touch_grid.jsonl").write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(
        analyzer,
        "COMPRESSED_SHADOW_SCHEDULE_FILES",
        ("chase_offset_touch_grid.jsonl", "missing.jsonl"),
    )

    analyzer._bind_existing_canonical_input_paths(str(tmp_path))

    assert Path(analyzer.COMPRESSED_SHADOW_SCHEDULE_FILES[0]) == (
        tmp_path / "chase_offset_touch_grid.jsonl"
    )
    assert analyzer.COMPRESSED_SHADOW_SCHEDULE_FILES[1] == "missing.jsonl"
