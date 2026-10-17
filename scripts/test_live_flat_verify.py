import live_flat_verify as v

OK = {"ok": True, "flat": True, "open_orders": [], "positions": [], "orphans": [], "read_age_s": 8}
EXPIRED = {"ok": False, "error": "credentials-missing", "read_age_s": 8}


def health(**row):
    base = {"account": "acct-b", "hire_active": False, "paused": True, "armed": False}
    base.update(row)
    return {"website": {"accounts": [{"account": "acct-a", "hire_active": True, "paused": True, "armed": False}, base]}}


def life(b):
    return {"exchange": {"acct-a": OK, "acct-b": b}}


def test_expired_paused_unarmed_credentials_missing_account_does_not_block():
    assert v.verdict(life(EXPIRED), health()) == (0, 0)


def test_active_hire_or_armed_or_unpaused_credentials_missing_account_still_blocks():
    assert v.verdict(life(EXPIRED), health(hire_active=True)) == (1, 0)
    assert v.verdict(life(EXPIRED), health(armed=True)) == (1, 0)
    assert v.verdict(life(EXPIRED), health(paused=False)) == (1, 0)
    assert v.verdict(life(EXPIRED), {"website": {"accounts": []}}) == (1, 0)  # not listed on the website: blocks


def test_other_errors_positions_orders_and_orphans_always_block():
    assert v.verdict(life({"ok": False, "error": "timeout"}), health()) == (1, 0)
    assert v.verdict(life(dict(EXPIRED, positions=[{"s": 1}])), health()) == (1, 0)
    assert v.verdict(life(dict(OK, positions=[{"s": 1}])), health()) == (1, 0)
    assert v.verdict({"exchange": {"acct-a": dict(OK, open_orders=[1])}}, health()) == (1, 0)
    assert v.verdict({"exchange": {"acct-a": dict(OK, orphans=[1])}}, health()) == (1, 0)


def test_stale_but_flat_is_counted_separately():
    assert v.verdict({"exchange": {"acct-a": dict(OK, read_age_s=300)}}, health()) == (0, 1)


def test_missing_exchange_map_raises_so_the_cli_prints_err(tmp_path, capsys):
    import json
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    a.write_text("{}"); b.write_text(json.dumps(health()))
    assert v.main(["x", str(a), str(b)]) == 0
    assert capsys.readouterr().out.strip() == "ERR ERR"


def test_missing_or_null_hire_active_paused_armed_each_block():
    for key in ("hire_active", "paused", "armed"):
        row_missing = {"account": "acct-b", "hire_active": False, "paused": True, "armed": False}
        del row_missing[key]
        h_missing = {"website": {"accounts": [row_missing]}}
        assert v.verdict(life(EXPIRED), h_missing) == (1, 0), key
        assert v.verdict(life(EXPIRED), health(**{key: None})) == (1, 0), key
