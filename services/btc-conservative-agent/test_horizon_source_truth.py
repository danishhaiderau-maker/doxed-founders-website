from research import research_dashboard as dashboard
from pathlib import Path
import shutil
import subprocess
import pytest


def setup(monkeypatch, report, current=True):
    monkeypatch.setattr(dashboard, '_declared_atomic_generation_report', lambda _: (report, {'manifest': {}}))
    monkeypatch.setattr(dashboard, '_generation_freshness_meta', lambda _: {'current': current})
    monkeypatch.setattr(dashboard, '_current_generation_identity', lambda: {})


def test_missing_report_cannot_become_empty_cohort(monkeypatch):
    setup(monkeypatch, None)
    result = dashboard._horizon_payload()
    assert result['source_available'] is False
    assert result['losing_trades'] is None
    assert result['horizons'] == []
    assert result['max_horizon_coverage_pct'] is None


def test_stale_report_cannot_become_current(monkeypatch):
    setup(monkeypatch, {'losing_trades': 0}, False)
    assert dashboard._horizon_payload()['source_available'] is False


def test_genuinely_empty_current_report_remains_distinct(monkeypatch):
    setup(monkeypatch, {'losing_trades': 0})
    result = dashboard._horizon_payload()
    assert result['source_available'] is True
    assert result['losing_trades'] == 0
    assert all(row['profitable'] == 0 for row in result['horizons'])


def test_missing_denominator_and_horizon_counts_are_unknown(monkeypatch):
    setup(monkeypatch, {})
    assert dashboard._horizon_payload()['source_available'] is False
    setup(monkeypatch, {'losing_trades': 2})
    assert all(row['profitable'] is None for row in dashboard._horizon_payload()['horizons'])


def test_actual_renderer_shows_unavailable_not_zero():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node unavailable')
    source = Path(dashboard.__file__).read_text(encoding='utf-8')
    function = source.split('async function loadHorizon() {', 1)[1].split('async function loadLeakage()', 1)[0]
    script = """
const elements = {};
global.document = {getElementById: id => elements[id] ||= {style:{}}};
global.fetch = async () => ({json:async()=>({source_available:false,coverage_reason:'HORIZON_REPORT_UNAVAILABLE'})});
""" + 'async function loadHorizon() {' + function + """
loadHorizon().then(()=>{
 if (!elements['horizon-note'].textContent.startsWith('Unavailable')) throw Error('missing unavailable');
 if (elements['horizon-note'].textContent.includes('Coverage 0')) throw Error('invented zero');
 if (!elements['horizon-body'].innerHTML.includes('Unavailable')) throw Error('invented counts');
});
"""
    subprocess.run([node, '-e', script], check=True, capture_output=True, text=True, timeout=15)
