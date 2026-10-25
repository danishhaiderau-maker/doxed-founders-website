"""HM B1 (#882): the three baseline-schema fail-closed paths each block on a wrong schema."""
from research import conservative_shadow_report as csr
from research import discovery_scorecard_publication as dsp
from research.shadow_model_input import first_signal_ts_from_baseline

GEN = {field: f"v-{field}" for field in csr.GENERATION_FIELDS}
SAME_OPP = "entry_baseline_same_opportunity_replay_v1"


def _shadow(tmp_path, schema):
    out = csr._build_shadow_report(
        tmp_path, expected_generation=GEN, baseline_report={"schema": schema, "generation": GEN},
        policy_candidates=[], policy_artifact_receipt={})
    return out


def _scorecard(tmp_path, schema):
    return dsp._build_discovery_scorecard_publication(
        tmp_path, expected_generation=GEN,
        evaluator_status={"schema": "v3_conservative_policy_evidence_v1", "generation": GEN},
        baseline_report={"schema": schema, "generation": GEN})


def test_shadow_report_blocks_on_wrong_baseline_schema(tmp_path):
    bad = _shadow(tmp_path, "entry_baseline_book_replay_v1")
    assert bad["status"] == "UNKNOWN"
    assert "BASELINE_REPORT_SCHEMA_INVALID" in bad["blockers"]
    assert "BASELINE_REPORT_SCHEMA_INVALID" not in _shadow(tmp_path, SAME_OPP)["blockers"]


def test_scorecard_blocks_on_wrong_baseline_schema(tmp_path):
    bad = _scorecard(tmp_path, "entry_baseline_book_replay_v1")
    assert bad["status"] == "UNKNOWN"
    assert bad["blockers"] == ["BASELINE_REPORT_SCHEMA_INVALID"]
    assert "BASELINE_REPORT_SCHEMA_INVALID" not in _scorecard(tmp_path, SAME_OPP).get("blockers", [])


def test_first_signal_ts_requires_book_replay_schema():
    rows = {"results": [{"signal_ts": 1792263671.0}], "non_book_rows": [{"signal_ts": 1792263700.0}]}
    assert first_signal_ts_from_baseline({"schema": "entry_baseline_book_replay_v1", **rows}) == 1792263671.0
    assert first_signal_ts_from_baseline({"schema": SAME_OPP, **rows}) is None
    assert first_signal_ts_from_baseline(rows) is None
