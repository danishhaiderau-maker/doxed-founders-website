from pathlib import Path


ROOT = Path(__file__).resolve().parent
DASHBOARD_SOURCE = ROOT / "research" / "research_dashboard.py"


def test_report_explorer_loads_large_reports_only_after_selection():
    source = DASHBOARD_SOURCE.read_text(encoding="utf-8")

    assert "Reports are loaded on demand." in source
    assert "if (i === 0) li.click();" not in source
    assert "output.textContent = `Loading ${title}…`;" in source
    assert "if (!rr.ok) throw new Error(`HTTP ${rr.status}`);" in source

