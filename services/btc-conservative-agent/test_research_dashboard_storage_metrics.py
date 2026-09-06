"""Execute Overview cards: payload size is not whole-filesystem occupancy."""
import json
import shutil
import subprocess

from research import research_dashboard as dashboard


def cards(storage, performance=None):
    page = dashboard.DASHBOARD_HTML
    start = page.index('  const kpis = [', page.index('async function loadSummary()'))
    end = page.index("  document.getElementById('kpis')", start)
    script = ('const storage=' + json.dumps(storage) + '; const p=' + json.dumps(performance or {})
              + '; const hist={},histPerf={},retention={},re={},d={};'
              + 'const fmtExecutionUsd=x=>x,fmtPct=x=>x,fmtMelb=x=>x;'
              + page[start:end] + 'console.log(JSON.stringify(Object.fromEntries(kpis)));')
    result = subprocess.run([shutil.which('node'), '-e', script], capture_output=True,
                            text=True, encoding='utf-8', timeout=15)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_storage_cards_do_not_conflate_denominators():
    result = cards({'local_size_mb': 2488.3, 'local_limit_mb': 25600,
                    'local_limit_pct': 9.72, 'fly_size_mb': 1946,
                    'fly_volume_total_mb': 3997, 'fly_volume_pct': 64.2})
    assert '25.00 GiB cap' in result['Local Fly mirror cache']
    assert '9.72%' in result['Local Fly mirror cache']
    assert '1946.0 MiB' in result['Fly transferable runtime payload']
    assert '64.2' not in result['Fly transferable runtime payload']
    assert result['Fly whole-volume occupancy'] == '64.2% used · 3997.0 MiB capacity'


def test_missing_storage_is_not_zero_or_default_capacity():
    result = cards({})
    assert result['Fly whole-volume occupancy'] == 'UNAVAILABLE · capacity unavailable'
    assert result['Fresh executed'] == 'UNAVAILABLE'
    partial = cards({'local_size_mb': 1})
    assert 'cap unavailable' in partial['Local Fly mirror cache']
    assert 'usage unavailable' in partial['Local Fly mirror cache']


def test_explicit_zero_and_configured_cap_are_preserved():
    result = cards({'local_size_mb': 0, 'local_limit_mb': 30720, 'local_limit_pct': 0,
                    'fly_size_mb': 0, 'fly_volume_pct': 0, 'fly_volume_total_mb': 3997}, {'trades': 0})
    assert result['Fresh executed'] == 0
    assert '30.00 GiB cap' in result['Local Fly mirror cache']
    assert '0.00%' in result['Local Fly mirror cache']
    assert result['Fly whole-volume occupancy'].startswith('0.0% used')
