"""Provisional 5b synthetic regressions; no market/laptop/acceptance run."""
# PROVISIONAL, pre-freeze, non-evidential. Live OFF. No runtime integration.
PIN7_SPEC_SHA256 = "dc0cc45f24f5afee9a69718fcc120fc27450f80b6e35de1058f0ed0e68eedf79"
P8_SPEC_SHA256 = "28315aeb4423e88d4e3dadcf3e42005b29ddbba1f2e054467aeab7827135641e"
CT_TABLE_SHA256 = "37603987e6dabdc42ce89aec9482a3366d881f14c5d7c53356307e890c7595f0"
RULES_SHA256 = "4305f12a02d23f5cad689fd388387ffad3ecc50e02e614c883894d7ef3bf94f2"
RK_PROVISIONAL_RULING = "72c250a0eb82"

import copy
import hashlib
import json
from pathlib import Path
import pytest
from research import pin7_classifier as c
from research import pin_scoring as s
from research import pin_research_contracts as contracts
from research import pin_fixture_runner as harness
from research import pin_k3

CASES = harness.load_cases()


@pytest.fixture(autouse=True)
def synthetic_runroot(tmp_path, monkeypatch):
    monkeypatch.setenv("RUNROOT", str(tmp_path))


R17_KEY = "PIN_RESEARCH_1S"
# RK 2e5c08df822f addendum (3) + RK 4340ab310add (4): the 12 field9 row-pricer cases are SOURCE_RETIRED_R17.  They run
# UNPATCHED and assert the R3QuarantinedSource raise; they are never compared to HM goldens under the monkeypatch.
# C7-04 (size_walk), C7-14 (fee_fields_pair) and C7-15 (historical control) stay unpatched golden comparisons.
R17_ROW_CASE_IDS = frozenset({
    "C7-01-TAKER-LONG-TOP", "C7-02-TAKER-LONG-WALK", "C7-03-TAKER-SHORT-TOP", "C7-13-TAKER-STALE-TIMEOUT",
    "C7-05-MAKER-TOUCH-NOFILL", "C7-06-MAKER-TRADE-THROUGH", "C7-07-MAKER-PARTIAL", "C7-08-MARKETABLE-AT-PLACEMENT",
    "C7-09-EXIT-HS-TC-OFF", "C7-10-EXIT-REBOUND-NEVER-CHEAPER", "C7-11-EXIT-TP-TARGET", "C7-12-EXIT-HS-TC-ON-PAIR",
})
# HM 23a73c4c696b B3: the Part B judge's pinned import closure (pin_fixture_b_judge.SOURCE_PATHS minus the judge).
PART_B_CLOSURE_MODULES = (
    "bitfinex_cost_profile", "research.fill_model", "research.p8_fixture_adapter", "research.pin7_classifier",
    "research.pin_fill_operations", "research.pin_k3", "research.pin_research_contracts", "research.pin_scoring",
)


@pytest.fixture
def pin_research_1s_unquarantined(monkeypatch):
    # Documented test-scoped ACTIVE monkeypatch (r3_quarantine.ACTIVE): fixture-local, undone by monkeypatch after
    # the test; no env override, no conftest/global change.  Outputs under it are NON-EVIDENTIAL (R17), never cited.
    # Wrapper-logic tests only (RK 2e5c08df822f (3), RK 4340ab310add (4)); never for field9 results.
    from research import r3_quarantine
    assert R17_KEY in r3_quarantine.ACTIVE
    monkeypatch.setattr(r3_quarantine, "ACTIVE", set(r3_quarantine.ACTIVE) - {R17_KEY})



@pytest.fixture(scope="module")
def hash_verified_b_policy():
    import os
    from research import pin_fixture_b_judge as judge
    path = Path(os.environ["PIN_B_SUPPLEMENT"]).resolve(strict=True)
    data = judge._json(judge._read(path.parent, path.name,
        "7d843fbc7348f95aaa1bd1b6597505a773900f35ee0d783c1a7e0b8e085fa75e"))
    pairs = data["numeric_policy"]["cross_type_allowed_only_at"]
    slots = frozenset((p["case_id"], p["path"]) for p in pairs)
    candidate_ids = data["scalar_policy"]["case_ids"]
    candidates = frozenset(candidate_ids)
    assert len(pairs) == len(slots) == 30
    assert len(candidate_ids) == len(candidates) == 10
    return slots, candidates


@pytest.fixture
def approved_b_policy(case, request):
    # Field9/Part A never requests or reads the B supplement.
    if case["operation"] in {"field9_fill_model", "field9_historical_control"}:
        return None
    return request.getfixturevalue("hash_verified_b_policy")


@pytest.mark.parametrize("case", [case for case in CASES if case["operation"] != "candidate"], ids=lambda case: case["id"])
def test_hm_tape(case, approved_b_policy):
    reason = harness.pending_reason(case)
    if reason:
        pytest.skip(reason)
    if case["id"] in R17_ROW_CASE_IDS:
        # SOURCE_RETIRED_R17 (RK 4340ab310add (4)): unpatched positive refusal; no golden comparison.
        from research import r3_quarantine
        with pytest.raises(r3_quarantine.R3QuarantinedSource, match=R17_KEY):
            harness.dispatch(case["operation"], case["input"])
        return
    actual = harness.dispatch(case["operation"], case["input"])
    if case["operation"] in {"field9_fill_model", "field9_historical_control"}:
        assert not harness.subset_errors(case["expected"], actual)
    else:
        from research import pin_fixture_b_judge as judge
        judge.compare_case(case, actual, *approved_b_policy)


@pytest.mark.parametrize("case", [case for case in CASES if case["operation"] == "candidate"], ids=lambda case: case["id"])
def test_candidate_primitive_not_adapter_contract_acceptance(case):
    x = case["input"]
    assert c.candidate(x["V"], x["E"], x["z"], x["current"], harness.THRESHOLDS) == case["expected"]
    # Deliberate contract HOLD: JSON-object adapter cannot equal HM's string.
    assert harness.dispatch("candidate", x) == {"candidate": case["expected"]}
    assert harness.subset_errors(case["expected"], harness.dispatch("candidate", x))


def test_source_and_full_manifest_pins():
    assert len(CASES) == 233
    assert harness.verify_fixture_hashes()["manifest_sha256"] == harness.HM_MANIFEST_SHA256
    assert contracts.PRIMARY_N == 836
    assert contracts.RULES_SHA256 == RULES_SHA256


def test_journal_persists_exact_pending_and_no_fresh_budget(tmp_path):
    state = c.initial_state(0)
    for h in range(9, 12):
        state, _ = c.step(state, dict(h=h*3600, V=.01, E=.8, z=.8), harness.THRESHOLDS)
    journal = c.StateJournal(tmp_path, {"code": "a", "thresholds": "b"}, {"unit": "seconds", "origin": 0})
    journal.append(state, input_sha256="a"*64, version="v1", source_sequence=11)
    saved, _, seq = journal.load()
    assert saved == state and seq == 1 and saved["count"] == 3
    assert journal.path.name == "pin7-state.jsonl"
    resumed, trace = c.step(saved, dict(h=43200, V=.01, E=.8, z=.8), harness.THRESHOLDS)
    assert trace["accepted"] and resumed["last_flip"] == 43200
    journal.append(resumed, input_sha256="b"*64, version="v1", source_sequence=12)
    assert journal.load()[2] == 2


@pytest.mark.parametrize("mutation", ["content", "hash", "clock", "bindings", "truncated", "rewind", "empty"])
def test_journal_corruption_or_uncertain_identity_stops(tmp_path, mutation):
    journal = c.StateJournal(tmp_path, {"code": "a"}, {"unit": "seconds"})
    state = c.initial_state(0)
    state["last_valid"] = 3600
    journal.append(state, input_sha256="a"*64, version="v1", source_sequence=1)
    if mutation == "clock":
        journal.clock_mapping = {"unit": "hours"}
    elif mutation == "bindings":
        journal.bindings = {"code": "changed"}
    elif mutation == "rewind":
        state["last_valid"] = 0
        with pytest.raises(ValueError, match="STOP_STATE_REWIND"):
            journal.append(state, input_sha256="b"*64, version="v1", source_sequence=2)
        return
    elif mutation in ("truncated", "empty"):
        journal.path.write_text("{" if mutation == "truncated" else "")
    else:
        data = json.loads(journal.path.read_text())
        data["state" if mutation == "content" else "state_sha256"] = {} if mutation == "content" else "0"*64
        journal.path.write_text(json.dumps(data) + "\n")
    with pytest.raises((ValueError, KeyError)):
        journal.load()


def test_restart_missing_checkpoint_never_initialises_new_budget(tmp_path):
    with pytest.raises(ValueError, match="STOP_MISSING_CHECKPOINT"):
        c.StateJournal(tmp_path, {}, {}).load()


def test_full_state_corrupt_flip_spacing():
    state = c.initial_state(0)
    state.update(flips=[0, 8*3600], last_flip=8*3600)
    with pytest.raises(ValueError):
        c.validate_state(state)


def test_subhour_and_repeated_close_cannot_accept():
    state = c.initial_state(-86400)
    state, out = c.step(state, dict(h=0, V=.01, E=.8, z=.8), harness.THRESHOLDS)
    for h in (0, 7140):
        _, out = c.step(state, dict(h=h, V=.01, E=.8, z=.8), harness.THRESHOLDS)
        assert not out["accepted"] and not out["entry"] and out["status"] == "STOP_CLOCK"


def test_implicit_missing_hour_resets_pending_but_not_latch_or_history():
    state = c.initial_state(-86400)
    state, _ = c.step(state, dict(h=0, V=.04, E=0, z=0), harness.THRESHOLDS)
    state, out = c.step(state, dict(h=7200, V=.01, E=0, z=0), harness.THRESHOLDS)
    assert state["count"] == 1 and state["first"] == 7200
    assert state["latch"] == "WAIT_SHOCK" and not out["accepted"]


def test_raw_shock_veto_does_not_delay_safety_or_fabricate_flat():
    state = c.initial_state(0)
    state["label"] = "UP"
    state, out = c.step(state, dict(h=18000, V=.04, E=0, z=0, exposure=True), harness.THRESHOLDS)
    assert out["safety"] and not out["entry"] and not out["accepted"]
    assert state["latch"] == "WAIT_SHOCK"


def test_missing_entry_basis_open_interpretation_does_not_invent_fill_or_pnl():
    actual = s.score_pin15(side="LONG", entry=None, entry_fresh=False, exit_fresh=False, exit_request_s=10,
                           holding_bars=[dict(low=90, high=110)])
    assert actual["category"] == "NO_FILL" and actual["missing_price_layer"] == "UNPRICED_WORST"
    assert actual["exit"] == 90 and actual["pnl_bp"] is None and not actual["filled"] and not actual["win"]
    totals = s.pin15_counts([dict(actual, strategy="S", fold="F")])["S/F"]
    assert totals["a"] == 1 and totals["filled"] == 0 and totals["bad_share"] is None


def test_pin15_adapter_calls_unchanged_fill_model(pin_research_1s_unquarantined):
    actual = s.taker_entry_and_score({1: dict(bid=99, ask=100, bid_qty=1, ask_qty=1, source_age_sec=0)},
        side="LONG", qty=.1, decision_ts=0, latency_sec=1, exit_request_s=10,
        bar_1s=dict(t=11, low=98, high=103))
    assert actual["entry_fill"]["fill_price"] == 100
    assert actual["category"] == "EXIT_WORST_1S" and actual["exit"] == 98
    missing = s.taker_entry_and_score({}, side="LONG", qty=.1, decision_ts=0, latency_sec=1, exit_request_s=10)
    assert missing["category"] == "NO_FILL" and missing["capture"] == 1 and not missing["win"]
    assert actual["evidence"] == missing["evidence"] == "NON_EVIDENTIAL_R17"


def test_r17_pin_wrappers_refuse_while_quarantined():
    """RK 2e5c08df822f: production default (key ACTIVE) stops both wrappers and the adapter's field9_fill_model."""
    from research import r3_quarantine, pin_fill_operations as fills, p8_fixture_adapter as adapter
    assert r3_quarantine.is_active(R17_KEY)
    with pytest.raises(r3_quarantine.R3QuarantinedSource, match=R17_KEY):
        s.taker_entry_and_score({}, side="LONG", qty=.1, decision_ts=0, latency_sec=1, exit_request_s=10)
    row_cases = [case for case in CASES if case["operation"] == "field9_fill_model"
                 and case["input"]["entrypoint"] in r3_quarantine.FILL_MODEL_ROW_PRICERS]
    assert len(row_cases) == 12
    assert {case["id"] for case in row_cases} == R17_ROW_CASE_IDS
    for case in row_cases:
        with pytest.raises(r3_quarantine.R3QuarantinedSource, match=R17_KEY):
            adapter.evaluate(case["operation"], case["input"])
    # size_walk / fee_fields_pair do not touch the row pricers (HM G1): not guarded.
    assert "evidence" not in fills.evaluate_fill({"entrypoint": "fee_fields_pair", "notional_usd": 100})


def test_r17_guard_raises_before_any_retired_pricer_call(monkeypatch):
    """HM 23a73c4c696b B1 / RK 4340ab310add (B1): the guard raises BEFORE any retired pricer is entered.

    The pricers (not ACTIVE) are replaced by call-recording stand-ins; the key stays ACTIVE (production default).
    """
    from research import r3_quarantine, fill_model, pin_fill_operations as fills, p8_fixture_adapter as adapter
    assert r3_quarantine.is_active(R17_KEY)
    calls = []

    def stand_in(name, result):
        def record(*args, **kwargs):
            calls.append(name)
            return copy.deepcopy(result)
        return record

    no_fill = dict(status="NO_FILL", fill_price=None, filled_qty=0, liquidity="TAKER", size_walk=[])
    for name in ("taker_fill_rows", "maker_fill_rows", "optimistic_touch_rows"):
        monkeypatch.setattr(fill_model, name, stand_in("fill_model." + name, no_fill))
    monkeypatch.setattr(fill_model, "realistic_exit_margin", stand_in("fill_model.realistic_exit_margin", (0.0, 0)))
    monkeypatch.setattr(s, "taker_fill_rows", stand_in("pin_scoring.taker_fill_rows", no_fill))
    row_cases = [case for case in CASES if case["id"] in R17_ROW_CASE_IDS]
    assert len(row_cases) == 12
    assert {case["input"]["entrypoint"] for case in row_cases} == set(r3_quarantine.FILL_MODEL_ROW_PRICERS)
    for case in row_cases:
        with pytest.raises(r3_quarantine.R3QuarantinedSource, match=R17_KEY):
            adapter.evaluate(case["operation"], case["input"])
        assert calls == [], case["id"]
        with pytest.raises(r3_quarantine.R3QuarantinedSource, match=R17_KEY):
            fills.evaluate_fill(copy.deepcopy(case["input"]))
        assert calls == [], case["id"]
    with pytest.raises(r3_quarantine.R3QuarantinedSource, match=R17_KEY):
        s.taker_entry_and_score({1: dict(bid=99, ask=100, bid_qty=1, ask_qty=1, source_age_sec=0)},
            side="LONG", qty=.1, decision_ts=0, latency_sec=1, exit_request_s=10, bar_1s=dict(t=11, low=98, high=103))
    assert calls == []


def test_r17_part_b_dispatch_leaves_r3_quarantine_unloaded(tmp_path):
    """RK 5e55e1f6849d (3) / RK 4340ab310add (3) / HM 23a73c4c696b B3, in a fresh interpreter (this CI process imports
    r3_quarantine elsewhere): dispatching every non-field9 in-repo tape case (Part B, 218) loads only the 8 judge-closure
    modules and never research.r3_quarantine."""
    import os
    import subprocess
    import sys
    script = r'''
import json, sys
from pathlib import Path
from research import p8_fixture_adapter as adapter
root = Path(adapter.__file__).with_name("pin_fixtures") / "tapes"
index = json.loads((root / "index.json").read_text())
cases = [case for name in index["files"] for case in json.loads((root / name).read_text())]
part_b = [case for case in cases if case["operation"] not in ("field9_fill_model", "field9_historical_control")]
seen = lambda: sorted(m for m in sys.modules if m == "bitfinex_cost_profile" or m.startswith("research."))
after_import = seen()
ok = sum(isinstance(adapter.evaluate(case["operation"], case["input"]), dict) for case in part_b)
print(json.dumps(dict(part_b_count=index["Part_B_count"], dispatched=len(part_b), ok=ok, after_import=after_import,
                      after_dispatch=seen(), r3_quarantine_loaded="research.r3_quarantine" in sys.modules)))
'''
    env = dict(os.environ, RUNROOT=str(tmp_path), PYTHONDONTWRITEBYTECODE="1")
    done = subprocess.run([sys.executable, "-B", "-c", script], cwd=str(Path(__file__).resolve().parent), env=env,
                          capture_output=True, text=True, timeout=600)
    assert done.returncode == 0, done.stderr
    result = json.loads(done.stdout.strip().splitlines()[-1])
    assert result["part_b_count"] == result["dispatched"] == result["ok"] == 218
    assert result["r3_quarantine_loaded"] is False
    assert result["after_import"] == result["after_dispatch"] == sorted(PART_B_CLOSURE_MODULES)


def test_r17_field9_output_is_non_evidential(pin_research_1s_unquarantined):
    for case in CASES:
        if case["operation"] == "field9_fill_model" and case["input"]["entrypoint"] != "size_walk" \
                and case["input"]["entrypoint"] != "fee_fields_pair":
            assert harness.dispatch(case["operation"], case["input"])["evidence"] == "NON_EVIDENTIAL_R17"


def test_exit_fallback_requires_exact_1s_bar():
    with pytest.raises(ValueError, match="EXIT_BAR_NOT_AT_1S"):
        s.score_pin15(side="LONG", entry=100, entry_fresh=True, exit_fresh=False, exit_request_s=10,
                      bar_1s=dict(t=10, low=98, high=103))


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_no_fallback_bar_cannot_produce_win(side):
    actual = s.score_pin15(side=side, entry=100, entry_fresh=True, exit_fresh=False, exit_request_s=10)
    assert actual["pnl_bp"] is None and not actual["win"] and actual["status"] == "HOLD_NO_EXTREME"


def test_pin16_caller_predicates_are_required_and_no_slippage_gate():
    common = dict(i=12, ii=10, backtest=20, N=836, signal_id="s", entry_time=0, exit_time=1)
    good = s.dual_record(**common, confidence_ok=True, risk_ok=True)
    assert good["slippage_bp"] == -2 and good["decision"] == "ELIGIBLE_UNIT_ONLY"
    assert good["alpha"] == .05/836 and "NOT_TESTED" in good["validation"]
    assert s.dual_record(**common, confidence_ok=False, risk_ok=True)["decision"] == "FAIL"
    assert s.dual_record(**common, confidence_ok=True, risk_ok=False)["decision"] == "FAIL"
    with pytest.raises(TypeError):
        s.dual_record(**common, confidence_ok=1, risk_ok=True)


def test_conflicting_close_fails_instead_of_deduplicating_different_pnl():
    with pytest.raises(ValueError, match="CONFLICTING_CLOSE"):
        s.branch_ledger([dict(close_key="p", entry_branch="U", i=1, ii=2), dict(close_key="p", entry_branch="U", i=3, ii=2)])


def test_b3_metadata_only_no_file_open_and_every_row_guarded():
    result = contracts.b3_guard(dict(events=[dict(kind="B2_PNL", file="/must/not/open")]))
    assert result["trace"][0]["alarm"] and not result["trace"][0]["read_allowed"]


def test_k3_previous_kill_is_monotone_and_never_pass():
    result = pin_k3.score_k3(dict(events=[], prior_kill=True))
    assert result["kill"] and result["status"] == "KILL"


@pytest.mark.parametrize("expected,actual", [([1, 2], [2, 1]), ([1], [1, 2]), (True, 1), ({"missing": 1}, {})])
def test_asserted_subset_comparator_does_not_relax_lists_types_or_missing_keys(expected, actual):
    assert harness.subset_errors(expected, actual)


def test_asserted_subset_comparator_allows_extra_dict_fields_and_declared_float_tolerance():
    assert harness.subset_errors({"x": 1.0}, {"x": 1 + 1e-8})
    assert not harness.subset_errors({"x": 1.0}, {"x": 1 + 1e-10, "extra": True})


def test_inputs_are_never_mutated():
    # HM 23a73c4c696b C1: unpatched; the 12 SOURCE_RETIRED_R17 row cases must raise and still leave inputs unchanged.
    from research import r3_quarantine
    refused = set()
    for case in CASES:
        if harness.pending_reason(case):
            continue
        before = copy.deepcopy(case["input"])
        if case["id"] in R17_ROW_CASE_IDS:
            with pytest.raises(r3_quarantine.R3QuarantinedSource, match=R17_KEY):
                harness.dispatch(case["operation"], case["input"])
            refused.add(case["id"])
        else:
            harness.dispatch(case["operation"], case["input"])
        assert case["input"] == before
    assert refused == R17_ROW_CASE_IDS


@pytest.mark.parametrize("missing", ["bridge_present", "bridge_allowed", "checkpoint"])
def test_calibration_seal_requires_causal_bridge_without_refit(missing):
    records = [dict(h=(j+1)*3600, V=j+1, E=j/719) for j in range(720)]
    seal = c.CalibrationSeal.create(records, a=0, b=720*3600, source_sha256="a"*64, bindings={"code": "c"}, complete_minutes=True)
    args = dict(start=720*3600, a=0, b=720*3600, bindings={"code": "c"}, bridge_present=True, bridge_allowed=True, checkpoint="checkpoint")
    before = seal.reuse(**args)
    records[-1]["V"] = 1e20  # caller mutation cannot alter the immutable seal
    assert before == seal.reuse(**args) and not before["refit"]
    args[missing] = None
    with pytest.raises(ValueError, match="HOLD_BRIDGE"):
        seal.reuse(**args)


def test_calibration_seal_rejects_outcomes_and_postfreeze_substitution():
    records = [dict(h=(j+1)*3600, V=j+1, E=j/719) for j in range(720)]
    seal = c.CalibrationSeal.create(records, a=0, b=720*3600, source_sha256="a"*64, bindings={"code": "c"}, complete_minutes=True)
    with pytest.raises(ValueError, match="STOP_CALIBRATION_REBIND"):
        seal.reuse(start=720*3600, a=-3600, b=720*3600, bindings={"code": "c"}, bridge_present=True, bridge_allowed=True, checkpoint="c")
    records[0]["PnL"] = 10
    with pytest.raises(ValueError, match="OUTCOME_FIELDS"):
        c.CalibrationSeal.create(records, a=0, b=720*3600, source_sha256="a"*64, bindings={"code": "c"}, complete_minutes=True)


def test_coincident_safety_and_flip_one_request_no_fabricated_flat():
    actual = c.ordered_close_intents(position_intent_id="position-one", safety_exit=True, accepted_flip=True, exposure=True, residual=True)
    assert len(actual) == 1 and actual[0]["priority"] == "SAFETY"
    assert actual[0]["reasons"] == ["SAFETY", "REGIME_FLIP"] and not actual[0]["actual_flat"]
    assert c.ordered_close_intents(position_intent_id="position-one", safety_exit=True, accepted_flip=True, exposure=False, residual=False) == []


def test_future_or_changed_candle_clock_not_receipt_evidence():
    case = next(case for case in CASES if case["id"] == "C02-GOLDEN-FLAT")
    bars = copy.deepcopy(case["input"]["bars"])
    bars[-1]["received"] -= 1
    assert c.candle_features(bars, 86400)["reason"] == "CLOCK"


def test_selection_N_836_primary_and_1672_sensitivity_are_not_interchangeable():
    assert contracts.PRIMARY_N == 836
    common = dict(i=7, ii=10, backtest=20, signal_id="s", entry_time=0, exit_time=1, risk_ok=True)
    p = .05 / 1000
    primary = s.dual_record(**common, N=836, confidence_ok=p <= .05 / 836)
    sensitivity = s.dual_record(**common, N=1672, confidence_ok=p <= .05 / 1672)
    assert primary["decision"] == "ELIGIBLE_UNIT_ONLY" and sensitivity["decision"] == "FAIL"


def test_journal_source_sequence_cannot_be_replayed(tmp_path):
    journal = c.StateJournal(tmp_path, {"code": "a"}, {"unit": "seconds"})
    state = c.initial_state(0)
    journal.append(state, input_sha256="a"*64, version="v1", source_sequence=1)
    with pytest.raises(ValueError, match="STOP_SOURCE_SEQUENCE"):
        journal.append(state, input_sha256="a"*64, version="v1", source_sequence=1)


def test_future_branch_trades_never_manufacture_parent_minimum():
    row = dict(intent="future", branch="U", filled=True, utc_day=101)
    result = s.pin8_coverage([row], terminal_day=100, parent_days=28)
    assert result["parent_trades"] == 0 and result["action"] == "FLAT"


@pytest.mark.parametrize("case_id,change", [
    ("P15-B-LONG-1S", "wrong_exit_side"),
    ("C04-SHOCK-EQUALITY", "strict_shock"),
    ("P16-II-CANNOT-RESCUE-LOSS", "gross_rescue"),
    ("P15-FLAG-5.0PCT", "greater_equal_flag"),
    ("T4-CAP-0-86400", "inclusive_lower_endpoint"),
])
def test_fixture_assertions_detect_semantic_mutants(case_id, change):
    case = next(case for case in CASES if case["id"] == case_id)
    inputs = copy.deepcopy(case["input"])
    actual = harness.dispatch(case["operation"], inputs)
    gold = {"candidate": case["expected"]} if case["operation"] == "candidate" else case["expected"]
    # Each mutant asserts its own semantic field, not unrelated wire-number types.
    if change == "wrong_exit_side":
        gold = {"exit": case["expected"]["exit"]}
    elif change == "gross_rescue":
        gold = {"decision": case["expected"]["decision"]}
    assert not harness.subset_errors(gold, actual)
    if change == "wrong_exit_side":
        inputs["side"] = "SHORT"
        actual = harness.dispatch(case["operation"], inputs)
    elif change == "strict_shock":
        inputs["V"] -= 1e-12
        actual = harness.dispatch(case["operation"], inputs)
    elif change == "gross_rescue":
        actual["decision"] = "ELIGIBLE_UNIT_ONLY"
    elif change == "greater_equal_flag":
        actual["S/F"]["flag"] = True
    else:
        actual["allowed"] = False
    assert harness.subset_errors(gold, actual)


@pytest.mark.parametrize("change", [dict(last_flip=7200), dict(count=7), dict(first=7200)])
def test_checkpoint_uncertain_clocks_or_pending_count_stop(change):
    state = c.initial_state(-86400)
    state.update(pending="UP", first=0, last_valid=3600, count=2)
    state.update(change)
    with pytest.raises(ValueError, match="STOP_CORRUPT"):
        c.validate_state(state)


def test_journal_initial_checkpoint_without_valid_close_can_advance(tmp_path):
    journal = c.StateJournal(tmp_path, {"code": "a"}, {"unit": "seconds"})
    state = c.initial_state(0)
    journal.append(state, input_sha256="a"*64, version="v1", source_sequence=0)
    state, _ = c.step(state, dict(h=3600, V=.01, E=0, z=0), harness.THRESHOLDS)
    journal.append(state, input_sha256="b"*64, version="v1", source_sequence=1)
    assert journal.load()[0] == state


def test_nested_performance_fields_cannot_enter_id_selector():
    case = next(case for case in CASES if case["id"] == "R2-08-GRAMMAR-MIN-NUMERIC-ID")
    grammar = copy.deepcopy(case["input"]["grammar"])
    grammar[0]["entry"]["PnL"] = 100
    assert contracts.select_ids(grammar)["status"] == "STOP"



def test_adapter_map_exact_frozen_operations_and_real_functions():
    from research import p8_fixture_adapter as adapter
    assert set(adapter.OPERATION_MAP) == {case["operation"] for case in CASES}
    assert len(adapter.OPERATION_MAP) == 27
    assert all(function.__module__.startswith("research.") and "fixture" not in function.__module__ for function in adapter.OPERATION_MAP.values())


@pytest.mark.parametrize("number", [float("nan"), float("inf"), float("-inf")])
def test_adapter_rejects_nonfinite_inputs_and_outputs(number, monkeypatch):
    from research import p8_fixture_adapter as adapter
    with pytest.raises(ValueError):
        adapter.evaluate("utc_days", {"seconds": [number]})
    monkeypatch.setitem(adapter.OPERATION_MAP, "safety", lambda data: {"decision": number})
    with pytest.raises(ValueError):
        adapter.evaluate("safety", {})


def test_checkpoint_adapter_requires_RUNROOT_and_contains_all_temp_writes(monkeypatch, tmp_path):
    from research import p8_fixture_adapter as adapter
    case = next(case for case in CASES if case["id"] == "P8R10-12-NOLOG-TERMINAL-NO-AUTORESUME")
    monkeypatch.delenv("RUNROOT")
    with pytest.raises(ValueError, match="RUNROOT"):
        adapter.evaluate(case["operation"], case["input"])
    writes = []
    original = Path.write_text
    def tracked(path, *args, **kwargs):
        assert path.resolve().is_relative_to(tmp_path.resolve())
        writes.append(path)
        return original(path, *args, **kwargs)
    monkeypatch.setenv("RUNROOT", str(tmp_path))
    monkeypatch.setattr(Path, "write_text", tracked)
    actual = adapter.evaluate(case["operation"], case["input"])
    assert writes and all(not path.exists() for path in writes)
    assert actual["latched"] and not actual["auto_resume"] and not actual["B3_scored"]
    assert all(row["warmup_state"] == "DISCARDED" and not row["read_allowed"] for row in actual["trace"])


@pytest.mark.parametrize("column", ["P&L", "pnl_usd", "Sharpe_ratio", "return_bp", "score", "ranking"])
def test_whole_input_pollution_never_salvages_ids(column):
    from research import p8_fixture_adapter as adapter
    case = next(case for case in CASES if case["id"] == "P8R4-08A-EXACT-12-4-3-3")
    data = copy.deepcopy(case["input"])
    data["auxiliary"] = {column: None}
    actual = adapter.evaluate("grammar_select", data)
    assert actual == {"status": "STOP", "alarm": True, "IDs": None}



def test_adapter_boundary_does_not_access_envelopes_or_oracles():
    import ast
    from research import p8_fixture_adapter as adapter
    tree = ast.parse(Path(adapter.__file__).read_text())
    strings = {node.value for node in ast.walk(tree) if isinstance(node, ast.Constant) and isinstance(node.value, str)}
    assert not {"expected", "case_id", "id"} & strings
    assert not any(isinstance(node, (ast.Import, ast.ImportFrom)) and "oracle" in ast.unparse(node) for node in ast.walk(tree))


# r3 additions only: synthetic inputs, never frozen tape evaluations.
@pytest.mark.parametrize("side,entry,exit_px,want,kind", [
    ("LONG", 100, 98, -200, int), ("SHORT", 100, 103, -300, int),
    ("LONG", .3, .30003, 1, int), ("SHORT", .3, .30003, -1, int),
    ("LONG", 3, 4, 10000/3, float), ("SHORT", 3, 4, -10000/3, float),
    ("LONG", 100, 100.00000000000001, 1e-12, float),
])
def test_r3_pin15_exact_decimal_bp(side, entry, exit_px, want, kind):
    result = s.score_pin15(side=side, entry=entry, exit_quote=exit_px,
                          entry_fresh=True, exit_fresh=True, exit_request_s=10)
    assert type(result["pnl_bp"]) is kind
    assert result["pnl_bp"] == want
    assert result["win"] is (want > 0)


@pytest.mark.parametrize("side,prices,want_mfe,want_final,kind", [
    ("LONG", [100.2, 99.5], 20, -50, int),
    ("SHORT", [99.8, 101], 20, -100, int),
    ("LONG", [.30003, .29997], 1, -1, int),
    ("LONG", [4, 2], 10000/3, -10000/3, float),
])
def test_r3_f274_exact_selection_and_bp(side, prices, want_mfe, want_final, kind):
    entry = .3 if prices[0] < 1 else (3 if prices[0] == 4 else 100)
    rows = [dict(bid=p, ask=p, bid_size=1, ask_size=1, decision_ms=i,
                 event_ms=i, sequence=i) for i, p in enumerate(prices)]
    result = pin_k3.executable_l1_mtm(dict(final=True, l1=rows, side=side, entry=entry,
                                         qty=1, synthetic_age_limit_ms=1))
    assert result["mfe_bp"] == want_mfe and result["final_bp"] == want_final
    assert type(result["mfe_bp"]) is kind and type(result["final_bp"]) is kind


def _r3_synthetic_bars():
    return [dict(end=i*60, open=100., high=100., low=100., close=100., complete=True)
            for i in range(1441)]


@pytest.mark.parametrize("logs,want,kind", [
    ([0., -0.] * 12, 0, int),
    ([0., .001, -0.] * 8, 1, int),
    ([-.001, 0., -0.] * 8, 1, int),
    ([1., -1e-30] + [0.] * 22, 1.0, float),
    ([.5, -.25] + [0.] * 22, 1/3, float),
    ([1., -1.] + [0.] * 22, 0.0, float),
])
def test_r3_e_identity_branches_common_terms(logs, want, kind, monkeypatch):
    import math
    # Fault-inject the hourly FLOAT TERMS after upstream checks. Minute returns
    # are separate calls; exactly 24 hourly calls follow their 1440 calls.
    calls = []
    values = iter([0.] * 1440 + logs)
    def log(_):
        calls.append(1)
        return next(values)
    monkeypatch.setattr(c.math, "log", log)
    result = c.candle_features(_r3_synthetic_bars(), 86400, availability_mode="historical_final")
    assert len(calls) == 1464
    assert type(result["E"]) is kind and result["E"] == want
    assert result["R"] == math.fsum(logs)
    assert all(type(result[k]) is float for k in ("V", "R", "z", "B"))


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_r3_nonfinite_hourly_terms_raise(bad, monkeypatch):
    values = iter([0.] * 1440 + [bad] + [0.] * 23)
    monkeypatch.setattr(c.math, "log", lambda _: next(values))
    with pytest.raises(ValueError, match="STOP_NONFINITE_HOURLY_LOG"):
        c.candle_features(_r3_synthetic_bars(), 86400, availability_mode="historical_final")


def test_r3_real_synthetic_hourly_logs_and_vetoes():
    import math
    bars = _r3_synthetic_bars()
    for i, b in enumerate(bars):
        p = 100 * math.exp(.0001*i)
        b.update(open=p, high=p, low=p, close=p)
    actual = c.candle_features(bars, 86400, availability_mode="historical_final")
    hourly = [b["close"] for b in bars[::60]]
    R = math.fsum(math.log(b/a) for a,b in zip(hourly, hourly[1:]))
    assert type(actual["E"]) is int and actual["E"] == 1
    assert actual["R"] == R and actual["z"] == R / actual["V"]
    assert c.candle_features(bars[:-1], 86400, availability_mode="historical_final")["reason"] == "COUNT"
    bars[-1]["close"] = 0
    assert c.candle_features(bars, 86400, availability_mode="historical_final")["reason"] == "OHLC"
