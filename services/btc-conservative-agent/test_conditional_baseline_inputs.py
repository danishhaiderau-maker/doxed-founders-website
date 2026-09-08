import pytest
from research_v3_contract import canonical_hash
from research.runtime_baseline_declaration import build_runtime_baseline_declaration
from research.baseline_execution_context import (
    conditional_directional_baseline_inputs, declared_directional_baseline_inputs)
from test_runtime_conditional_declaration import inputs


def captured(direction="LONG"):
    declaration = build_runtime_baseline_declaration(**inputs())["declaration"]
    row = {"direction": direction, "symbol": "tBTCUSD", "source_revision": "rev",
           "signal_ts": 120, "episode_id": "episode", "research_context_declaration": declaration,
           "schedules": {"base": {"policy_signature": "policy", "episode_id": "episode",
                                  "schedule": [{"limit_price": 100}]}}}
    return seal(row)


def seal(row):
    row["capture_signature"] = canonical_hash("directional-entry-capture",
        {k: v for k, v in row.items() if k != "capture_signature"}, length=64)
    return row


BASELINE = {"baseline_id": "base", "policy_signature": "policy"}


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_conditional_inputs_preserve_uncertainty_and_sizing(direction):
    row = captured(direction)
    result = conditional_directional_baseline_inputs(row, BASELINE)
    assert result["requested_qty"] == .25
    assert result["venue_acceptance"] == "UNKNOWN"
    assert result["qualification_eligible"] is False
    assert "signed_quantity_constraints" not in result
    with pytest.raises(ValueError):
        declared_directional_baseline_inputs(row, BASELINE)


@pytest.mark.parametrize("defect", ["authority", "late", "identity", "tamper"])
def test_conditional_input_gates(defect):
    row = captured()
    if defect == "authority": row["research_context_declaration"]["qualification_eligible"] = True
    if defect == "late": row["signal_ts"] = 99
    if defect == "identity": row["source_revision"] = "other"
    if defect != "tamper": seal(row)
    else: row["signal_ts"] = 121
    with pytest.raises(ValueError):
        conditional_directional_baseline_inputs(row, BASELINE)
