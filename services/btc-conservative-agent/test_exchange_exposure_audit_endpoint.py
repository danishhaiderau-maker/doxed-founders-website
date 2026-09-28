from unittest import mock

import bot


def test_exchange_exposure_audit_requires_strict_admin_auth():
    with bot.app.test_request_context("/api/exchange_exposure_audit"), mock.patch.object(
        bot, "_admin_authed_strict", return_value=False
    ):
        response, status = bot.api_exchange_exposure_audit()

    assert status == 401
    assert response.get_json()["error"] == "admin token required"


def test_exchange_exposure_audit_is_read_only_and_sanitized():
    private_payload = {
        "open_orders": [{"id": "private-order"}],
        "positions": [],
    }
    audit = {
        "checked_ts": 123.0,
        "authoritative": True,
        "fresh": True,
        "flat": True,
        "orders_synced": True,
        "positions_synced": True,
        "trades_synced": True,
        "open_order_count": 0,
        "open_position_count": 0,
        "orphan_order_ids": [],
        "orphan_position_ids": [],
        "error": None,
        "_rebuild_payload": private_payload,
    }
    bot.state["live_armed"] = False
    bot.state["bitfinex_live_enabled"] = False

    with bot.app.test_request_context("/api/exchange_exposure_audit"), mock.patch.object(
        bot, "_admin_authed_strict", return_value=True
    ), mock.patch.object(
        bot, "_refresh_bitfinex_exposure_audit", return_value=dict(audit)
    ) as refresh:
        response, status = bot.api_exchange_exposure_audit()

    payload = response.get_json()
    assert status == 200
    assert payload["ok"] is True
    assert payload["authoritative"] is True
    assert payload["fresh"] is True
    assert payload["flat"] is True
    assert payload["mutating"] is False
    assert payload["live_armed"] is False
    assert payload["bitfinex_live_enabled"] is False
    assert "_rebuild_payload" not in payload
    assert "open_orders" not in payload
    assert "positions" not in payload
    refresh.assert_called_once_with()


def test_exchange_exposure_audit_fails_closed_when_private_read_is_unavailable():
    audit = {
        "checked_ts": 123.0,
        "authoritative": False,
        "fresh": False,
        "flat": False,
        "orders_synced": False,
        "positions_synced": False,
        "trades_synced": False,
        "open_order_count": None,
        "open_position_count": None,
        "orphan_order_ids": [],
        "orphan_position_ids": [],
        "error": "PRIVATE_API_UNAVAILABLE",
    }

    with bot.app.test_request_context("/api/exchange_exposure_audit"), mock.patch.object(
        bot, "_admin_authed_strict", return_value=True
    ), mock.patch.object(
        bot, "_refresh_bitfinex_exposure_audit", return_value=audit
    ):
        response, status = bot.api_exchange_exposure_audit()

    payload = response.get_json()
    assert status == 503
    assert payload["ok"] is False
    assert payload["flat"] is False
    assert payload["error"] == "PRIVATE_API_UNAVAILABLE"


def test_private_read_audit_remains_available_in_forced_paper_mode():
    """Paper-mode mutation gating must not suppress read-only flat proof."""

    def empty_private_read(fn, label="EXCHANGE", max_attempts=5):
        return []

    with (
        mock.patch.object(bot, "_private_api_keys_ok", return_value=True),
        mock.patch.object(bot, "_direct_private_exchange_owner", return_value=False),
        mock.patch.object(bot, "bitfinex_private", object()),
        mock.patch.object(
            bot, "_exchange_call_with_retry", side_effect=empty_private_read
        ) as private_read,
        mock.patch.object(
            bot,
            "_managed_exchange_identity_snapshot",
            return_value={"order_ids": set(), "position_ids": set(), "trade_ids": set(), "pending_candidates": []},
        ),
        mock.patch.object(bot, "_exchange_position_size", return_value=0.0),
    ):
        audit = bot._refresh_bitfinex_exposure_audit()

    assert audit["authoritative"] is True
    assert audit["fresh"] is True
    assert audit["flat"] is True
    assert audit["open_order_count"] == 0
    assert audit["open_position_count"] == 0
    assert audit["error"] is None
    assert private_read.call_count == 3
    assert all(call.kwargs["max_attempts"] == 4 for call in private_read.call_args_list)


def test_exchange_retry_recovers_from_transient_nonce_race():
    attempts = iter(
        [
            bot.ccxt.ExchangeError("bitfinex nonce: small"),
            bot.ccxt.ExchangeError("bitfinex nonce: small"),
            {"ok": True},
        ]
    )

    def request():
        result = next(attempts)
        if isinstance(result, Exception):
            raise result
        return result

    with mock.patch.object(bot.time, "sleep") as sleep:
        result = bot._exchange_call_with_retry(
            request,
            label="NONCE_RACE_TEST",
            max_attempts=4,
        )

    assert result == {"ok": True}
    assert sleep.call_count == 2


def test_exchange_retry_does_not_mask_non_transient_exchange_error():
    def request():
        raise bot.ccxt.ExchangeError("bitfinex insufficient balance")

    with mock.patch.object(bot.time, "sleep") as sleep:
        try:
            bot._exchange_call_with_retry(request, max_attempts=4)
        except bot.ccxt.ExchangeError as exc:
            assert "insufficient balance" in str(exc)
        else:  # pragma: no cover - protects the fail-closed contract.
            raise AssertionError("non-transient exchange error was swallowed")

    sleep.assert_not_called()
