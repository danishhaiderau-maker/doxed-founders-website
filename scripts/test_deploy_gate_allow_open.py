"""--allow-open: open paper trades never block a deploy; live/force_paper/status still do."""
import importlib.util, json, pathlib
spec = importlib.util.spec_from_file_location("g", pathlib.Path(__file__).with_name("deploy_gate_open_trades.py"))
g = importlib.util.module_from_spec(spec); spec.loader.exec_module(g)


def test_open_trades_are_info_only():
    r = g.apply_allow_open({"ok": False, "reasons": ["OPEN_TRADES:3", "COUNT_MISMATCH:monitor=3 trades_feed=2"]})
    assert r["ok"] and r["reasons"] == [] and len(r["info_open_trades_will_be_flattened"]) == 2


def test_live_armed_force_paper_and_status_failure_still_block():
    for bad in ("LIVE_NOT_OFF", "FORCE_PAPER_NOT_TRUE", "STATUS_READ_FAILED:URLError"):
        r = g.apply_allow_open({"ok": False, "reasons": ["OPEN_TRADES:1", bad]})
        assert not r["ok"] and r["reasons"] == [bad]


def test_strict_mode_unchanged():
    assert not g.evaluate(lambda p: (_ for _ in ()).throw(RuntimeError("x")))["ok"]



# ---- #806: live surfaces block even with --allow-open --------------------------------------------------------------
import copy  # noqa: E402
import io  # noqa: E402
import contextlib  # noqa: E402

SAFE_STATUS = {"live_armed": False, "force_paper_mode": True, "git_rev": "abc"}
SAFE_LC = {"live_copy_output": {"enabled": False}, "live_copy_source_enabled": False,
           "tiles": [{"lane": "FAMILY_GS01", "bitfinex_live_orders": False},
                     {"lane": "FAMILY_GS06", "bitfinex_live_orders": None}],
           "website": {"reachable": True, "armed_accounts": []}}
FLAT = {"mirror_ok": True, "read_age_s": 3.0, "fresh": True, "positions": [], "orphans": [], "open_orders": 0,
        "verdict": "FLAT_NO_STOPS"}


def _fetch(lc=None, accounts=("a1", "a2"), per_account=None, raise_on=(), open_rows=0):
    lc = copy.deepcopy(SAFE_LC if lc is None else lc)
    per_account = per_account or {}
    seen = []

    def fetch(path):
        seen.append(path)
        for frag in raise_on:
            if frag in path:
                raise TimeoutError(frag)
        if path == "/api/status":
            return SAFE_STATUS
        if path == "/api/state":
            return {"lane_position_counts": {"L": {"open": open_rows, "pending": 0}}}
        if path == "/api/live-copy/status":
            return lc
        if path == "/api/monitor/live/exchange-stops":
            return {**FLAT, "account": accounts[0] if accounts else None, "accounts_seen": list(accounts)}
        if path.startswith("/api/monitor/live/exchange-stops?account="):
            acct = path.split("=", 1)[1]
            return {**FLAT, "account": acct, "accounts_seen": list(accounts), **per_account.get(acct, {})}
        rows = [{"trade_id": f"o{i}", "status": "OPEN"} for i in range(open_rows)]
        return {"rows": rows, "next_cursor": None, "remaining_after_page": 0}
    fetch.seen = seen
    return fetch


def _gate(fetch):
    return g.apply_allow_open(g.evaluate(fetch))


def test_all_safe_passes_and_reads_every_account():
    f = _fetch()
    r = _gate(f)
    assert r["ok"] and r["reasons"] == [] and r["warnings"] == []
    assert "/api/monitor/live/exchange-stops?account=a1" in f.seen and "/api/monitor/live/exchange-stops?account=a2" in f.seen
    assert r["live_surfaces"]["live_provably_off"] is True


def test_all_safe_with_open_paper_trades_passes_allow_open_only():
    f = _fetch(open_rows=2)
    assert _gate(f)["ok"] and not g.evaluate(_fetch(open_rows=2))["ok"]


def _blocks(fetch, prefix):
    r = _gate(fetch)
    assert not r["ok"] and any(x.startswith(prefix) for x in r["reasons"]), (prefix, r["reasons"])
    assert not any(x.startswith(prefix) for x in r["info_open_trades_will_be_flattened"])
    return r


def test_each_unsafe_state_blocks_even_with_allow_open():
    on = copy.deepcopy(SAFE_LC); on["live_copy_output"]["enabled"] = True
    _blocks(_fetch(lc=on), "LIVE_COPY_OUTPUT_ON")
    lanes = copy.deepcopy(SAFE_LC); lanes["tiles"][0]["bitfinex_live_orders"] = True
    r = _blocks(_fetch(lc=lanes), "LIVE_ORDERS_ON:FAMILY_GS01")
    armed = copy.deepcopy(SAFE_LC); armed["website"]["armed_accounts"] = ["acct-x"]
    _blocks(_fetch(lc=armed), "WEBSITE_ARMED:acct-x")
    down = copy.deepcopy(SAFE_LC); down["website"]["reachable"] = False
    _blocks(_fetch(lc=down), "WEBSITE_ARM_READ_FAILED")
    _blocks(_fetch(per_account={"a2": {"positions": [{"symbol": "tBTCF0:USTF0", "amount": 0.001}],
                                       "verdict": "UNPROTECTED_POSITION"}}), "EXCHANGE_NOT_FLAT:a2:UNPROTECTED_POSITION")
    _blocks(_fetch(per_account={"a2": {"open_orders": 1}}), "EXCHANGE_NOT_FLAT:a2:OPEN_ORDERS=1")
    _blocks(_fetch(per_account={"a1": {"orphans": [{"id": 1}]}}), "EXCHANGE_NOT_FLAT:a1:ORPHANS=1")
    _blocks(_fetch(per_account={"a1": {"verdict": "FLAT_WITH_STOP_ORPHANS"}}), "EXCHANGE_NOT_FLAT:a1:FLAT_WITH_STOP_ORPHANS")
    _blocks(_fetch(raise_on=("/api/live-copy/status",)), "LIVE_COPY_STATUS_READ_FAILED")


def test_no_live_surface_reason_is_an_allow_open_prefix():
    for reason in ("LIVE_COPY_OUTPUT_ON", "LIVE_ORDERS_ON:x", "WEBSITE_ARMED:x", "WEBSITE_ARM_READ_FAILED",
                   "EXCHANGE_NOT_FLAT:x", "EXCHANGE_STALE:x", "EXCHANGE_STOPS_READ_FAILED:x", "EXCHANGE_MIRROR_NOT_OK:x",
                   "EXCHANGE_STOPS_MALFORMED:x", "LIVE_COPY_STATUS_MALFORMED:x", "LIVE_COPY_STATUS_READ_FAILED:x"):
        assert not reason.startswith(g.ALLOW_OPEN_INFO_PREFIXES), reason


def test_malformed_fields_block():
    for mutate, prefix in (
            (lambda d: d.pop("live_copy_output"), "LIVE_COPY_STATUS_MALFORMED:live_copy_output.enabled"),
            (lambda d: d["live_copy_output"].update(enabled="false"), "LIVE_COPY_STATUS_MALFORMED:live_copy_output"),
            (lambda d: d.update(tiles=None), "LIVE_COPY_STATUS_MALFORMED:tiles"),
            (lambda d: d["tiles"][0].update(bitfinex_live_orders="yes"), "LIVE_COPY_STATUS_MALFORMED:tiles."),
            (lambda d: d.update(website={"reachable": True}), "LIVE_COPY_STATUS_MALFORMED:website")):
        lc = copy.deepcopy(SAFE_LC); mutate(lc)
        _blocks(_fetch(lc=lc), prefix)
    _blocks(_fetch(per_account={"a1": {"open_orders": "0"}}), "EXCHANGE_STOPS_MALFORMED:a1")
    _blocks(_fetch(per_account={"a1": {"positions": None}}), "EXCHANGE_STOPS_MALFORMED:a1")
    _blocks(_fetch(per_account={"a1": {"read_age_s": None}}), "EXCHANGE_STOPS_MALFORMED:a1")
    _blocks(_fetch(per_account={"a1": {"account": "other"}}), "EXCHANGE_STOPS_MALFORMED:a1")


def test_stale_or_no_accounts_block_when_live_could_be_on():
    on = copy.deepcopy(SAFE_LC); on["tiles"][1]["bitfinex_live_orders"] = True
    _blocks(_fetch(lc=on, per_account={"a1": {"read_age_s": 25.0, "fresh": False}}), "EXCHANGE_STALE:a1")
    _blocks(_fetch(lc=on, per_account={"a1": {"read_age_s": 5.0, "fresh": False}}), "EXCHANGE_STALE:a1")
    _blocks(_fetch(lc=on, per_account={"a1": {"read_age_s": -1.0}}), "EXCHANGE_STALE:a1")
    _blocks(_fetch(lc=on, accounts=()), "EXCHANGE_NOT_FLAT:NO_ACCOUNTS")
    _blocks(_fetch(lc=on, raise_on=("exchange-stops",)), "EXCHANGE_STOPS_READ_FAILED")
    _blocks(_fetch(lc=on, per_account={"a2": {"mirror_ok": False, "verdict": "UNKNOWN_MIRROR_NOT_OK"}}),
            "EXCHANGE_MIRROR_NOT_OK:a2")
    # an unreadable live-copy status is never "provably off": the mirror outage blocks too
    r = _blocks(_fetch(raise_on=("/api/live-copy/status", "exchange-stops")), "EXCHANGE_STOPS_READ_FAILED")
    assert r["warnings"] == []


def test_zero_accounts_blocks_in_strict_and_allow_open_when_live_on():
    armed = copy.deepcopy(SAFE_LC); armed["website"]["armed_accounts"] = ["a1"]
    r = _blocks(_fetch(lc=armed, accounts=()), "EXCHANGE_NOT_FLAT:NO_ACCOUNTS")
    assert "WEBSITE_ARMED:a1" in r["reasons"]


def test_mirror_outage_while_everything_off_only_warns():
    src_on = copy.deepcopy(SAFE_LC); src_on["live_copy_source_enabled"] = True   # source on but output/tiles/arm off
    r = _gate(_fetch(lc=src_on, per_account={"a2": {"mirror_ok": False, "verdict": "UNKNOWN_MIRROR_NOT_OK"}}))
    assert r["ok"] and r["warnings"] == ["WARN_EXCHANGE_MIRROR_NOT_OK:a2:UNKNOWN_MIRROR_NOT_OK"]
    for kw in ({"raise_on": ("exchange-stops",)}, {"accounts": ()},
               {"per_account": {"a1": {"read_age_s": 99.0, "fresh": False}}},
               {"per_account": {"a2": {"mirror_ok": False, "verdict": "UNKNOWN_MIRROR_NOT_OK"}}}):
        r = _gate(_fetch(**kw))
        assert r["ok"] and r["reasons"] == [] and r["warnings"] and all(w.startswith("WARN_EXCHANGE") for w in r["warnings"]), kw
    # but a fresh read that SEES a position still blocks while off
    _blocks(_fetch(per_account={"a1": {"positions": [{"amount": 0.001}], "verdict": "PROTECTED"}}),
            "EXCHANGE_NOT_FLAT:a1:PROTECTED")


def test_strict_mode_also_blocks_on_live_surfaces():
    on = copy.deepcopy(SAFE_LC); on["live_copy_output"]["enabled"] = True
    r = g.evaluate(_fetch(lc=on))
    assert not r["ok"] and "LIVE_COPY_OUTPUT_ON" in r["reasons"]


def test_output_states_the_unobservable_gap_and_dry_run_never_prints_the_token(monkeypatch):
    secret = "tok-SHOULD-NEVER-PRINT-123"
    monkeypatch.setenv("BOT_ADMIN_TOKEN", secret)
    lc_on = copy.deepcopy(SAFE_LC); lc_on["live_copy_output"]["enabled"] = True
    monkeypatch.setattr(g, "_http_fetch", lambda base, token: _fetch(lc=lc_on))
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = g.main(["--allow-open", "--dry-run"])
    assert rc == 0                                            # dry-run never fails the shell
    body = json.loads(out.getvalue().strip().splitlines()[-1])
    assert body["dry_run"] is True and body["ok"] is False and "LIVE_COPY_OUTPUT_ON" in body["reasons"]
    assert "SUBSCRIBER_MAX_CONCURRENT_SIGNALS" in body["unobservable"] and "relay-paused read-back" in body["unobservable"]
    assert "SUBSCRIBER_MAX_CONCURRENT_SIGNALS" in err.getvalue()
    assert secret not in out.getvalue() and secret not in err.getvalue()
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        assert g.main(["--allow-open"]) == 1                  # same state without --dry-run blocks


def test_new_reasons_fail_fast_not_waited_out(monkeypatch):
    monkeypatch.setenv("BOT_ADMIN_TOKEN", "x")
    armed = copy.deepcopy(SAFE_LC); armed["website"]["armed_accounts"] = ["a1"]
    monkeypatch.setattr(g, "_http_fetch", lambda base, token: _fetch(lc=armed))
    monkeypatch.setattr(g.time, "sleep", lambda s: (_ for _ in ()).throw(AssertionError("must not wait")))
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        assert g.main(["--wait-seconds", "600"]) == 1
