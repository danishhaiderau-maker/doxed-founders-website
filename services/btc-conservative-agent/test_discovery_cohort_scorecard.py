from discovery_cohort_scorecard import build_episode_matched_scorecard


def row(world, episode="e1", pnl=1.0, **extra):
    value = {"evidence_world": world, "episode_id": episode, "adx_bucket": "STRONG", "offset_pct": .3,
             "chase_policy": "w234", "exit_family": "ATR_TRAIL", "policy_id": "p1", "policy_signature": "ps",
             "epoch_id": "epoch-1", "opportunity_id": "opp-1", "source_revision": "source-rev",
             "deployed_revision": "deployed-rev", "direction": "LONG",
             "schedule_sha256": "ss", "schedule_id": "schedule-id", "original_requested_qty": .1,
             "tape_sha256": "tape-hash", "tape_id": "tape-id",
             "tile_config_signature": "tiles", "config_signature": "cfg", "cost_model_id": "cost",
             "simulation_model": "CONSERVATIVE_BBO_DEPTH_TAPE", "net_pnl_usd": pnl}
    value.update(extra); return value


def comparison(report, left, right):
    return next(x for x in report["matched_comparisons"] if x["left_world"] == left and x["right_world"] == right)


def test_worlds_never_pool_pnl_and_aliases_normalize():
    report = build_episode_matched_scorecard([row("OBSERVED_PAPER", pnl=1), row("IDEAL_TOUCH_DIAGNOSTIC_ONLY", pnl=2), row("CONSERVATIVE_BBO_DEPTH_TAPE", pnl=.5)])
    assert report["pnl_sum_across_worlds"] is False
    assert report["discovery_shadow_equals_paper"] is False
    assert [report["worlds"][w]["cells"][0]["mean_net_pnl_usd"] for w in ("OBSERVED_PAPER", "IDEAL_TOUCH", "CONSERVATIVE_BBO")] == [1, 2, .5]
    assert report["worlds"]["CONSERVATIVE_BBO"]["world_alias_provenance"] == {"CONSERVATIVE_BBO_DEPTH_TAPE": 1}
    assert report["upstream_input_wiring_complete"] is False


def test_exact_episode_and_identity_match_allows_only_matched_delta():
    report = build_episode_matched_scorecard([row("IDEAL_TOUCH", pnl=1), row("CONSERVATIVE_BBO", pnl=.75)])
    found = comparison(report, "IDEAL_TOUCH", "CONSERVATIVE_BBO")
    assert found["cohort_equality_proven"] is True
    assert found["exact_identity_match_count"] == 1
    assert found["mean_right_minus_left_pnl_usd"] == -.25


def test_schedule_and_tape_ids_need_not_equal_their_hashes():
    report = build_episode_matched_scorecard([
        row("IDEAL_TOUCH", schedule_id="human-schedule", schedule_sha256="a" * 64,
            tape_id="segment-name", tape_sha256="b" * 64),
        row("CONSERVATIVE_BBO", schedule_id="human-schedule", schedule_sha256="a" * 64,
            tape_id="segment-name", tape_sha256="b" * 64),
    ])
    assert comparison(report, "IDEAL_TOUCH", "CONSERVATIVE_BBO")["cohort_equality_proven"] is True


def test_runtime_model_vs_conservative_is_calibration_only():
    report = build_episode_matched_scorecard([row("OBSERVED_PAPER", simulation_model="AUTHENTICATED_ACTUAL"), row("CONSERVATIVE_BBO")])
    found = comparison(report, "OBSERVED_PAPER", "CONSERVATIVE_BBO")
    assert found["cohort_equality_proven"] is False
    assert found["comparison_status"] == "CALIBRATION_ONLY_MODEL_DIFFERENCE"
    assert found["matched_pnl_delta_count"] == 0


def test_episode_deltas_and_identity_mismatch_block_equality():
    report = build_episode_matched_scorecard([row("IDEAL_TOUCH", "shared"), row("CONSERVATIVE_BBO", "shared", tape_sha256="other"), row("IDEAL_TOUCH", "left"), row("CONSERVATIVE_BBO", "right")])
    found = comparison(report, "IDEAL_TOUCH", "CONSERVATIVE_BBO")
    assert found["left_only_episode_ids"] == ["left"]
    assert found["right_only_episode_ids"] == ["right"]
    assert found["cohort_equality_proven"] is False
    assert "IDENTITY_MISMATCH:shared:tape_hashes" in found["blockers"]


def test_duplicate_variants_count_once_and_block_match():
    duplicate = row("IDEAL_TOUCH")
    report = build_episode_matched_scorecard([duplicate, dict(duplicate), row("CONSERVATIVE_BBO")])
    world = report["worlds"]["IDEAL_TOUCH"]
    assert world["independent_episode_count"] == 1
    assert world["cells"][0]["duplicate_episode_ids"] == ["e1"]
    assert comparison(report, "IDEAL_TOUCH", "CONSERVATIVE_BBO")["exact_identity_match_count"] == 0


def test_different_policies_in_same_axes_are_independent_groups_not_duplicates():
    report = build_episode_matched_scorecard([
        row("CONSERVATIVE_BBO", policy_id="p1", policy_signature="s1"),
        row("CONSERVATIVE_BBO", policy_id="p2", policy_signature="s2"),
    ])
    world = report["worlds"]["CONSERVATIVE_BBO"]
    assert world["independent_episode_count"] == 1
    assert len(world["cells"]) == 2
    assert all(cell["duplicate_episode_ids"] == [] for cell in world["cells"])


def test_missing_nonfinite_pnl_is_not_zero():
    report = build_episode_matched_scorecard([row("CONSERVATIVE_BBO", "missing", None), row("CONSERVATIVE_BBO", "nan", float("nan"))])
    cell = report["worlds"]["CONSERVATIVE_BBO"]["cells"][0]
    assert cell["independent_episode_count"] == 2
    assert cell["pnl_observed_episode_count"] == 0
    assert cell["missing_or_nonfinite_pnl_count"] == 2
    assert cell["net_pnl_usd_sum"] is None
    assert cell["mean_net_pnl_usd"] is None


def test_matched_identity_with_missing_pnl_has_no_invented_delta():
    report = build_episode_matched_scorecard([row("IDEAL_TOUCH", pnl=None), row("CONSERVATIVE_BBO", pnl=None)])
    found = comparison(report, "IDEAL_TOUCH", "CONSERVATIVE_BBO")
    assert found["cohort_equality_proven"] is True
    assert found["matched_pnl_delta_count"] == 0
    assert found["mean_right_minus_left_pnl_usd"] is None


def test_conflicting_alias_and_missing_identity_block_equality():
    report = build_episode_matched_scorecard([row("IDEAL_TOUCH", requested_qty=.2), row("CONSERVATIVE_BBO")])
    found = comparison(report, "IDEAL_TOUCH", "CONSERVATIVE_BBO")
    assert found["cohort_equality_proven"] is False
    assert "INCOMPLETE_IDENTITY:e1" in found["blockers"]
    assert report["worlds"]["IDEAL_TOUCH"]["cells"][0]["identity_blocker_counts"] == {
        "CONFLICTING_IDENTITY_ALIASES:quantity": 1
    }


def test_repeated_tape_alias_value_is_deduplicated_without_losing_provenance():
    report = build_episode_matched_scorecard([
        row("IDEAL_TOUCH", tape_hashes=["tape-hash"]),
        row("CONSERVATIVE_BBO"),
    ])
    found = comparison(report, "IDEAL_TOUCH", "CONSERVATIVE_BBO")
    assert found["cohort_equality_proven"] is True


def test_negative_quantity_blocks_identity_equality():
    report = build_episode_matched_scorecard([
        row("IDEAL_TOUCH", original_requested_qty=-.1), row("CONSERVATIVE_BBO")
    ])
    assert comparison(report, "IDEAL_TOUCH", "CONSERVATIVE_BBO")["cohort_equality_proven"] is False


def test_generation_opportunity_revisions_and_direction_are_identity_dimensions():
    for field, changed in (("epoch_id", "epoch-2"), ("opportunity_id", "opp-2"),
                           ("source_revision", "other-source"),
                           ("deployed_revision", "other-deployed"), ("direction", "SHORT")):
        report = build_episode_matched_scorecard([
            row("IDEAL_TOUCH"), row("CONSERVATIVE_BBO", **{field: changed})
        ])
        found = comparison(report, "IDEAL_TOUCH", "CONSERVATIVE_BBO")
        assert found["cohort_equality_proven"] is False
        assert any(item.startswith("IDENTITY_MISMATCH:e1:") for item in found["blockers"])


def test_missing_invalid_or_conflicting_generation_identity_blocks_equality():
    for extra in ({"epoch_id": "UNKNOWN"}, {"epoch_id": None},
                  {"dataset_epoch": "epoch-2"}):
        report = build_episode_matched_scorecard([
            row("IDEAL_TOUCH", **extra), row("CONSERVATIVE_BBO")
        ])
        found = comparison(report, "IDEAL_TOUCH", "CONSERVATIVE_BBO")
        assert found["cohort_equality_proven"] is False
        assert "INCOMPLETE_IDENTITY:e1" in found["blockers"]


def test_malformed_axis_fails_closed_without_type_error():
    report = build_episode_matched_scorecard([
        row("CONSERVATIVE_BBO", adx_bucket={"not": "hashable"})
    ])
    assert report["status"] == "UNKNOWN"
    assert report["worlds"]["CONSERVATIVE_BBO"]["independent_episode_count"] == 0
    assert "INCOMPLETE_OR_INVALID_CELL:e1" in report["blockers"]
