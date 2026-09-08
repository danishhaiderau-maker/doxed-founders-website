import ast
from pathlib import Path


SOURCE=Path(__file__).parent/'research'/'research_dashboard.py'


def projection():
    tree=ast.parse(SOURCE.read_text(encoding='utf-8'))
    node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='_shadow_tier_projection')
    ns={}; exec(compile(ast.Module(body=[node],type_ignores=[]),'dashboard','exec'),ns)
    return ns['_shadow_tier_projection']


def test_missing_and_stale_hide_counts():
    fn=projection()
    assert fn(None,True)['rows']==[]
    assert fn({'complete_replay_count':999},False)['rows']==[]


def test_conditional_only_counts_separate_bounded_no_promotion():
    fn=projection()
    report={'conditional_report':{'complete_replay_count':3,'unknown_replay_count':1},
        'conditional_delayed_variant_reports':[{'timing_model_sha256':str(i),'report':{'complete_replay_count':i}} for i in range(20)]}
    result=fn(report,True)
    assert result['rows'][0]['complete'] is None
    assert result['rows'][1]['complete']==3
    assert len(result['rows'])==18 and result['truncated']
    assert all(row['qualification_allowed'] is False for row in result['rows'])
    assert result['rows'][2]['timing']=='0'
    assert result['rows'][2]['venue_acceptance']=='UNKNOWN'


def test_route_atomic_and_renderer_uses_text_not_html():
    tree=ast.parse(SOURCE.read_text(encoding='utf-8'))
    route=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='api_research_design')
    assert any(isinstance(n,ast.Call) and isinstance(n.func,ast.Name)
        and n.func.id=='_declared_atomic_generation_report'
        and any(isinstance(a,ast.Constant) and a.value=='conservative_shadow_terminal_report.json' for a in n.args)
        for n in ast.walk(route))
    script=SOURCE.read_text(encoding='utf-8').split('async function loadResearchDesign()',1)[1].split('const current =',1)[0]
    assert 'td.textContent = row[key]' in script
    assert 'innerHTML' not in script
    bad='<img src=x onerror=alert(1)>'
    result=projection()({'delayed_variant_reports':[{'timing_model_sha256':bad,'report':{}}]},True)
    assert result['rows'][2]['timing']==bad  # renderer must treat it as literal text
