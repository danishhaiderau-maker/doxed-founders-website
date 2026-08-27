import json

import analyzer_research_engine_v62 as analyzer


REVISION = "b" * 40
EPOCH = "epoch-current"


def _manifest():
    names = (
        analyzer.EXIT_COMBINATIONS_REPORT_FILE,
        analyzer.EXIT_LEAKAGE_BY_REASON_REPORT_FILE,
    )
    return {
        "generation_id": "generation-current",
        "generation_revision": REVISION,
        "source_data_revision": "source-current",
        "fresh_epoch": {"epoch_id": EPOCH},
        "analysis_provenance": {
            "generation_revision": REVISION,
            "fresh_epoch_id": EPOCH,
        },
        "reports": [{"file": name} for name in names],
    }


def _write_reports(tmp_path, *, source_rows=0, terminal_rows=0, result_rows=0):
    provenance = {
        "generation_revision": REVISION,
        "fresh_epoch_id": EPOCH,
    }
    (tmp_path / analyzer.EXIT_COMBINATIONS_REPORT_FILE).write_text(
        json.dumps({
            "analysis_provenance": provenance,
            "evidence_worlds": {
                "paper": {"source_rows": source_rows, "terminal_rows": terminal_rows}
            },
            "total_combos": result_rows,
        }),
        encoding="utf-8",
    )
    (tmp_path / analyzer.EXIT_LEAKAGE_BY_REASON_REPORT_FILE).write_text(
        json.dumps({
            "analysis_provenance": provenance,
            "evidence_worlds": {
                "paper": {"source_rows": source_rows, "terminal_rows": terminal_rows}
            },
            "reasons": [{} for _ in range(result_rows)],
        }),
        encoding="utf-8",
    )


def test_current_parseable_zero_evidence_is_truthfully_empty(tmp_path):
    _write_reports(tmp_path)

    receipt = analyzer.build_exit_reports_validation(_manifest(), tmp_path)

    assert receipt["status"] == "EMPTY"
    assert receipt["current_generation_valid"] is True
    assert receipt["identity_match"] is True
    assert receipt["minimum_trade_gate_applied"] is False


def test_source_without_terminal_exit_evidence_is_insufficient(tmp_path):
    _write_reports(tmp_path, source_rows=3)

    receipt = analyzer.build_exit_reports_validation(_manifest(), tmp_path)

    assert receipt["status"] == "INSUFFICIENT"
    assert receipt["current_generation_valid"] is True


def test_current_terminal_or_result_evidence_is_populated(tmp_path):
    _write_reports(tmp_path, source_rows=3, terminal_rows=1, result_rows=1)

    receipt = analyzer.build_exit_reports_validation(_manifest(), tmp_path)

    assert receipt["status"] == "POPULATED"
    assert receipt["qualification_eligible"] is False


def test_mismatched_epoch_fails_current_generation_validation(tmp_path):
    _write_reports(tmp_path)
    path = tmp_path / analyzer.EXIT_COMBINATIONS_REPORT_FILE
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["analysis_provenance"]["fresh_epoch_id"] = "epoch-old"
    path.write_text(json.dumps(payload), encoding="utf-8")

    receipt = analyzer.build_exit_reports_validation(_manifest(), tmp_path)

    assert receipt["status"] == "INSUFFICIENT"
    assert receipt["current_generation_valid"] is False
    assert receipt["identity_match"] is False

