"""#849 RK ruling 24 Oct (PASS + 4 amendments): live-row funding read-back from the Bitfinex funding ledger."""
import json
import logging

import pytest

import live_funding_ledger as lfl
import live_vs_paper as lv
import r17_exclusion as rx
import tile_fill

S = 1792310400.0                       # a 00/08/16 UTC settlement instant
assert S % tile_fill.FUNDING_INTERVAL_S == 0
POS = (194481758, S - 600.0, S + 600.0)  # the Bitfinex position open across S
KEY, SECRET = "k" * 43, "s" * 43


def _row(lid, ts, amount, rate=0.0001, desc=None, currency="USTF0"):
    return [lid, currency, "margin", int(ts * 1000), None, amount, 250.0, None,
            desc or f"Funding Event BTCF0:USTF0 ({rate:.8f}) on wallet margin"]


def test_no_settlement_in_window_is_a_countable_zero_without_any_entry():
    out = lfl.verify(S + 10, S + 3600, ledger_rows=[], read_at_ts=S + 4000, paper_funding_usd=0.0)
    assert out["funding_status"] == "NO_CROSSING" and out["funding_verified"] is True
    assert out["funding_usd_raw"] == 0.0 and out["funding_ts_crossed"] == [] and out["funding_ts_fallbacks"] == []
    assert out["funding_model"] == "LIVE_LEDGER_V1" and out["funding_readback_at_ts"] == S + 4000
    assert rx.funding_failure(out) is None
    # the proof is the paper pricer's own schedule (bot.py:32778 -> tile_fill.settlements_crossed)
    assert tile_fill.settlements_crossed(S + 10, S + 3600) == []


def test_settlement_with_its_entry_uses_the_ledger_value_and_records_the_gap():
    rows = [_row(10500585082, S + 4, -0.025, rate=0.0001)]           # paid 0.025 USD funding (ledger amount < 0)
    out = lfl.verify(S - 60, S + 60, ledger_rows=rows, read_at_ts=S + 120, position=POS, paper_funding_usd=0.02,
                     live_notional_usd=250.0)
    assert out["funding_status"] == "PRICED" and out["funding_verified"] is True
    assert out["funding_usd_raw"] == 0.025                            # cost-positive, the paper convention
    assert out["funding_ledger_ids"] == [10500585082] and out["funding_ledger_mts"] == [S + 4]
    assert out["funding_usd_by_settlement"][0]["settlement_ts"] == S
    assert out["funding_live_minus_paper_usd"] == pytest.approx(0.005)
    assert out["funding_position_id"] == 194481758 and rx.funding_failure(out) is None


def test_settlement_without_an_entry_is_unverified():
    out = lfl.verify(S - 60, S + 60, ledger_rows=[], read_at_ts=S + 120, position=POS)
    assert out["funding_status"] == "UNVERIFIED_SETTLEMENT_NO_ENTRY" and out["funding_verified"] is False
    assert out["funding_settlement_status"] == ["NO_ENTRY"] and rx.funding_failure(out) == "FUNDING_UNVERIFIED"


def test_untied_entries_are_unverified():
    rows = [_row(1, S + 4, -0.025)]
    out = lfl.verify(S - 60, S + 60, ledger_rows=rows, position=None)                 # no Bitfinex position
    assert out["funding_status"] == "UNVERIFIED_POSITION_UNRESOLVED"
    out = lfl.verify(S - 60, S + 60, ledger_rows=rows, position=(9, S + 1, S + 60))   # position opened after S
    assert out["funding_status"] == "UNVERIFIED_ENTRY_UNTIED"
    out = lfl.verify(S - 60, S + 60, ledger_rows=[_row(1, S + 4, -0.05, rate=0.0001)], position=POS,
                     live_notional_usd=250.0)                                         # charged on 500 USD: merged lot
    assert out["funding_status"] == "UNVERIFIED_ENTRY_UNTIED"
    out = lfl.verify(S - 60, S + 60, ledger_rows=[_row(1, S + 4, -0.02), _row(2, S + 5, -0.02)], position=POS)
    assert out["funding_status"] == "UNVERIFIED_AMBIGUOUS_ENTRY"
    out = lfl.verify(S + 10, S + 3600, ledger_rows=[_row(3, S + 1800, -0.02)])        # off-schedule entry in the hold
    assert out["funding_status"] == "UNVERIFIED_ENTRY_UNTIED" and out["funding_verified"] is False


def test_entries_of_other_settlements_or_symbols_are_ignored():
    rows = [_row(1, S + 4, -0.02), _row(2, S + 8 * 3600 + 4, -0.02, desc="Funding Event ETHF0:USTF0 (0.0001) on wallet margin")]
    out = lfl.verify(S + 10, S + 3600, ledger_rows=rows)   # S is before the real entry fill: another position's
    assert out["funding_status"] == "NO_CROSSING" and out["funding_usd_raw"] == 0.0


def test_read_failure_is_unverified():
    out = lfl.verify(S - 60, S + 60, read_error="HTTP_500", read_at_ts=S + 120)
    assert out["funding_status"] == "UNVERIFIED_LEDGER_READ_FAILED" and out["funding_readback_error"] == "HTTP_500"
    assert rx.funding_failure(out) == "FUNDING_UNVERIFIED"
    out = lfl.verify(S + 10, S + 60, read_error="READ_KEY_MISSING")   # even with no crossing: a failed read is unproven
    assert out["funding_verified"] is False


def test_fee_entry_is_flagged_never_applied():
    rows = [_row(1, S + 4, -0.025, rate=0.0001), _row(77, S + 30, -0.5, desc="Trading fees for 0.001 BTCF0 (BTCF0:USTF0)")]
    out = lfl.verify(S - 60, S + 60, ledger_rows=rows, position=POS, live_notional_usd=250.0)
    assert out["funding_fee_anomaly"] is True and out["funding_fee_anomaly_ledger_ids"] == [77]
    assert out["funding_usd_raw"] == 0.025 and out["funding_ledger_ids"] == [1]       # the fee is not in the funding
    assert out["funding_status"] == "PRICED" and rx.funding_failure(out) is None      # a flag for HM, not a gate


def test_paper_mismatch_does_not_exclude_the_live_row():
    rows = [_row(1, S + 4, -0.9, rate=0.0036)]
    fund = lfl.verify(S - 60, S + 60, ledger_rows=rows, position=POS, paper_funding_usd=0.01, live_notional_usd=250.0)
    assert fund["funding_live_minus_paper_usd"] == pytest.approx(0.89) and fund["funding_verified"] is True
    chain = {"correlation_id": "c1", "fill_price": 100.0, "fill_at_ts": S - 60, "exit_price": 100.5, "closed": True}
    assert lv.live_funding_failure(chain, fund) is None
    r = lv.pair_row("c1", chain, {"side": "LONG", "entry_price": 100.0, "exit_price": 100.4, "funding_usd_raw": 0.01},
                    live_funding=fund)
    assert r["funding_usd_live"] == 0.9 and r["funding_usd_paper"] == 0.01
    assert r["funding_live_minus_paper_usd"] == pytest.approx(0.89)
    assert not any("FUNDING" in x for x in r["non_countable_reasons"] or [])


def test_unverified_live_funding_makes_only_the_live_leg_non_countable():
    chain = {"correlation_id": "c1", "fill_price": 100.0, "fill_at_ts": S - 60, "exit_price": 100.5, "closed": True}
    twin = {"side": "LONG", "entry_price": 100.0, "exit_price": 100.4}
    bad = lfl.verify(S - 60, S + 60, ledger_rows=[], position=POS)
    r = lv.pair_row("c1", chain, twin, live_funding=bad)
    assert r["live_non_countable"] is True and r["paper_twin_non_countable"] is False
    assert "LIVE_FUNDING_UNVERIFIED:UNVERIFIED_SETTLEMENT_NO_ENTRY" in r["non_countable_reasons"]
    assert r["funding_usd_live"] is None
    r = lv.pair_row("c1", chain, twin)                          # no read-back at all fails closed
    assert "LIVE_FUNDING_UNVERIFIED:NO_READBACK" in r["non_countable_reasons"]
    assert lv.live_funding_failure(dict(chain, closed=False), None) is None   # open row: not applicable yet


def test_non_fill_exit_stamp_next_to_a_settlement_is_a_ts_fallback():
    out = lfl.verify(S - 600, S + 30, ledger_rows=[_row(1, S + 4, -0.02)], position=POS, exit_ts_source="exit_at_ts")
    assert out["funding_ts_fallbacks"] == ["exit_ts:exit_at_ts"] and rx.funding_failure(out) == "FUNDING_TS_FALLBACK"


def test_position_match_is_unique_or_none():
    r = ["tBTCF0:USTF0", "CLOSED", 0, 100.0, 0, 0, None, None, None, None, None, 5, int((S - 60) * 1000), int((S + 60) * 1000)]
    assert lfl.match_position([r], S - 60, S + 60) == (5, S - 60, S + 60)
    assert lfl.match_position([r, list(r[:11]) + [6] + r[12:]], S - 60, S + 60) is None
    assert lfl.match_position([r], S - 600, S + 60) is None


def test_reader_never_uses_the_trading_key_and_never_logs_secrets(caplog):
    assert lfl.read_credentials({"BITFINEX_API_KEY": KEY, "BITFINEX_API_SECRET": SECRET}) is None
    assert lfl.read_credentials({"BFX_READ_API_KEY": KEY, "BFX_READ_API_SECRET": SECRET})[2] == "BFX_READ_API_KEY"
    sent = []

    def post(path, body, creds):
        sent.append((path, body))
        if path == lfl.LEDGER_PATH:
            return [_row(1, S + 4, -0.025)]
        r = ["tBTCF0:USTF0", "CLOSED", 0, 100.0, 0, 0, None, None, None, None, None, 5, int((S - 60) * 1000), int((S + 60) * 1000)]
        return [r]

    caplog.set_level(logging.DEBUG)
    rd = lfl.Reader(creds_fn=lambda: (KEY, SECRET, "BFX_READ_API_KEY"), post=post, clock=lambda: S + 500)
    chain = {"correlation_id": "c1", "fill_price": 100.0, "fill_qty": 2.5, "fill_at_ts": S - 60,
             "exit_fill_record_at_ts": S + 60, "closed": True}
    out = rd.begin()(chain, 0.02)
    assert out["funding_status"] == "PRICED" and out["funding_readback_at_ts"] == S + 500
    assert sent[0][1]["category"] == 29 and [p for p, _ in sent] == [lfl.LEDGER_PATH, lfl.POSITIONS_PATH]
    rd(chain, 0.02)
    assert len(sent) == 2                                       # verified read-back is cached
    blob = json.dumps(out) + caplog.text
    assert KEY not in blob and SECRET not in blob

    def boom(path, body, creds):
        raise lfl.ReadError("HTTP_401")
    out = lfl.Reader(creds_fn=lambda: (KEY, SECRET, "x"), post=boom).begin()(chain, None)
    assert out["funding_status"] == "UNVERIFIED_LEDGER_READ_FAILED" and out["funding_readback_error"] == "HTTP_401"
    out = lfl.Reader(creds_fn=lambda: None).begin()(chain, None)
    assert out["funding_readback_error"] == "READ_KEY_MISSING"
    assert KEY not in json.dumps(out) + caplog.text and SECRET not in caplog.text


def test_real_post_redacts_transport_errors(monkeypatch):
    import urllib.request

    def fail(req, timeout=0):
        raise OSError(f"connection reset {KEY} {SECRET}")
    monkeypatch.setattr(urllib.request, "urlopen", fail)
    with pytest.raises(lfl.ReadError) as ei:
        lfl._post(lfl.LEDGER_PATH, {"category": 29}, (KEY, SECRET, "BFX_READ_API_KEY"))
    assert str(ei.value) == "TRANSPORT_OSError" and KEY not in repr(ei.value) and SECRET not in repr(ei.value)


def test_build_wires_the_readback_and_a_raising_reader_fails_closed():
    chain = {"correlation_id": "c1", "fill_price": 100.0, "fill_at_ts": S + 10, "exit_fill_record_at_ts": S + 60, "closed": True}
    twin = {"side": "LONG", "entry_price": 100.0, "exit_price": 100.4, "funding_usd_raw": 0.0}
    seen = []
    ok = lv.build([chain], {"c1": twin}, funding_readback_for=lambda ch, paper: (
        seen.append(paper), lfl.verify(S + 10, S + 60, ledger_rows=[], paper_funding_usd=paper))[1])
    assert seen == [0.0] and ok["rows"][0]["funding_usd_live"] == 0.0
    assert not any("FUNDING" in x for x in ok["rows"][0]["non_countable_reasons"] or [])
    bad = lv.build([chain], {"c1": twin}, funding_readback_for=lambda ch, paper: 1 / 0)
    assert "LIVE_FUNDING_UNVERIFIED:NO_READBACK" in bad["rows"][0]["non_countable_reasons"]
