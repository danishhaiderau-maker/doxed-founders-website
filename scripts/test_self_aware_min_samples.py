"""Pre-registered minimums as code (Addendum 1 + 2), tested on REAL /api/monitor/tiles/trades rows (fixtures/*.ndjson, saved
19 Oct 2026 from the live route) so the field names cannot drift again."""
import copy
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from self_aware import min_samples as ms

FIX = Path(__file__).resolve().parent / "fixtures"
OLD = [json.loads(x) for x in (FIX / "tiles_trades_oldest_page_20261019.ndjson").read_text().splitlines()]
NEW = [json.loads(x) for x in (FIX / "tiles_trades_newest_page_20261019.ndjson").read_text().splitlines()]
T0 = 1_800_000_000.0


def _with_731_fields(rows, basis="MODEL_TIME", lag=None):
    """The live route does not carry exit_pricing_basis / exit_repriced_lag_s until PR feat/tiles-repriced-fields deploys."""
    out = copy.deepcopy(rows)
    for r in out:
        if isinstance(r.get("pricing"), dict):
            r["pricing"].setdefault("exit_pricing_basis", basis)
            r["pricing"].setdefault("exit_repriced_lag_s", lag)
    return out


def _good_template():
    for r in _with_731_fields(NEW):
        if ms.invalid_reason(r) is None:
            return r
    raise AssertionError("fixture has no valid row")


# ---------------------------------------------------------------- fixture-based contract (field names cannot drift)
def test_fixture_rows_have_every_field_the_filter_reads_except_the_two_731_fields():
    closed = [r for r in OLD + NEW if r.get("status") == "CLOSED"]
    assert len(closed) >= 80
    for r in closed:
        for k in ms.REQUIRED_TOP:
            assert k in r, k
        for blk, req in (("exit", ms.REQUIRED_EXIT), ("fill", ms.REQUIRED_FILL)):
            for k in req:
                assert k in r[blk], (blk, k)
        for k in ms.REQUIRED_PRICING:
            if k not in ("exit_pricing_basis", "exit_repriced_lag_s"):
                assert k in r["pricing"], k
        assert isinstance(r["pnl"].get("bp"), float) and r["pnl"].get("bp_basis") == "net_pnl_bp"
    # every fixture lane resolves (live map + opaque retired archive labels); GS-06 is opaque-only
    assert all(ms.hypothesis_of(r) for r in OLD + NEW if r.get("lane"))
    assert "FAMILY_GS06_COMMITTED_FADE_ATR_TP" in ms.OPAQUE_RETIRED_HYPOTHESIS_BY_LANE
    assert "FAMILY_GS06_COMMITTED_FADE_ATR_TP" not in ms.LIVE_HYPOTHESIS_BY_LANE
    assert {"FAMILY_GS_FADE3_TP30", "FAMILY_GS_FADE3_LOOSE_TP25"} <= set(ms.LIVE_HYPOTHESIS_BY_LANE)


def test_until_731_fields_reach_the_route_every_live_row_is_invalid_for_a_missing_field():
    for r in OLD + NEW:
        if r["status"] == "CLOSED":
            assert ms.invalid_reason(r) == "MISSING_FIELD:pricing.exit_pricing_basis"
    out = ms.valid_trades(OLD + NEW)
    assert out["n"] == 0 and out["rejected_by_reason"]["MISSING_FIELD"] == 85


def test_real_rows_classified_with_731_fields_present():
    rows = _with_731_fields(OLD + NEW)
    reasons = {}
    for r in rows:
        w = ms.invalid_reason(r)
        reasons[w] = reasons.get(w, 0) + 1
    assert reasons.get(None, 0) >= 20                                  # PAPER_FILL book-priced rows
    unstamped = [r for r in rows if r["status"] == "CLOSED" and (r["entry_ts"] is None or r["exit_ts"] is None)]
    assert len(unstamped) == 22 and all(ms.invalid_reason(r) for r in unstamped)   # old pre-#708 rows: never valid
    assert reasons["UNSTAMPED_OR_STRADDLE_TS"] >= 10
    assert reasons.get("DEPLOY_FLATTEN", 0) >= 10 and reasons.get("NOT_CLOSED") == 2
    for r in rows:   # every valid row really is book-priced, stamped, fresh and not flattened/quarantined
        if ms.invalid_reason(r) is None:
            assert r["exit"]["deploy_flatten"] is False and r["identity_quarantined"] is False and r["fresh_epoch_id"]
            assert r["fill"]["fill_basis"] in ms.BOOK_FILL_BASES and r["pricing"]["exit_status"] == "PAPER_FILL"
    deployed = [r for r in rows if r["status"] == "CLOSED" and r["exit"].get("deploy_flatten") is True]
    assert deployed and all(ms.invalid_reason(r) for r in deployed)   # never countable, whatever check trips first
    v = ms.valid_trades(rows)
    assert v["n"] >= 1 and v["n"] + sum(x for k, x in v["rejected_by_reason"].items() if k != "_first_missing") == len(rows)


# ---------------------------------------------------------------- validity rules on a real row
def test_each_missing_field_makes_the_row_invalid():
    base = _good_template()
    for blk, req in (("", ms.REQUIRED_TOP), ("pricing", ms.REQUIRED_PRICING), ("exit", ms.REQUIRED_EXIT), ("fill", ms.REQUIRED_FILL)):
        for k in req:
            if k in ("status",):
                continue
            r = copy.deepcopy(base)
            (r[blk] if blk else r).pop(k)
            assert (ms.invalid_reason(r) or "").startswith("MISSING_FIELD:") or k == "lane", (blk, k, ms.invalid_reason(r))
    r = copy.deepcopy(base); r.pop("lane")
    assert ms.invalid_reason(r).startswith("MISSING_FIELD")


def test_validity_definition_on_real_row():
    base = _good_template()
    def mut(path, val):
        r = copy.deepcopy(base)
        d = r
        for p in path[:-1]:
            d = d[p]
        d[path[-1]] = val
        return ms.invalid_reason(r)
    assert ms.invalid_reason(base) is None
    assert mut(["identity_quarantined"], True) == "IDENTITY_QUARANTINED"
    assert mut(["exit", "deploy_flatten"], True) == "DEPLOY_FLATTEN"
    assert mut(["exit", "forced_close"], True) == "FORCED_CLOSE"
    assert mut(["pricing", "exit_status"], "FORCED_NO_BOOK") == "FORCED_OR_UNPRICED_EXIT"
    assert mut(["pricing", "exit_status"], "PAPER_FILL_FORCED") == "FORCED_OR_UNPRICED_EXIT"
    assert mut(["fill", "fill_basis"], "BBO") == "LEGACY_BBO_PRICING"
    assert mut(["fill", "fill_basis"], "NOT_INDEXED_SINCE_BOOT") is None          # live rows carry this after a boot
    assert mut(["pricing", "legacy_pricing"], True) == "LEGACY_PRICING"
    assert mut(["fresh_epoch_id"], "") == "UNSTAMPED_FRESH_EPOCH_ID"
    assert mut(["pricing", "fresh_eligible"], False) == "NOT_FRESH_ELIGIBLE"
    assert mut(["entry_ts"], None) == "UNSTAMPED_OR_STRADDLE_TS" and mut(["exit_ts"], None) == "UNSTAMPED_OR_STRADDLE_TS"
    assert mut(["lane"], "FAMILY_UNKNOWN") == "UNKNOWN_LANE"                       # explicit table, fail closed
    assert mut(["lane"], "FAMILY_GS06_COMMITTED_FADE_ATR_TP") == "RETIRED_LANE_OPAQUE"  # R22 archive-only
    assert mut(["pricing", "fill_valid"], False) == "NOT_BOOK_PRICED"
    assert mut(["fill", "evidence_status"], "PARTIAL") == "FILL_EVIDENCE_INCOMPLETE"
    r = copy.deepcopy(base); r["pricing"].update(exit_pricing_basis="REPRICED_CURRENT_TIME", exit_repriced_lag_s=4.0)
    assert ms.invalid_reason(r) is None
    r["pricing"]["exit_repriced_lag_s"] = 10.5
    assert ms.invalid_reason(r) == "REPRICED_BEYOND_10S_CAP"
    r["pricing"]["exit_repriced_lag_s"] = None
    assert ms.invalid_reason(r) == "REPRICED_BEYOND_10S_CAP"


def test_one_per_entry_non_overlapping_and_repriced_reported_separately():
    base = _good_template()
    def row(i, **kw):
        r = copy.deepcopy(base); r.update(trade_id=f"x{i}", entry_ts=T0 + i * 1000, exit_ts=T0 + i * 1000 + 100); r["exit"]["close_ts"] = r["exit_ts"]
        r["pricing"].update(kw); return r
    a, dup = row(1), row(1)
    overlap = row(2); overlap["entry_ts"] = a["entry_ts"] + 50
    rep = row(3, exit_pricing_basis="REPRICED_CURRENT_TIME", exit_repriced_lag_s=2.0)
    out = ms.valid_trades([a, dup, overlap, rep])
    assert out["n"] == 2 and out["repriced_current_time_n"] == 1
    assert out["rejected_by_reason"] == {"DUPLICATE_ENTRY": 1, "OVERLAPPING_HOLD": 1}


# ---------------------------------------------------------------- combos sealed holdout (Addendum 2) on real-shaped rows
def holdout(n=300, hours_step=0.5, mean=3.0, ctl_gap=2.0, frozen=T0, fdr=True, policies=5832, seed=7, looks=1, ctl=True, noise=2.0):
    rng = random.Random(seed)
    base = _good_template()
    trades = []
    for i in range(n):
        entry = frozen + 600 + i * hours_step * 3600
        net = mean + rng.uniform(-noise, noise)
        r = copy.deepcopy(base)
        r.update(trade_id=f"h{i}", entry_ts=entry, exit_ts=entry + 60, policy_signature="paper-policy-P")
        r["exit"]["close_ts"] = entry + 60
        r["pnl"].update(bp=net, bp_basis="net_pnl_bp")
        r["control_net_bp"] = (net - ctl_gap + rng.uniform(-1, 1)) if ctl else None
        trades.append(r)
    return {"policy_id": "paper-policy-P", "frozen_at": frozen, "sealed_at": frozen + 1, "looks": looks, "trades": trades,
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
    assert r["state"] == "FAIL" and r["gates"]["g5_training_bh_fdr_q010_all_5832"] is False
    r = ms.combos_holdout(holdout(ctl=False))
    assert r["state"] == "FAIL" and r["gates"]["g4_control_diff_ci_lower_gt0"] is False


def test_pending_under_300_trades_or_under_5_days_never_claims_edge():
    for h in (holdout(n=299, hours_step=0.5), holdout(n=300, hours_step=0.05)):
        r = ms.combos_holdout(h)
        assert r["state"] == "PENDING_SAMPLES" and r["edge_claim"] is False and r["ranking_allowed"] is False
    assert ms.combos_holdout(None)["state"] == "PENDING_SAMPLES"


def test_sealing_rules_row_before_freeze_other_policy_and_reopen_are_refused():
    h = holdout()
    h["trades"][0]["entry_ts"] = h["frozen_at"] - 5
    r = ms.combos_holdout(h)
    assert r["state"] == "REFUSED_NOT_SEALED" and r["rows_before_freeze"] == 1 and r["edge_claim"] is False
    h2 = holdout(); h2.pop("frozen_at")
    assert ms.combos_holdout(h2)["state"] == "REFUSED_NOT_SEALED"
    h3 = holdout(); h3["trades"][5]["policy_signature"] = "paper-policy-OTHER"
    assert ms.combos_holdout(h3)["state"] == "REFUSED_NOT_SEALED"
    assert ms.combos_holdout(holdout(looks=2))["state"] == "REFUSED_REOPENED"


def test_study_minimums_days_early_flags_only_and_static_states(monkeypatch):
    base = _good_template()
    def rows(n, net=1.0, step=900):
        out = []
        for i in range(n):
            r = copy.deepcopy(base); r.update(trade_id=f"s{i}", entry_ts=T0 + i * step, exit_ts=T0 + i * step + 60); r["exit"]["close_ts"] = r["exit_ts"]
            r["pnl"].update(bp=net, bp_basis="net_pnl_bp"); out.append(r)
        return out
    ids = (ms.hypothesis_of(base),)
    r0 = ms.study_state("H1", rows(300))                                          # H1 is an offline replay: no live tile counts
    assert r0["state"] == "PENDING_SAMPLES" and "offline replay, no live tile by design" in r0["why"]
    r = ms.study_state("H1", rows(300), ids)                                      # 300 trades spread over 75 h: >= 3 days
    assert r["state"] == "COUNTABLE" and r["min_days"] == 3 and r["edge_claim"] is False
    assert ms.study_state("H3", rows(300), ids)["min_days"] == 5 and ms.study_state("H3", rows(300), ids)["state"] == "PENDING_SAMPLES"
    assert ms.study_state("H1", rows(299), ids)["state"] == "PENDING_SAMPLES"
    r = ms.study_state("H1", rows(60, net=-2.0), ids)
    assert r["state"] == "PENDING_SAMPLES" and r["early_flags"][0]["flag"] == "KILL_RULE_MET"
    assert r["early_flags"][0]["auto_action"] is False and r["auto_tile_change"] is False
    assert ms.study_state("H1", rows(150, net=0.1), ids)["early_flags"][0]["flag"] == "RETIRE_RULE_MET"
    assert ms.study_state("H7")["state"] == "UNTESTABLE" and ms.study_state("H5")["state"] == "FEASIBILITY_ONLY"
    for s in ("H7", "H5"):
        assert ms.study_state(s)["state"] not in ("RED", "PENDING_SAMPLES")
