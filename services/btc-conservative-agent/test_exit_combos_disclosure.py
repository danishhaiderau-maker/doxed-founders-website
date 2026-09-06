import re
import shutil
import subprocess
from pathlib import Path
import pytest


def test_exit_inventory_preserves_all_tables_and_labels():
    source=(Path(__file__).parent/'research/research_dashboard.py').read_text(encoding='utf-8')
    section=source.split('<section id="sec-exit-combos">',1)[1].split('</section>',1)[0]
    assert section.count('<details')==section.count('</details>')==1
    assert section.count('<table>') > 20
    assert section.index('<details') < section.index('<table>')
    assert section.rindex('</table>') < section.index('</details>')
    ids=re.findall(r'id="([^"]+)"',section)
    assert len(ids)==len(set(ids))


def test_actual_source_gate_collapses_missing_reopens_populated():
    node=shutil.which('node')
    if not node: pytest.skip('Node unavailable')
    source=(Path(__file__).parent/'research/research_dashboard.py').read_text(encoding='utf-8')
    fn=source.split('function executionPanelSource(',1)[1].split('async function loadChaseThreshold',1)[0]
    js="""
const assert=require('assert');
const inventory={open:true}, note={}, kpi={innerHTML:'OLD'}, row={innerHTML:'OLD'}, provenance={textContent:'OLD'};
const root={querySelectorAll:q=>q==='tbody'?[row]:[kpi],querySelector:q=>provenance};
global.document={getElementById:id=>id==='sec-exit-combos'?root:id==='exit-combos-detail-inventory'?inventory:note};
"""+'function executionPanelSource('+fn+"""
assert.equal(executionPanelSource('exit-combos',{source_available:false,empty_reason:'ATOMIC_GENERATION_UNAVAILABLE'}),false);
assert.equal(inventory.open,false);assert.equal(kpi.innerHTML,'');assert.equal(provenance.textContent,'');
assert(note.textContent.includes('ATOMIC_GENERATION_UNAVAILABLE'));assert(note.textContent.includes('Report Explorer'));
assert(row.innerHTML.includes('UNAVAILABLE'));
assert.equal(executionPanelSource('exit-combos',{source_available:true,generation_identity:{id:'exact'}}),true);
assert.equal(inventory.open,true);assert(provenance.textContent.includes('exact'));
"""
    result=subprocess.run([node,'-e',js],capture_output=True,text=True,timeout=20)
    assert result.returncode==0,result.stdout+result.stderr
