import bot


def test_activity_tables_render_truthful_empty_state_rows():
    source = bot.DASHBOARD_JS

    expected = {
        "signalsTable": (14, "No active signals right now."),
        "ordersTable": (11, "No pending orders right now."),
        "positionsTable": (11, "No open paper positions right now."),
        "expiredOrdersTable": (8, "No expired orders in this session."),
        "tradesTable": (18, "No closed trades in this session."),
    }

    for table_id, (columns, message) in expected.items():
        assert f"'{table_id}'" in source
        assert (
            f'<tr><td colspan="{columns}" style="color:#8b949e;">'
            f"{message}</td></tr>"
        ) in source


def test_activity_empty_states_are_render_only_fallbacks():
    source = bot.DASHBOARD_JS

    assert "activeSignalRows || '<tr><td" in source
    assert "pendingOrderRows || '<tr><td" in source
    assert "positionRows || '<tr><td" in source
    assert "expiredOrderRows || '<tr><td" in source
    assert "closedTradeRows || '<tr><td" in source
