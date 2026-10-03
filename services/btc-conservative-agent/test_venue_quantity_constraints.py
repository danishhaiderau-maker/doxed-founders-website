from research.quantity_execution import validate_signed_quantity_constraints
from research.venue_quantity_constraints import capture_quantity_constraints


class FakeExchange:
    def __init__(self, market):
        self._market = market

    def market(self, symbol):
        assert symbol == "BTC/USDT:USDT"
        return self._market


def capture(market, *, revision="a" * 40):
    return capture_quantity_constraints(
        FakeExchange(market),
        ccxt_symbol="BTC/USDT:USDT",
        evidence_symbol="tBTCF0:USTF0",
        captured_at="2026-08-30T03:00:00+00:00",
        source_revision=revision,
    )


def test_exact_bitfinex_metadata_builds_integrity_bound_receipt_without_defaults():
    got = capture({
        "id": "BTCF0:USTF0",
        "precision": {"amount": 8},
        "limits": {"amount": {"min": "0.0001"}, "cost": {"min": "5"}},
    })
    assert got["supported"] is True
    receipt, reasons = validate_signed_quantity_constraints(
        got["receipt"], symbol="tBTCF0:USTF0"
    )
    assert reasons == []
    assert receipt["quantity_step"] == "1E-8"
    assert receipt["min_lot"] == "0.0001"
    assert receipt["min_notional"] == "5"
    assert receipt["source_revision"] == "a" * 40


def test_missing_minimum_or_ambiguous_step_is_unsupported_not_defaulted():
    missing = capture({
        "id": "BTCF0:USTF0", "precision": {"amount": 8},
        "limits": {"amount": {"min": None}, "cost": {"min": None}},
    })
    assert missing["supported"] is False
    assert set(missing["reasons"]) == {
        "VENUE_MIN_LOT_UNAVAILABLE", "VENUE_MIN_NOTIONAL_UNAVAILABLE",
    }

    ambiguous = capture({
        "id": "BTCF0:USTF0", "precision": {"amount": "0.0005"},
        "limits": {"amount": {"min": "0.001"}, "cost": {"min": "5"}},
    })
    assert ambiguous["supported"] is False
    assert "VENUE_QUANTITY_PRECISION_OR_STEP_UNAVAILABLE" in ambiguous["reasons"]


def test_missing_revision_fails_closed():
    got = capture({
        "id": "BTCF0:USTF0", "precision": {"amount": 8},
        "limits": {"amount": {"min": "0.0001"}, "cost": {"min": "5"}},
    }, revision="")
    assert got == {
        "supported": False,
        "receipt": None,
        "reasons": ["SOURCE_REVISION_UNAVAILABLE"],
    }


def test_unloaded_markets_are_loaded_once_and_unpublished_min_notional_stays_unsupported():
    class _Exchange:
        def __init__(self):
            self.loaded = False
            self.load_calls = 0

        def load_markets(self):
            self.load_calls += 1
            self.loaded = True

        def market(self, symbol):
            if not self.loaded:
                raise RuntimeError("bitfinex markets not loaded")
            return {"id": "tBTCF0:USTF0", "precision": {"amount": 8, "price": 5},
                    "limits": {"amount": {"min": 4e-05, "max": 100.0}, "cost": {"min": None, "max": None}}}

    ex = _Exchange()
    out = capture_quantity_constraints(ex, ccxt_symbol="BTC/USDT:USDT", evidence_symbol="tBTCF0:USTF0",
                                       captured_at="2026-10-03T00:00:00Z", source_revision="rev")
    assert ex.load_calls == 1
    assert out["supported"] is False and out["receipt"] is None
    assert "VENUE_MARKET_METADATA_UNAVAILABLE" not in out["reasons"]
    assert out["reasons"] == ["VENUE_MIN_NOTIONAL_UNAVAILABLE"]
