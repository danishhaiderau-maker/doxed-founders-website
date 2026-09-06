"""Execute production route helpers without starting the trading runtime."""
import ast
import os
from pathlib import Path
import pytest
from test_data_sync_quarantine_receipt import fixture


@pytest.mark.parametrize('suffix', ['', '-wal', '-shm'])
def test_route_original_binding_is_strict(tmp_path, suffix):
    tree = ast.parse(Path(__file__).with_name('bot.py').read_text(encoding='utf-8-sig'))
    names = {'_data_sync_forensic_binding', '_data_sync_consistency_mode'}
    selected = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    scope = {'Path': Path, 'os': os, '_data_sync_runtime_root': lambda: tmp_path}
    exec(compile(ast.Module(body=selected, type_ignores=[]), 'bot.py', 'exec'), scope)
    directory = fixture(tmp_path)
    component = directory / ('lifecycle_index.sqlite3' + suffix)
    assert scope['_data_sync_forensic_binding'](component)['size'] == component.stat().st_size
    assert scope['_data_sync_consistency_mode'](component) == 'strict_generation_v1'
    assert scope['_data_sync_consistency_mode'](tmp_path / 'active.sqlite3') == 'sqlite_snapshot_v1'
    (directory / 'receipt.json').write_text('{}')
    with pytest.raises(ValueError):
        scope['_data_sync_consistency_mode'](component)
