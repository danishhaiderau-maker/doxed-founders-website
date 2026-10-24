"""RK PASS (24 Oct 19:36 AEDT): acct-cmqdk8n9 fence exclusion - one test per criterion flip + unreadable read."""
import copy
import pytest
import live_fences_health as lfh

ACCT = "acct-cmqdk8n9"


def _row():
    return {"account": ACCT, "armed": False, "armed_at": None, "status": "PAUSED", "paused": True,
            "hire_active": False,
            "exchange_mirror": {"ok": False, "error": "credentials-missing", "fence_exclusion": {
                "schema": lfh.EXCLUSION_SCHEMA, "read_ok": True, "excluded": True,
                "reason": "HIRE_EXPIRED+NO_CREDENTIALS+PAUSED+CANNOT_ARM",
                "criteria": {c: True for c in lfh.EXCLUSION_CRITERIA},
                "values": {"status": "PAUSED", "hire_expires_at": "2026-10-01T00:00:00.000Z"}}}}


def _web(row):
    return {"accounts": [row], "armed_accounts": []}


def _summary(web):
    import live_copy_lifecycle as lcl
    return lfh.summary(lfh.attach_row_ctx(web, lcl.exchange_view(web)), web["armed_accounts"])


def test_all_criteria_excluded_not_blocking_and_never_flat_or_green():
    web = _web(_row())
    assert lfh.arm_block(web) is None
    s = _summary(web)
    e = s["accounts"][ACCT]
    assert e["status"] == "EXCLUDED (unreadable)" and e["ok"] is False and e["flat"] is False
    assert s["verdict"] != "GREEN"
    assert any(g["code"] == lfh.EXCLUDED_ALERT for g in s["gaps"])


def _flip_relay(c):
    def f(r):
        r["exchange_mirror"]["fence_exclusion"]["criteria"][c] = False
        r["exchange_mirror"]["fence_exclusion"]["excluded"] = False
    return f


FLIPS = {
    "hire_expired": lambda r: r.update(hire_active=True),
    "no_credentials": lambda r: r["exchange_mirror"].update(ok=True, error=None, live_fences={"ok": True}),
    "paused": lambda r: r.update(paused=False, status="ACTIVE"),
    "cannot_arm": lambda r: r.update(armed=True, armed_at="2026-10-24T08:00:00Z"),
    "relay_hire_expired": _flip_relay("hire_expired"),
    "relay_no_credentials": _flip_relay("no_credentials"),
    "relay_paused": _flip_relay("paused"),
    "relay_cannot_arm": _flip_relay("cannot_arm"),
}


@pytest.mark.parametrize("name", sorted(FLIPS))
def test_each_criterion_flip_returns_unverified(name):
    r = copy.deepcopy(_row())
    FLIPS[name](r)
    assert lfh.exclusion(ACCT, r)["excluded"] is False
    s = _summary(_web(r))
    assert s["accounts"][ACCT].get("status") != "EXCLUDED (unreadable)"
    if name != "no_credentials":  # still no creds -> UNVERIFIED blocks arming
        assert lfh.arm_block(_web(r))


@pytest.mark.parametrize("bad", ["missing", "read_failed", "wrong_schema", "garbage"])
def test_unreadable_read_returns_unverified_and_blocks(bad):
    r = copy.deepcopy(_row())
    fe = r["exchange_mirror"]
    if bad == "missing":
        fe.pop("fence_exclusion")
    elif bad == "read_failed":
        fe["fence_exclusion"] = {"schema": lfh.EXCLUSION_SCHEMA, "read_ok": False, "excluded": False}
    elif bad == "wrong_schema":
        fe["fence_exclusion"]["schema"] = "x"
    else:
        fe["fence_exclusion"] = "garbage"
    assert lfh.exclusion(ACCT, r)["excluded"] is False
    assert lfh.arm_block(_web(r))
    assert _summary(_web(r))["accounts"][ACCT]["ok"] is False


def test_other_account_never_excluded():
    r = copy.deepcopy(_row())
    r["account"] = "acct-aaaaaaaa"
    assert lfh.exclusion(r["account"], r)["excluded"] is False
    assert lfh.arm_block(_web(r))
