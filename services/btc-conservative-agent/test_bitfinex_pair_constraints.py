from decimal import Decimal

import pytest

from research import bitfinex_pair_constraints as bpc
from research.quantity_execution import (
    apply_quantity_constraints,
    validate_signed_quantity_constraints,
)
from research.venue_quantity_constraints import capture_public_pair_constraints

# Shape of GET /v2/conf/pub:info:pair:futures (2026-10-03 capture).
FIXTURE = [[
    ["BTCF0:USTF0", [1562164542332, None, None, "0.00004", "100.0", None, None, None, 0.01, 0.005]],
    ["ETHF0:BTCF0", [1608633542930, None, None, "0.0008", "100.0", None, None, None, 0.01, 0.005]],
]]
SYMBOL = "tBTCF0:USTF0"


@pytest.fixture(autouse=True)
def _fresh_cache():
    bpc.clear_cache()
    yield
    bpc.clear_cache()


class CountingFetch:
    def __init__(self, payload=FIXTURE):
        self.payload = payload
        self.urls = []

    def __call__(self, url):
        self.urls.append(url)
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


def test_parse_published_min_and_max_amount():
    amounts, reasons = bpc.parse_pair_info(FIXTURE, "BTCF0:USTF0")
    assert reasons == []
    assert amounts == {"min_amount": "0.00004", "max_amount": "100.0"}


def test_fetch_builds_constraints_from_public_endpoint_only():
    fetch = CountingFetch()
    got = bpc.fetch_pair_constraints(SYMBOL, now=1_000.0, fetch=fetch)
    assert got["supported"] is True
    assert fetch.urls == [bpc.PUBLIC_PAIR_INFO_URL]
    assert bpc.PUBLIC_PAIR_INFO_URL.startswith("https://api-pub.bitfinex.com/")
    c = got["constraints"]
    assert (c["min_amount"], c["max_amount"]) == ("0.00004", "100.0")
    assert c["amount_precision"] == 8 and c["price_sig_digits"] == 5
    assert c["min_notional"] is None and c["min_notional_status"] == "NOT_PUBLISHED_BY_VENUE"
    assert c["source"] == bpc.SOURCE and c["fetched_at"] == "1970-01-01T00:16:40Z"


def test_default_fetch_sends_no_credentials(monkeypatch):
    seen = {}

    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b"[[]]"

    def fake_urlopen(request, timeout):
        seen["headers"] = {k.lower() for k in request.headers}
        seen["method"] = request.get_method()
        return Resp()

    monkeypatch.setattr(bpc.urllib.request, "urlopen", fake_urlopen)
    bpc._default_fetch(bpc.PUBLIC_PAIR_INFO_URL)
    assert seen["method"] == "GET"
    assert "user-agent" in seen["headers"]
    assert not {"authorization", "bfx-apikey", "bfx-signature", "bfx-nonce"} & seen["headers"]


def test_cache_hits_within_ttl_and_refetches_after():
    fetch = CountingFetch()
    assert bpc.fetch_pair_constraints(SYMBOL, now=0.0, fetch=fetch, ttl_sec=60)["cache"] == "MISS"
    assert bpc.fetch_pair_constraints(SYMBOL, now=59.0, fetch=fetch, ttl_sec=60)["cache"] == "HIT"
    assert len(fetch.urls) == 1
    assert bpc.fetch_pair_constraints(SYMBOL, now=60.0, fetch=fetch, ttl_sec=60)["cache"] == "MISS"
    assert len(fetch.urls) == 2


@pytest.mark.parametrize("payload,reason", [
    (OSError("down"), "VENUE_PAIR_INFO_UNAVAILABLE:OSError"),
    ({"error": "x"}, "PAIR_INFO_PAYLOAD_INVALID"),
    ([[["ETHF0:BTCF0", FIXTURE[0][1][1]]]], "PAIR_NOT_PUBLISHED"),
    ([[["BTCF0:USTF0", [0, None, None, None, "100.0"]]]], "VENUE_MIN_AMOUNT_UNAVAILABLE"),
])
def test_unavailable_or_malformed_fails_closed(payload, reason):
    got = capture_public_pair_constraints(
        evidence_symbol=SYMBOL, captured_at="2026-10-03T00:00:00Z",
        source_revision="a" * 40, requested_qty="0.0003", fetch=CountingFetch(payload), now=0.0,
    )
    assert got["supported"] is False and got["receipt"] is None
    assert got["reasons"] == [reason]


def test_failed_fetch_is_not_cached():
    bpc.fetch_pair_constraints(SYMBOL, now=0.0, fetch=CountingFetch(OSError("down")))
    ok = bpc.fetch_pair_constraints(SYMBOL, now=1.0, fetch=CountingFetch())
    assert ok["supported"] is True and ok["cache"] == "MISS"


def test_below_min_fails_closed_with_clear_reason_and_no_round_up():
    c = bpc.fetch_pair_constraints(SYMBOL, now=0.0, fetch=CountingFetch())["constraints"]
    got = bpc.check_intent_quantity("0.0000399", c)
    assert got["accepted"] is False
    assert got["reasons"] == ["INTENT_QUANTITY_BELOW_VENUE_MIN_AMOUNT:0.00003990<0.00004"]
    assert Decimal(got["executable_quantity"]) < Decimal(c["min_amount"])


def test_precision_only_rounds_down():
    c = bpc.fetch_pair_constraints(SYMBOL, now=0.0, fetch=CountingFetch())["constraints"]
    got = bpc.check_intent_quantity("0.000293259999", c)
    assert got["accepted"] is True and got["rounded_down"] is True
    assert got["executable_quantity"] == "0.00029325"
    assert Decimal(got["executable_quantity"]) <= Decimal("0.000293259999")
    # A quantity that rounds down below the minimum is rejected, never lifted to it.
    edge = bpc.check_intent_quantity("0.000039999999", c)
    assert edge["accepted"] is False and edge["executable_quantity"] == "0.00003999"


def test_above_max_and_missing_constraints_fail_closed():
    c = bpc.fetch_pair_constraints(SYMBOL, now=0.0, fetch=CountingFetch())["constraints"]
    assert bpc.check_intent_quantity("100.1", c)["reasons"][0].startswith(
        "INTENT_QUANTITY_ABOVE_VENUE_MAX_AMOUNT")
    assert bpc.check_intent_quantity("0.0003", None)["reasons"] == [
        "VENUE_QUANTITY_CONSTRAINTS_UNAVAILABLE"]


def test_current_paper_size_is_admitted_with_signed_v2_receipt():
    price = 85252.0
    qty = 0.25 * 100 / price
    got = capture_public_pair_constraints(
        evidence_symbol=SYMBOL, captured_at="2026-10-03T00:00:00Z",
        source_revision="a" * 40, requested_qty=qty, fetch=CountingFetch(), now=0.0,
    )
    assert got["supported"] is True and got["reasons"] == []
    receipt, defects = validate_signed_quantity_constraints(got["receipt"], symbol=SYMBOL)
    assert defects == []
    assert receipt["min_lot"] == "0.00004" and receipt["max_lot"] == "100.0"
    assert receipt["min_notional"] is None
    decision = apply_quantity_constraints(
        requested_qty=qty, raw_partial_qty=qty, execution_price=price,
        constraints=got["receipt"], symbol=SYMBOL,
    )
    assert decision["accepted"] is True
    assert decision["minimum_notional_decision"] == "NOT_PUBLISHED_BY_VENUE"
    assert decision["rounded_executable_quantity"] <= qty


def test_below_min_intent_still_persists_receipt_but_is_unsupported():
    got = capture_public_pair_constraints(
        evidence_symbol=SYMBOL, captured_at="2026-10-03T00:00:00Z",
        source_revision="a" * 40, requested_qty="0.00001", fetch=CountingFetch(), now=0.0,
    )
    assert got["supported"] is False
    assert got["receipt"] is not None
    assert got["reasons"][0].startswith("INTENT_QUANTITY_BELOW_VENUE_MIN_AMOUNT")


def test_v2_receipt_tamper_and_max_lot_are_enforced():
    got = capture_public_pair_constraints(
        evidence_symbol=SYMBOL, captured_at="2026-10-03T00:00:00Z",
        source_revision="a" * 40, fetch=CountingFetch(), now=0.0,
    )
    tampered = dict(got["receipt"], min_lot="0.000001")
    _, defects = validate_signed_quantity_constraints(tampered, symbol=SYMBOL)
    assert "QUANTITY_CONSTRAINT_SIGNATURE_INVALID" in defects
    over = apply_quantity_constraints(
        requested_qty=150, raw_partial_qty=150, execution_price=85000,
        constraints=got["receipt"], symbol=SYMBOL,
    )
    assert over["accepted"] is False and over["reasons"] == ["REQUESTED_QTY_ABOVE_MAX_LOT"]