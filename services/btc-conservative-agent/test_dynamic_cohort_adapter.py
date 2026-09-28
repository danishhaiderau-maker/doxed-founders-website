"""Same-publication cohort contract; no mutable Fly input or synthetic fills."""
from copy import deepcopy

import pytest

from research.dynamic_cohort_adapter import GENERATION_FIELDS, adapt_dynamic_cohorts


GENERATION = {key: "generation-" + key for key in GENERATION_FIELDS}


def row(**changes):
    value = {
        "generation": dict(GENERATION), "episode_id": "episode-one", "opportunity_id": "opportunity-one",
        "policy_id": "ENTRY_PLUS_EXIT_A", "policy_signature": "a" * 64,
        "evidence_world": "CONSERVATIVE_BBO", "cost_model_id": "declared-fees-v1",
        "simulation_model": "depth-terminal-v1", "economics_evidence_basis": "DECLARED_SIMULATION",
        "declared_contract_sha256": "c" * 64, "declared_position_margin_usd": 25.0,
        "original_requested_qty": 0.01, "market": "BITFINEX", "symbol": "BTCUSD", "direction": "LONG",
        "signal_ts": 1000.0, "required_end_ts": 9000.0,
        "pre_entry_features": {"regime": {"value": "BULL", "observed_ts": 999.0}},
        "bucket_definition_signature": "fixed-ex-ante-buckets-v1",
        "outcome_state": "FULL_FILL", "terminal_complete": True, "net_pnl_usd": 2.5,
    }
    value.update(changes)
    return value


def adapt(rows, **changes):
    return adapt_dynamic_cohorts(rows, expected_generation=GENERATION,
                                feature_names=("regime",), protocol={"purge_sec": 7200}, **changes)


def test_pivots_candidates_and_uses_longest_required_horizon():
    result = adapt([row(), row(policy_id="B", policy_signature="b" * 64, required_end_ts=10000.0)])
    group = result["groups"][0]
    assert len(group["candidates"]) == 2
    assert len(group["episodes"]) == 1
    assert group["episodes"][0]["required_end_ts"] == 10000.0
    assert len(group["episodes"][0]["policy_outcomes"]) == 2
    assert result["counts"]["supported_outcomes"] == 2


def test_declared_usd_contract_does_not_fragment_different_opportunity_quantities():
    result = adapt([row(), row(episode_id="episode-two", opportunity_id="opportunity-two", original_requested_qty=.02)])
    assert len(result["groups"]) == 1
    assert len(result["groups"][0]["episodes"]) == 2


def test_same_episode_quantity_conflict_rejects_entire_episode():
    result = adapt([row(), row(policy_id="B", original_requested_qty=.02)])
    assert result["groups"][0]["episodes"] == []
    assert result["rejections"]["INCOMPARABLE_EPISODE_CANDIDATES"] == 1


def test_exact_quantity_fallback_is_separate_and_flagged():
    result = adapt([row(declared_contract_sha256=None, declared_position_margin_usd=None),
                    row(declared_contract_sha256=None, declared_position_margin_usd=None, original_requested_qty=.02)])
    assert len(result["groups"]) == 2
    assert result["counts"]["exact_quantity_fallback_rows"] == 2
    assert result["sizing_fallback_warning"]


@pytest.mark.parametrize("field,value,reason", [
    ("signal_ts", None, "SIGNAL_OR_HORIZON_MISSING_NONFINITE"),
    ("signal_ts", float("nan"), "SIGNAL_OR_HORIZON_MISSING_NONFINITE"),
    ("required_end_ts", float("inf"), "SIGNAL_OR_HORIZON_MISSING_NONFINITE"),
    ("required_end_ts", 999, "HORIZON_BEFORE_SIGNAL"),
    ("cost_model_id", None, "ECONOMICS_IDENTITY_INCOMPLETE"),
    ("declared_position_margin_usd", None, "DECLARED_SIZING_CONTRACT_INCOMPLETE"),
    ("generation", {}, "GENERATION_MISMATCH"),
    ("source_revision", "wrong", "GENERATION_MISMATCH"),
    ("pre_entry_features", {"regime": {"value": "BULL", "observed_ts": 1001}}, "POST_SIGNAL_FEATURE"),
    ("pre_entry_features", {"regime": {"value": "BULL"}}, "CAUSAL_FEATURE_VALUE_OR_TIME_INVALID"),
])
def test_missing_or_noncausal_fields_fail_closed(field, value, reason):
    result = adapt([row(**{field: value})])
    assert result["groups"] == []
    assert result["rejections"][reason] == 1
    assert "INPUT_ROWS_REJECTED_CANDIDATE_UNIVERSE_INCOMPLETE" in result["blockers"]
    assert result["comparison_complete"] is False


def test_unknown_stays_visible_but_is_not_zero_outcome():
    result = adapt([row(outcome_state="UNKNOWN", net_pnl_usd=12345)])
    assert result["counts"]["unknown_outcome_rows"] == 1
    assert result["groups"][0]["episodes"][0]["policy_outcomes"] == {}


@pytest.mark.parametrize("changes", [{"terminal_complete": "true"}, {"net_pnl_usd": float("nan")},
                                     {"net_pnl_usd": None}, {"terminal_complete": False}])
def test_incomplete_terminal_is_not_an_economic_outcome(changes):
    result = adapt([row(**changes)])
    assert result["groups"][0]["episodes"][0]["policy_outcomes"] == {}
    assert result["rejections"]["TERMINAL_ECONOMICS_INCOMPLETE"] == 1


def test_zero_only_from_explicit_complete_no_fill():
    good = adapt([row(outcome_state="NO_FILL", net_pnl_usd=0)])
    assert good["groups"][0]["episodes"][0]["policy_outcomes"]["ENTRY_PLUS_EXIT_A"]["net_pnl_usd"] == 0
    bad = adapt([row(outcome_state="NO_FILL", net_pnl_usd=1)])
    assert bad["rejections"]["ZERO_OUTCOME_NONZERO_PNL"] == 1


def test_worlds_and_economics_do_not_mix():
    result = adapt([row(), row(evidence_world="OBSERVED_PAPER"), row(cost_model_id="other")])
    assert len(result["groups"]) == 3


def test_taxonomies_never_mix_even_with_same_economics():
    result = adapt([row(), row(bucket_definition_signature="different-taxonomy")])
    assert len(result["groups"]) == 2


def test_conflicting_duplicate_permutations_have_identical_receipts():
    from itertools import permutations
    hashes = {adapt(order)["adapter_sha256"] for order in permutations([row(), row(), row(net_pnl_usd=5)])}
    assert len(hashes) == 1


def test_duplicate_conflict_does_not_select_last_winner_and_hash_is_order_independent():
    rows = [row(), row(net_pnl_usd=5)]
    first, second = adapt(rows), adapt(reversed(rows))
    assert first["groups"][0]["episodes"][0]["policy_outcomes"] == {}
    assert first["adapter_sha256"] == second["adapter_sha256"]


def test_conflicting_signature_disqualifies_candidate_across_episodes():
    result = adapt([row(), row(policy_signature="other", episode_id="episode-two", opportunity_id="opportunity-two")])
    assert result["groups"][0]["candidates"] == []
    assert all(not e["policy_outcomes"] for e in result["groups"][0]["episodes"])


def test_identical_duplicates_deduplicate_without_mutating_input():
    source = row()
    original = deepcopy(source)
    result = adapt([source, source])
    assert source == original
    assert result["counts"]["duplicate_rows"] == 1
    assert result["counts"]["supported_outcomes"] == 1


@pytest.mark.parametrize("limit,rows", [
    ("max_rows", [row(), row()]),
    ("max_episodes", [row(), row(episode_id="two")]),
    ("max_groups", [row(), row(cost_model_id="other")]),
    ("max_candidates", [row(), row(policy_id="B")]),
])
def test_limits_fail_explicitly_not_truncate(limit, rows):
    with pytest.raises(ValueError, match="LIMIT"):
        adapt(rows, **{limit: 1})
