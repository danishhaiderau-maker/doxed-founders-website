"""Exact-size contract for the delegated smallest-live-copy test."""

import ast
from pathlib import Path


BOT_PATH = Path(__file__).with_name("bot.py")


class _Logger:
    def error(self, *args, **kwargs):
        pass


class _Venue:
    def __init__(self, *, min_qty=0.0001, min_notional=5.0, precision=None):
        self.min_qty = min_qty
        self.min_notional = min_notional
        self.precision = precision

    def market(self, _symbol):
        return {
            "limits": {
                "amount": {"min": self.min_qty},
                "cost": {"min": self.min_notional},
            }
        }

    def amount_to_precision(self, _symbol, qty):
        return str(self.precision if self.precision is not None else qty)


def _calc(venue, price=80_000.0, leverage=100.0, margin=0.25):
    tree = ast.parse(BOT_PATH.read_text(encoding="utf-8"))
    fn = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "calc_position_qty"
    )
    scope = {
        "bitfinex_public": venue,
        "SYMBOL_CCXT": "BTCF0/USTF0:USTF0",
        "MAKER_FEE_PCT": 0.0,
        "TAKER_FEE_PCT": 0.0,
        "FIXED_MARGIN_USDT": 0.25,
        "DEFAULT_RESEARCH_LEVERAGE": 100.0,
        "logger": _Logger(),
    }
    exec(compile(ast.Module(body=[fn], type_ignores=[]), str(BOT_PATH), "exec"), scope)
    return scope["calc_position_qty"](price, leverage, margin)


def test_requested_25_notional_is_not_rounded_up():
    qty = _calc(_Venue())
    assert qty == 25.0 / 80_000.0
    assert qty * 80_000.0 == 25.0


def test_venue_minimum_notional_above_request_fails_closed():
    assert _calc(_Venue(min_notional=30.0)) == 0.0


def test_venue_minimum_quantity_above_request_fails_closed():
    assert _calc(_Venue(min_qty=0.001)) == 0.0


def test_precision_must_not_increase_requested_notional():
    assert _calc(_Venue(precision=0.0004)) == 0.0


def test_limit_and_market_paths_pass_signal_margin_and_block_zero_qty():
    source = BOT_PATH.read_text(encoding="utf-8")
    assert source.count('calc_position_qty(price, _state_leverage(), signal.get("margin_usdt"))') >= 2
    assert source.count('"VENUE_MINIMUM_EXCEEDS_REQUESTED_SIZE"') >= 6
