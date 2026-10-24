"""RK (24 Oct 2026): exchange_mirror.live_fences ok:false / unreadable / missing on ANY account (both accounts covered) =>
arm gate BLOCK, exchange-health summary/gaps non-GREEN, alert raised."""
import live_copy_lifecycle as lcl
import live_fences_health as lfh

GOOD = {"schema": "relay_live_fences_v1", "ok": True, "blocked": False, "exit_fill": [], "exit_fill_cycle": [], "other": [],
        "exit_fill_fence_write_failures": [], "boot_reconcile": None}
BAD = {
    "ok_false": ({"schema": "relay_live_fences_v1", "ok": False, "error": "relation LiveEntryFence does not exist"},
                 lfh.CAUSE_NOT_OK),
    "missing": (None, lfh.CAUSE_MISSING),
    "unreadable_str": ("garbage", lfh.CAUSE_UNREADABLE),
    "unreadable_schema": ({"schema": "other", "blocked": False}, lfh.CAUSE_UNREADABLE),
    "unreadable_no_blocked": ({"schema": "relay_live_fences_v1"}, lfh.CAUSE_UNREADABLE),
}
ACCTS = ("acctA", "acctB")


def mirror(lf, drop=False):
    m = {"ok": True, "flat": True, "read_age_s": 2.0, "open_orders": [], "stops": [], "positions": []}
    if not drop:
        m["live_fences"] = lf
    return m


def view(a_lf, b_lf, a_drop=False, b_drop=False, armed=ACCTS):
    return {"reachable": True, "armed_accounts": list(armed), "accounts": [
        {"account": "acctA", "armed": "acctA" in armed, "exchange_mirror": mirror(a_lf, a_drop)},
        {"account": "acctB", "armed": "acctB" in armed, "exchange_mirror": mirror(b_lf, b_drop)}]}


def _cases():
    for name, (lf, cause) in BAD.items():
        for bad_acct in ACCTS:
            drop = name == "missing"
            a = (lf, drop) if bad_acct == "acctA" else (GOOD, False)
            b = (lf, drop) if bad_acct == "acctB" else (GOOD, False)
            yield name, bad_acct, cause, view(a[0], b[0], a[1], b[1])


def test_all_good_both_accounts_is_green_and_unblocked():
    v = view(GOOD, GOOD)
    assert lfh.arm_block(v) is None
    s = lfh.summary(lcl.exchange_view(v), v["armed_accounts"])
    assert s["verdict"] == "GREEN" and s["gaps"] == []
    assert lfh.mirror_alerts(lcl.exchange_view(v), ACCTS) == []


def test_any_account_bad_blocks_arm_goes_non_green_and_alerts():
    seen = set()
    for name, bad_acct, cause, v in _cases():
        blk = lfh.arm_block(v)
        assert blk == f"LIVE_FENCES_UNREADABLE:{bad_acct}={cause}", (name, bad_acct, blk)
        ex = lcl.exchange_view(v)
        s = lfh.summary(ex, v["armed_accounts"])
        assert s["verdict"] == "RED", (name, bad_acct)
        assert [g["account"] for g in s["gaps"]] == [bad_acct] and s["gaps"][0]["code"] == "LIVE_FENCES_UNREADABLE"
        assert s["accounts"][bad_acct]["ok"] is False
        other = "acctB" if bad_acct == "acctA" else "acctA"
        assert s["accounts"][other]["ok"] is True
        seen.add(bad_acct)
    assert seen == set(ACCTS)


def test_both_accounts_bad_reports_both():
    v = view(BAD["ok_false"][0], None, b_drop=True)
    assert lfh.arm_block(v) == "LIVE_FENCES_UNREADABLE:acctA=LIVE_FENCES_OK_FALSE,acctB=LIVE_FENCES_MISSING"
    assert {g["account"] for g in lfh.summary(lcl.exchange_view(v), ACCTS)["gaps"]} == set(ACCTS)


def test_unarmed_account_is_amber_never_green():
    v = view(GOOD, BAD["ok_false"][0], armed=("acctA",))
    s = lfh.summary(lcl.exchange_view(v), ["acctA"])
    assert s["verdict"] == "AMBER" and s["gaps"][0]["severity"] == "AMBER"
    assert lfh.arm_block(v) == "LIVE_FENCES_UNREADABLE:acctB=LIVE_FENCES_OK_FALSE"   # still blocks arming


def test_no_mirror_or_no_accounts_fails_closed():
    assert lfh.arm_block({}) == "LIVE_FENCES_UNREADABLE:NO_ACCOUNTS_VIEW"
    assert lfh.arm_block(None) == "LIVE_FENCES_UNREADABLE:NO_ACCOUNTS_VIEW"
    v = {"accounts": [{"account": "acctA", "armed": True}, {"account": "acctB", "armed": True, "exchange_mirror": mirror(GOOD)}]}
    assert lfh.arm_block(v) == "LIVE_FENCES_UNREADABLE:acctA=NO_EXCHANGE_MIRROR"   # exchange_view would say NO_MIRROR_YET
    assert lfh.summary({}, [])["verdict"] == "AMBER"


def test_lifecycle_alert_raised_for_each_account():
    for name, bad_acct, cause, v in _cases():
        arming = {"sources": {"website_armed_accounts": list(ACCTS)}}
        al = lcl.alerts(arming=arming, intents=[], exchange=lcl.exchange_view(v), clock={}, now=0.0)
        hits = [a for a in al if a["code"] == "LIVE_FENCES_UNREADABLE"]
        assert [(a["account"], a["severity"], a["cause"]) for a in hits] == [(bad_acct, "RED", cause)], (name, al)


def test_exchange_health_route_and_arm_gate_are_wired():
    src = open("live_copy_monitor_api.py").read()
    i = src.index("def live_exchange_health")
    assert "_lfh.summary(" in src[i:i + 1500] and '"verdict": lf["verdict"]' in src[i:i + 1500]
    bot = open("bot.py").read()
    j = bot.index("def _live_guardian_arm_block")
    assert "return _live_fences_arm_block()" in bot[j:j + 900]
    k = bot.index("def api_live_copy_arm_gate")
    assert '"live_fences_block": _live_fences_arm_block()' in bot[k:k + 1200]


def test_account_without_credentials_is_unverified_and_blocked_never_ok():
    for err in ("credentials-missing", "NO_CREDENTIALS", "Exchange credentials unavailable (X)"):
        for bad in ACCTS:
            v = view(GOOD, GOOD)
            m = next(a for a in v["accounts"] if a["account"] == bad)["exchange_mirror"]
            m.update(ok=False, error=err)   # live_fences itself still readable from the DB
            assert lfh.live_fences_cause(m) == lfh.CAUSE_NO_CREDENTIALS
            assert lfh.arm_block(v) == f"LIVE_FENCES_UNREADABLE:{bad}=NO_CREDENTIALS_UNVERIFIED"
            s = lfh.summary(lcl.exchange_view(v), ACCTS)
            assert s["verdict"] == "RED" and s["accounts"][bad]["ok"] is False


def test_gate_passes_only_on_explicit_ok_true_never_on_missing_error():
    for bad in ACCTS:
        for lf in ({k: v for k, v in GOOD.items() if k != "ok"},   # no ok, no error -> still blocked
                   {**GOOD, "ok": "true"}, {**GOOD, "ok": 1}, {**GOOD, "ok": None}):
            v = view(GOOD, GOOD)
            next(a for a in v["accounts"] if a["account"] == bad)["exchange_mirror"]["live_fences"] = lf
            assert lfh.arm_block(v) == f"LIVE_FENCES_UNREADABLE:{bad}=LIVE_FENCES_UNREADABLE"
            assert lfh.summary(lcl.exchange_view(v), ACCTS)["verdict"] != "GREEN"
    assert lfh.arm_block(view(GOOD, GOOD)) is None
