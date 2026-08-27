import re

import bot


def test_activity_tables_keep_mobile_horizontal_scroll_contract():
    """All activity tables must remain usable without widening the mobile page."""

    source = bot.HTML
    activity = source.split('<h2 id="activityTables">', 1)[1].split(
        '<script src="/static/dashboard.js', 1
    )[0]

    table_bodies = re.findall(r'<tbody id="([^"]+)"></tbody>', activity)
    assert table_bodies == [
        "virtualChaseTable",
        "signalsTable",
        "positionsTable",
        "ordersTable",
        "expiredOrdersTable",
        "tradesTable",
        "aiHistoryTable",
    ]

    # The table itself is the bounded horizontal scroll container. This keeps
    # the desktop table unchanged while preserving touch/trackpad access to
    # every column on a narrow viewport without widening the whole page.
    assert "table { border-collapse:collapse; width:100%; max-width:100%;" in source
    assert "display:block; overflow-x:auto;" in source
    assert "overscroll-behavior-inline:contain;" in source
    assert "-webkit-overflow-scrolling:touch;" in source
