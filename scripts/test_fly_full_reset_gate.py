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
    eligible = gate.build_facts({"features": "x; live-eligible switch ON: 2 (T1, T2); DISARMED."}, OK_HEALTH)
    assert any("live-eligible" in m for m in gate.live_failures(eligible))
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
