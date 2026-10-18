"""Pre-registered minimums as code (Addendum 1 + 2). Tests: PASS, each single-gate FAIL, PENDING, sealing, validity, static states."""
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from self_aware import min_samples as ms

T0 = 1_800_000_000.0


def holdout(n=300, hours_step=0.5, mean=3.0, ctl_gap=2.0, frozen=T0, fdr=True, policies=5832, seed=7, looks=1, ctl=True, noise=2.0):
    rng = random.Random(seed)
    trades = []
    for i in range(n):
        entry = frozen + 600 + i * hours_step * 3600
        net = mean + rng.uniform(-noise, noise)
        trades.append({"trade_id": f"t{i}", "entry_id": f"e{i}", "status": "CLOSED", "hypothesis": "combos", "entry_ts": entry,
                       "net_bp": net, "control_net_bp": (net - ctl_gap + rng.uniform(-1, 1)) if ctl else None,
                       "pricing": {"fresh_eligible": True, "fresh_epoch_id": "fse-1", "exit_status": "PAPER_FILL", "fill_model": "REALISTIC_V1"},
                       "exit": {"close_ts": entry + 60, "deploy_flatten": False}})
    return {"policy_id": "P", "frozen_at": frozen, "sealed_at": frozen + 1, "looks": looks, "trades": trades,
            "training": {"bh_fdr_q010_passed": fdr, "policies_tested": policies}}


def test_shas_are_full_length_and_exposed():
    assert all(len(v) == 64 for v in ms.PREREG.values())
    assert ms.ADDENDUM2_SHA256.startswith("2a047c5e") and ms.ADDENDUM1_SHA256.startswith("a61ce07d")


def test_pass_needs_all_gates_and_is_the_only_edge_claim():
    r = ms.combos_holdout(holdout())
    assert r["state"] == "PASS" and all(r["gates"].values()) and r["edge_claim"] and r["ranking_allowed"]
    assert r["training_cut_is_evidence"] is False and r["holdout_reopen_allowed"] is False


def test_each_single_gate_fail_retires_the_policy():
    cases = {
        "g2_mean_ge_0p5bp": holdout(mean=0.2),
        "g3_ci_lower_gt0": holdout(mean=0.8, noise=40.0),
        "g4_control_diff_ci_lower_gt0": holdout(ctl_gap=0.0),
        "g5_training_bh_fdr_q010_all_5832": holdout(fdr=False),
    }
    for gate, h in cases.items():
        r = ms.combos_holdout(h)
        assert r["state"] == "FAIL" and r["gates"][gate] is False, (gate, r.get("gates"), r.get("mean_net_bp"))
        assert r["policy_retired"] is True and r["edge_claim"] is False and r["ranking_allowed"] is False
    r = ms.combos_holdout(holdout(policies=5000))
    assert r["state"] == "FAIL" and r["gates"]["g5_training_bh_fdr_q010_all_5832"] is False   # all 5,832 must be counted
    r = ms.combos_holdout(holdout(ctl=False))
    assert r["state"] == "FAIL" and r["gates"]["g4_control_diff_ci_lower_gt0"] is False


def test_pending_under_300_trades_or_under_5_days_never_claims_edge():
    for h in (holdout(n=299, hours_step=0.5), holdout(n=300, hours_step=0.05)):  # 299 trades; 300 trades inside <5 days
        r = ms.combos_holdout(h)
        assert r["state"] == "PENDING_SAMPLES" and r["edge_claim"] is False and r["ranking_allowed"] is False
    assert ms.combos_holdout(None)["state"] == "PENDING_SAMPLES"


def test_sealing_rules_row_before_freeze_and_reopen_are_refused():
    h = holdout()
    h["trades"][0]["entry_ts"] = h["frozen_at"] - 5
    r = ms.combos_holdout(h)
    assert r["state"] == "REFUSED_NOT_SEALED" and r["rows_before_freeze"] == 1 and r["edge_claim"] is False
    h2 = holdout(); h2.pop("frozen_at")
    assert ms.combos_holdout(h2)["state"] == "REFUSED_NOT_SEALED"
    assert ms.combos_holdout(holdout(looks=2))["state"] == "REFUSED_REOPENED"


def _row(i, **pr):
    base = {"trade_id": f"r{i}", "status": "CLOSED", "hypothesis": "H1", "entry_ts": T0 + i * 1000, "net_bp": 1.0,
            "pricing": {"fresh_eligible": True, "fresh_epoch_id": "fse-1", "exit_status": "PAPER_FILL", "fill_model": "REALISTIC_V1"},
            "exit": {"close_ts": T0 + i * 1000 + 100}}
    base["pricing"].update(pr)
    return base


def test_valid_trade_definition():
    assert ms.invalid_reason(_row(1)) is None
    assert ms.invalid_reason({**_row(1), "identity_quarantined": True}) == "IDENTITY_QUARANTINED"
    r = _row(1); r["exit"]["deploy_flatten"] = True
    assert ms.invalid_reason(r) == "DEPLOY_FLATTEN"
    assert ms.invalid_reason(_row(1, exit_status="FORCED_NO_BOOK")) == "FORCED_OR_UNPRICED_EXIT"
    assert ms.invalid_reason(_row(1, fill_model="BBO")) == "LEGACY_BBO_PRICING"
    assert ms.invalid_reason(_row(1, fresh_epoch_id="")) == "UNSTAMPED_FRESH_EPOCH_ID"
    assert ms.invalid_reason(_row(1, fresh_eligible=False)) == "NOT_FRESH_ELIGIBLE"
    r = _row(1); r["entry_ts"] = None
    assert ms.invalid_reason(r) == "UNSTAMPED_OR_STRADDLE_TS"
    assert ms.invalid_reason(_row(1, exit_status="REPRICED_CURRENT_TIME", exit_reprice_age_sec=4.0)) is None
    assert ms.invalid_reason(_row(1, exit_status="REPRICED_CURRENT_TIME", exit_reprice_age_sec=11.0)) == "REPRICED_BEYOND_10S_CAP"
    assert ms.invalid_reason(_row(1, exit_status="REPRICED_CURRENT_TIME")) == "REPRICED_BEYOND_10S_CAP"


def test_one_per_entry_non_overlapping_and_repriced_reported_separately():
    a, dup = _row(1), _row(1)
    overlap = _row(2); overlap["entry_ts"] = a["entry_ts"] + 50      # starts before a exits (a exits +100)
    rep = _row(3, exit_status="REPRICED_CURRENT_TIME", exit_reprice_age_sec=2.0)
    out = ms.valid_trades([a, dup, overlap, rep])
    assert out["n"] == 2 and out["repriced_current_time_n"] == 1
    assert out["rejected_by_reason"] == {"DUPLICATE_ENTRY": 1, "OVERLAPPING_HOLD": 1}


def test_study_minimums_days_early_flags_only_and_static_states():
    rows = [_row(i) for i in range(300)]          # 300 trades within ~83 h of exits: >=3 days
    r = ms.study_state("H1", rows)
    assert r["state"] == "COUNTABLE" and r["min_days"] == 3 and r["edge_claim"] is False
    assert ms.study_state("H3", [_row(i) for i in range(300)])["min_days"] == 5
    short = ms.study_state("H1", [_row(i) for i in range(299)])
    assert short["state"] == "PENDING_SAMPLES"
    bad = [{**_row(i), "net_bp": -2.0} for i in range(60)]
    r = ms.study_state("H1", bad)
    assert r["state"] == "PENDING_SAMPLES" and r["early_flags"][0]["flag"] == "KILL_RULE_MET"
    assert r["early_flags"][0]["auto_action"] is False and r["auto_tile_change"] is False
    mid = [{**_row(i), "net_bp": 0.1} for i in range(150)]
    assert ms.study_state("H1", mid)["early_flags"][0]["flag"] == "RETIRE_RULE_MET"
    assert ms.study_state("H7")["state"] == "UNTESTABLE" and ms.study_state("H5")["state"] == "FEASIBILITY_ONLY"
    for s in ("H7", "H5"):
        assert ms.study_state(s)["state"] not in ("RED", "PENDING_SAMPLES")
