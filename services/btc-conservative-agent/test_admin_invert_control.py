from pathlib import Path

import bot


AUTH = {"X-Bot-Admin-Token": "invert-test-token"}


def test_invert_endpoint_is_authenticated_and_changes_new_ticket_policy(monkeypatch):
    monkeypatch.setattr(bot, "_BOT_ADMIN_TOKEN", "invert-test-token")
    monkeypatch.setattr(bot, "save_persistent_config", lambda: None)
    monkeypatch.setattr(bot, "_collector_v22_epoch_id", lambda: "epoch-invert-test")
    monkeypatch.setattr(bot, "_patch_api_state_cache_fields", lambda **_updates: None)
    monkeypatch.setattr(bot, "_DASHBOARD_BOOTSTRAP_COMPLETE", True)
    with bot.state_lock:
        monkeypatch.setitem(bot.state, "invert_signal", False)

    old_ticket = {}
    old_identity = bot.stamp_signal_policy_identity(
        old_ticket, raw_direction="LONG", executed_direction="LONG", invert_on=False,
    )

    client = bot.app.test_client()
    denied = client.post(
        "/api/toggle_invert_signal", environ_base={"REMOTE_ADDR": "198.51.100.9"},
    )
    assert denied.status_code == 401
    assert denied.get_json().get("error") or denied.get_json().get("status")

    allowed = client.post(
        "/api/toggle_invert_signal", headers=AUTH,
        environ_base={"REMOTE_ADDR": "198.51.100.9"},
    )
    body = allowed.get_json()
    assert allowed.status_code == 200
    assert body["invert_signal"] is True
    assert body["applies_to"] == "new_signals_only"
    assert body["existing_tickets_unchanged"] is True

    executed, inverted = bot.apply_invert_direction("LONG")
    new_ticket = {}
    new_identity = bot.stamp_signal_policy_identity(
        new_ticket, raw_direction="LONG", executed_direction=executed, invert_on=True,
    )
    assert inverted is True
    assert new_ticket["raw_direction"] == "LONG"
    assert new_ticket["executed_direction"] == "SHORT"
    assert new_identity["policy_signature"] != old_identity["policy_signature"]
    assert new_identity["policy_epoch_id"] != old_identity["policy_epoch_id"]

    # The toggle never rewrites a previously captured ticket.
    assert old_ticket["invert_on"] is False
    assert old_ticket["raw_direction"] == "LONG"
    assert old_ticket["executed_direction"] == "LONG"
    assert old_ticket["policy_identity"] == old_identity


def test_public_state_truthfully_shows_invert_but_cannot_control_it():
    public = bot._sanitize_public_state({"invert_signal": True, "positions": [], "trades": []})
    assert public["invert_signal"] is True
    assert public["invert_on"] is True
    assert public["operator_authed"] is False
    assert public["invert_control"] == "admin_login_required"


def test_invert_ui_is_admin_aware_and_default_remains_off():
    source = Path(bot.__file__).read_text(encoding="utf-8")
    assert '"invert_signal": False' in source
    assert "INVERT_SIGNAL_DEFAULT = True" not in source
    assert "invert_live_default_applied" not in source
    assert 'id="invertToggleBtn"' in source
    assert "Admin login required to toggle invert" in source
    assert "applyInvertUi" in source
    assert '"error": "unauthorized — admin token required"' in source
