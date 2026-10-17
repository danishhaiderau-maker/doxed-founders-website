import json

import fly_full_reset_gate as gate

OK_BUILD = {"features": "13 registered tiles; paper ON: 5 (T1 a); live-eligible switch ON: 0 (none); DISARMED. Derived"}
OK_HEALTH = {"force_paper_mode": True, "live_armed": False, "bitfinex_live_enabled": False}


def test_live_facts_pass_when_disarmed_paper_only_and_no_live_eligible_tile():
    facts = gate.build_facts(OK_BUILD, OK_HEALTH)
    assert facts == {"live_eligible_switch_on": 0, "disarmed": True, "force_paper_mode": True, "live_armed": False,
                     "bitfinex_live_enabled": False}
    assert gate.live_failures(facts) == []


def test_live_facts_fail_closed():
    armed = gate.build_facts({"features": "x; live-eligible switch ON: 0 (none); ARMED."}, OK_HEALTH)
    assert any("DISARMED" in m for m in gate.live_failures(armed))
    unreadable = gate.build_facts({"features": "x; DISARMED."}, OK_HEALTH)
    assert any("live-eligible" in m for m in gate.live_failures(unreadable))
    assert gate.live_failures(gate.build_facts({}, {}))  # unknown is a failure, never a pass
    assert gate.live_failures(gate.build_facts(OK_BUILD, {**OK_HEALTH, "force_paper_mode": False}))


def _clean():
    return ({"total_matching": 0}, {"per_tile": [{"lane": "A", "fly_n": 0}]},
            {"per_tile": [{"lane": "A", "closed": 0, "fresh_eligible": 0}]},
            {"lanes": [{"lane": "A", "closes": 0, "mean_book_slippage_usd": None, "n_slippage_rows": 0}]})


def test_zero_rows_pass_and_every_leftover_fails():
    assert gate.zero_row_failures(*_clean()) == []
    t, r, p, l = _clean()
    assert gate.zero_row_failures({"total_matching": 3}, r, p, l)
    assert gate.zero_row_failures(t, {"per_tile": [{"lane": "A", "fly_n": 2}]}, p, l)
    assert gate.zero_row_failures(t, r, {"per_tile": [{"lane": "A", "closed": 1, "fresh_eligible": 0}]}, l)
    assert gate.zero_row_failures(t, r, p, {"lanes": [{"lane": "A", "closes": 1}]})
    assert gate.zero_row_failures(t, r, p, {"lanes": [{"lane": "A", "closes": 0, "mean_book_slippage_usd": 0.0}]})
    assert gate.zero_row_failures(t, r, {"per_tile": []}, l)  # an empty answer is not proof


def test_leftover_gate_allows_only_small_delete_class_leftovers():
    plan = {"mode": "full-reset-no-archive", "total_delete_files": 3, "total_delete_bytes": 4096, "unclassified_data_files_count": 0}
    assert gate.leftover_failures(plan, 200, 64 << 20) == []
    assert gate.leftover_failures({**plan, "total_delete_files": 201}, 200, 64 << 20)
    assert gate.leftover_failures({**plan, "total_delete_bytes": 65 << 20}, 200, 64 << 20)
    assert gate.leftover_failures({**plan, "unclassified_data_files_count": 1}, 200, 64 << 20)
    assert gate.leftover_failures({}, 200, 64 << 20)  # an empty or foreign document is not proof


OK = {"canonical_epoch": {"aliases": {"collector_v22_epoch_id": "epoch-x", "fresh_epoch_id": None}}}


def test_dv_gate_needs_a_stamped_row_matching_the_epoch_and_a_fresh_epoch_id():
    fs = {"set": True, "epoch_ts": 1792000000.4}
    row = json.dumps({"s": 1, "b": [], "a": [], "dv": "fse-1792000000"})
    assert gate.dv_failures(row, OK, fs) == []
    assert gate.dv_failures(json.dumps({"s": 1}), OK, fs)  # no dv
    assert gate.dv_failures(row.replace("0000", "9999"), OK, fs)  # wrong epoch
    assert gate.dv_failures(row, {}, fs)  # no bound epoch alias
    assert gate.dv_failures(row, {"canonical_epoch": {"aliases": {"collector_v22_epoch_id": None, "fresh_epoch_id": None}}}, fs)
    assert gate.dv_failures(row, OK, {"set": False})  # epoch not stamped
    assert gate.dv_failures("", OK, fs) and gate.dv_failures("not json", OK, fs)


def _audit(**kw):
    base = {"authoritative": True, "fresh": True, "flat": True, "checked_ts": 1000.0, "error": None, "open_position_count": 0, "open_order_count": 0,
            "orphan_order_ids": [], "orphan_position_ids": []}
    base.update(kw)
    return {k: v for k, v in base.items() if v != "GONE"}


def test_exchange_proof_requires_every_field_present_and_zero():
    proof, bad = gate.exchange_proof(_audit())
    assert bad == [] and proof["exchange_positions"] == 0 and proof["exchange_delta_btc"] == 0.0 and proof["proof_ts"] == 1000.0
    for key in ("open_position_count", "open_order_count", "orphan_order_ids", "orphan_position_ids",
                "authoritative", "fresh", "flat", "checked_ts", "error"):
        assert gate.exchange_proof(_audit(**{key: "GONE"}))[1], key  # a missing key is NOT zero
    assert gate.exchange_proof(_audit(open_position_count=1))[1]
    assert gate.exchange_proof(_audit(open_order_count=2))[1]
    assert gate.exchange_proof(_audit(orphan_order_ids=["x"]))[1]
    assert gate.exchange_proof(_audit(fresh=False))[1]
    assert gate.exchange_proof(_audit(open_position_count=None))[1]
    assert gate.exchange_proof({})[1] and gate.exchange_proof(None)[1]


def test_exchange_proof_uses_the_audits_own_clock_and_requires_a_null_error():
    proof, bad = gate.exchange_proof(_audit(checked_ts=1234.5))
    assert bad == [] and proof["proof_ts"] == 1234.5  # NOT the runner clock
    assert gate.exchange_proof(_audit(error="boom"))[1] and gate.exchange_proof(_audit(error=""))[1]
    assert gate.exchange_proof(_audit(checked_ts="1000"))[1] and gate.exchange_proof(_audit(checked_ts=0))[1]
    assert gate.exchange_proof(_audit(checked_ts=True))[1]


BASELINE_BUILD = {"features": "7 registered tiles; paper ON: 5 (T1 a); live-eligible switch ON: 7 (T1, T2, T3, T4, T5, T6, T7); live copy DISARMED (force_paper)."}


def test_a_nonzero_live_eligible_count_is_the_normal_baseline_and_passes_when_unchanged():
    before = gate.build_facts(BASELINE_BUILD, OK_HEALTH)
    assert before["live_eligible_switch_on"] == 7 and before["disarmed"] is True
    assert gate.live_failures(before) == []  # NOT required to be 0
    after = gate.build_facts(BASELINE_BUILD, OK_HEALTH)
    assert gate.verify_live_failures(before, after) == []


def test_verify_fails_when_the_count_changes_or_anything_arms():
    before = gate.build_facts(BASELINE_BUILD, OK_HEALTH)
    fewer = gate.build_facts({"features": BASELINE_BUILD["features"].replace("ON: 7", "ON: 6")}, OK_HEALTH)
    more = gate.build_facts({"features": BASELINE_BUILD["features"].replace("ON: 7", "ON: 8")}, OK_HEALTH)
    for after in (fewer, more):
        assert any("live_eligible_switch_on" in m for m in gate.verify_live_failures(before, after))
    armed = gate.build_facts({"features": BASELINE_BUILD["features"].replace("DISARMED", "ARMED")}, OK_HEALTH)
    assert gate.verify_live_failures(before, armed)
    for bad_health in ({"live_armed": True}, {"bitfinex_live_enabled": True}, {"force_paper_mode": False}):
        assert gate.verify_live_failures(before, gate.build_facts(BASELINE_BUILD, {**OK_HEALTH, **bad_health}))
    assert gate.verify_live_failures(before, gate.build_facts({}, {}))  # unknown after the wipe is a failure
    assert gate.verify_live_failures(gate.build_facts({}, {}), gate.build_facts({}, {}))  # unreadable before AND after still fails
