"""Exercise the actual LAB accumulator without importing the trading runtime."""
import ast
import copy
import json
import math
from pathlib import Path
import threading

import pytest


def runtime(tmp_path, ledger=None):
    tree = ast.parse(Path(__file__).with_name('bot.py').read_text(encoding='utf-8'))
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                    and node.name == 'update_lane_lab_pnl_ledger')
    env = dict(state={'lane_lab_pnl_ledger': copy.deepcopy(ledger or {})}, state_lock=threading.RLock(),
               _normalize_lane_key=lambda lane: lane.upper(), json=json, utc_iso=lambda: 'fixture',
               LANE_LAB_PNL_LEDGER_FILE=str(tmp_path/'lab.json'))
    exec(compile(ast.Module(body=[function], type_ignores=[]), 'bot.py', 'exec'), env)
    return env, env['update_lane_lab_pnl_ledger']


def test_half_cent_closes_reconcile_all_unrounded_partitions(tmp_path):
    env, update = runtime(tmp_path)
    for pnl, side in [(0.005, 'LONG'), (0.005, 'SHORT'), (-0.005, 'SHORT')]:
        update('test', 'CLOSE', pnl, side)
    row = env['state']['lane_lab_pnl_ledger']['TEST']
    assert row['net_pnl_usd'] == pytest.approx(0.005)
    assert row['gross_wins_usd'] == pytest.approx(0.01)
    assert row['gross_losses_usd'] == pytest.approx(-0.005)
    assert row['net_pnl_usd'] == pytest.approx(row['gross_wins_usd'] + row['gross_losses_usd'])
    assert row['net_pnl_usd'] == pytest.approx(row['long_pnl_usd'] + row['short_pnl_usd'])
    assert (row['closes'], row['wins'], row['losses'], row['long_closes'], row['short_closes']) == (3, 2, 1, 1, 2)
    assert row['pnl_precision_status'] == 'FULL_PRECISION_FROM_CREATION'
    assert json.loads((tmp_path/'lab.json').read_text())['lanes']['TEST'] == row


def test_many_fractional_closes_and_json_restart_preserve_precision(tmp_path):
    env, update = runtime(tmp_path)
    events = [(0.00137, 'LONG'), (-0.00093, 'SHORT'), (0.00219, 'SHORT'), (-0.00028, 'LONG')] * 250
    for index, (pnl, side) in enumerate(events):
        update('test', 'CLOSE', pnl, side)
        if index == 499:
            env, update = runtime(tmp_path, json.loads((tmp_path/'lab.json').read_text())['lanes'])
    row = env['state']['lane_lab_pnl_ledger']['TEST']
    assert row['net_pnl_usd'] == pytest.approx(math.fsum(pnl for pnl, _ in events), abs=1e-12)
    assert row['net_pnl_usd'] == pytest.approx(row['gross_wins_usd'] + row['gross_losses_usd'], abs=1e-12)
    assert row['net_pnl_usd'] == pytest.approx(row['long_pnl_usd'] + row['short_pnl_usd'], abs=1e-12)
    assert row['pnl_precision_status'] == 'FULL_PRECISION_FROM_CREATION'


def test_legacy_rounded_totals_not_reconstructed_or_relabelled_exact(tmp_path):
    old = dict(net_pnl_usd=1.48, gross_wins_usd=3.36, gross_losses_usd=-1.85,
               long_pnl_usd=0.04, short_pnl_usd=1.46, closes=8)
    env, update = runtime(tmp_path, {'TEST': old})
    update('test', 'OPEN')
    row = env['state']['lane_lab_pnl_ledger']['TEST']
    assert all(row[key] == value for key, value in old.items())
    assert row['pnl_precision_status'] == 'LEGACY_ROUNDED_BASELINE_NOT_RECONSTRUCTED'
    update('test', 'CLOSE', 0.005, 'LONG')
    assert row['net_pnl_usd'] == pytest.approx(1.485)
    assert row['gross_wins_usd'] == pytest.approx(3.365)
    assert row['long_pnl_usd'] == pytest.approx(0.045)
    assert row['net_pnl_usd'] - (row['gross_wins_usd'] + row['gross_losses_usd']) == pytest.approx(-0.03)
    assert row['pnl_precision_status'] == 'LEGACY_ROUNDED_BASELINE_NOT_RECONSTRUCTED'


def test_unclassified_direction_does_not_fabricate_long_or_short(tmp_path):
    env, update = runtime(tmp_path)
    update('test', 'CLOSE', 0.005, None)
    row = env['state']['lane_lab_pnl_ledger']['TEST']
    assert row['net_pnl_usd'] == pytest.approx(0.005)
    assert row['long_closes'] == row['short_closes'] == 0
    assert row['long_pnl_usd'] == row['short_pnl_usd'] == 0
