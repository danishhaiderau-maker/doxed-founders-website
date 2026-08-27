import re

from research import research_dashboard as dashboard


def test_every_analyzer_table_is_targeted_by_accessible_scroll_wrapper():
    source = dashboard.DASHBOARD_HTML
    table_count = len(re.findall(r"<table(?:\s|>)", source))

    assert table_count >= 40
    assert "root.querySelectorAll('main table').forEach(table =>" in source
    assert "table.parentElement.classList.contains('table-scroll')" in source
    assert "wrapper.className = 'table-scroll'" in source
    assert "wrapper.setAttribute('role', 'region')" in source
    assert "wrapper.setAttribute('tabindex', '0')" in source
    assert "scroll horizontally for more columns" in source
    assert "ensureScrollableTables();" in source


def test_narrow_viewport_scrolls_each_table_without_widening_page():
    source = dashboard.DASHBOARD_HTML

    assert '<meta name="viewport" content="width=device-width, initial-scale=1"/>' in source
    assert "html, body { width: 100%; max-width: 100%; overflow-x: hidden; }" in source
    assert "main { padding: 20px 24px; width: 100%; max-width: 1200px; min-width: 0; overflow: hidden; }" in source
    assert ".table-scroll { width: 100%; max-width: 100%; min-width: 0; overflow-x: auto;" in source
    assert "overscroll-behavior-inline: contain;" in source
    assert "-webkit-overflow-scrolling: touch;" in source
    assert ".table-scroll table { display: table; width: max-content; min-width: 100%; max-width: none;" in source


def test_desktop_table_semantics_and_data_hooks_are_unchanged():
    source = dashboard.DASHBOARD_HTML

    # Wrapping moves the existing table node; it does not clone, rewrite, or
    # replace table markup, tbody IDs, or the report-population functions.
    assert "table.parentNode.insertBefore(wrapper, table);" in source
    assert "wrapper.appendChild(table);" in source
    assert "cloneNode" not in source
    assert "outerHTML" not in source
    assert 'id="policy-grid-body"' in source
    assert 'id="diagnostic-policy-grid-body"' in source
    assert 'id="chase-policy-body"' in source
    assert 'id="missed-proof-body"' in source


def test_report_explorer_uses_keyboard_accessible_native_buttons():
    source = dashboard.DASHBOARD_HTML

    assert "const button = document.createElement('button');" in source
    assert "button.type = 'button';" in source
    assert "button.textContent = title;" in source
    assert "button.onclick = async () =>" in source
    assert "li.appendChild(button);" in source
    assert "list.querySelectorAll('button').forEach" in source
    assert "button.setAttribute('aria-current', 'true');" in source
    assert ".explorer-list button:focus-visible" in source
    assert "li.onclick = async () =>" not in source


def test_archive_download_links_remain_inside_generic_mobile_table_scrollers():
    source = dashboard.DASHBOARD_HTML

    assert 'id="archive-body"' in source
    assert 'id="past-analysis-body"' in source
    assert 'href="/download/archive/${encodeURIComponent(sid)}"' in source
    assert 'href="/download/past-analysis/${encodeURIComponent(id)}"' in source
    assert "root.querySelectorAll('main table')" in source
