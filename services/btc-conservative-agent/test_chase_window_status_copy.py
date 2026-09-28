"""Execute the actual dashboard status renderer; no DOM screenshot inference."""
import json
import re
import subprocess
from pathlib import Path


def test_status_uses_age_windows_and_preserves_late_hold_exception():
    source = Path(__file__).with_name('bot.py').read_text(encoding='utf-8')
    function = re.search(r'    function renderChaseBucketGateStatus\(buckets\) \{.*?\n    \}', source, re.S).group()
    script = '''
const el={innerHTML:''};const document={getElementById:()=>el};
''' + function + '''
renderChaseBucketGateStatus({'0':false,'1':true,'2':false,'3':true});
const selected=el.innerHTML;
renderChaseBucketGateStatus({'0':false,'1':false});
console.log(JSON.stringify({selected,off:el.innerHTML}));
'''
    result = subprocess.run(['node','-e',script],capture_output=True,text=True,timeout=20)
    assert result.returncode == 0,result.stderr
    content = json.loads(result.stdout)
    assert '5-minute signal-age windows' in content['selected']
    assert 'tick count' not in content['selected']
    assert 'subject to other gates' in content['selected']
    assert 'before/between enabled windows' in content['selected']
    assert 'hold their last permitted limit until TTL' in content['selected']
    assert 'no further repricing' in content['selected']
    assert 'All chase buckets OFF' in content['off']
    assert 'paper limit placement/repricing and pending-order fills are blocked' in content['off']
    paragraph = re.search(r'<p[^>]*>Checked = eligible to place/reprice.*?</p>',source).group()
    assert 'Disabled windows before/between enabled windows' in paragraph
    assert 'Only after the last enabled window' in paragraph
    assert 'hold their last permitted limit until TTL' in paragraph
    assert 'no further repricing' in paragraph
    assert 'If a later window is unchecked' not in source
