"""Storage labels must distinguish capacity, inventory and transfer progress."""
import ast
from pathlib import Path


def test_storage_capacity_is_not_presented_as_remaining_download():
    tree=ast.parse(Path(__file__).with_name('bot.py').read_text(encoding='utf-8'))
    html=next(node.value.value for node in tree.body if isinstance(node,ast.Assign)
              and any(isinstance(t,ast.Name) and t.id=='HTML' for t in node.targets))
    assert 'Volume capacity:' in html
    assert 'capacity does not shrink after a wipe' in html
    assert 'not the download backlog' in html
    assert 'A dash means unavailable, not zero' in html
    assert 'only verified source deletion frees space' in html
    assert html.count('id="dataSizeVolumeTotal"')==1
    assert 'id="dataSizeVolumeTotal">-</span>' in html
    assert 'MB of <span id="dataSizeVolumeTotal"' not in html
