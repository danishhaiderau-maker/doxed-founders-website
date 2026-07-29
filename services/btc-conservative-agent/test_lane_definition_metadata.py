"""Focused contract for active Type B and retired Tile 2 audit metadata."""

import contextlib
import io
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import analyzer_research_engine_v62 as analyzer


def main() -> int:
    specs = analyzer._static_pathway_lane_specs()
    type_b = specs["TYPE_B_HUNTER_V1"]
    tile_2 = specs["SR_MICRO_TILE_V2_STATIC"]

    type_b_conditions = " ".join(analyzer._lane_entry_conditions(type_b)).lower()
    assert "shared" in type_b_conditions
    assert "score gap" in type_b_conditions or "score-gap" in type_b_conditions
    assert "adx" in type_b_conditions
    assert analyzer._lane_depends_on_ai("TYPE_B_HUNTER_V1", type_b) is True
    assert analyzer._lane_depends_on_chase("TYPE_B_HUNTER_V1", type_b) is True
    assert analyzer._lane_depends_on_edge("TYPE_B_HUNTER_V1", type_b) is False

    tile_2_conditions = " ".join(analyzer._lane_entry_conditions(tile_2)).lower()
    tile_2_execution = str(tile_2["entry"]["execution"]).lower()
    assert "one long" in tile_2_conditions
    assert "one short" in tile_2_conditions
    assert "30m" in tile_2_execution
    assert "no chase" in tile_2_execution
    # The retired definition remains decodable for historical reports, but it
    # is deliberately outside the active analyzer dependency classifier.
    assert tile_2["entry"]["policy_version"]

    with tempfile.TemporaryDirectory() as tmpdir:
        old_report = analyzer.LANE_DEFINITION_REPORT_FILE
        analyzer.LANE_DEFINITION_REPORT_FILE = os.path.join(tmpdir, "lane_definition.json")
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                report = analyzer.lane_definition_report(
                    session={"session_id": "lane-metadata-test"},
                    benchmark_report={"lanes": {}},
                )
        finally:
            analyzer.LANE_DEFINITION_REPORT_FILE = old_report

    rows = {row["lane"]: row for row in report["lanes"]}
    assert "TYPE_B_HUNTER_V1" in rows
    assert rows["TYPE_B_HUNTER_V1"]["role"]
    assert rows["TYPE_B_HUNTER_V1"]["research_question"]
    assert rows["TYPE_B_HUNTER_V1"]["entry_conditions"]
    assert rows["TYPE_B_HUNTER_V1"]["depends_on_ai"] is True
    assert "SR_MICRO_TILE_V2_STATIC" not in rows
    assert "SR_MICRO_TILE_V2_STATIC" in report["retired_lanes"]

    print("lane definition metadata tests: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
