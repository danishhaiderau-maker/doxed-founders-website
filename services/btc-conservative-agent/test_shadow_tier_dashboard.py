import ast
from pathlib import Path


SOURCE=Path(__file__).parent/'research'/'research_dashboard.py'


def test_empty_and_failed_states_are_outside_scrollable_table():
    script = SOURCE.read_text(encoding='utf-8').split('async function loadResearchDesign()', 1)[1].split('async function loadEvidenceCoverage()', 1)[0]
    assert "tierNote.textContent = 'UNAVAILABLE OR STALE" in script
    assert "tierBody.textContent" not in script
    assert "tierTable.hidden = false" in script
    failure = script.split('} catch (error)', 1)[1]
    assert 'tierTable.hidden = true' in failure
    assert 'tierBody.replaceChildren()' in failure
    assert 'research evidence request failed' in failure
GEN={key:key+'-1' for key in ('source_revision','deployed_revision','analyzer_revision','manifest_entry_hash','epoch_id','tile_config_signature','generation_key','evaluator_version')}
MAN={**GEN,'dataset_epoch':GEN['epoch_id'],'config_signature':GEN['tile_config_signature']}


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
    report={'generation':GEN,'conditional_report':{'generation':GEN,'complete_replay_count':3,'unknown_replay_count':1},
        'conditional_delayed_variant_reports':[{'timing_model_sha256':str(i),'report':{'generation':GEN,'complete_replay_count':i}} for i in range(20)]}
    result=fn(report,True,MAN)
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
    result=projection()({'generation':GEN,'delayed_variant_reports':[{'timing_model_sha256':bad,'report':{'generation':GEN}}]},True,MAN)
    assert result['rows'][2]['timing']==bad  # renderer must treat it as literal text


def test_actual_flask_atomic_manifest_and_identity(tmp_path,monkeypatch):
    import json
    monkeypatch.delenv('BTC_AGENT_DATA_DIR',raising=False)
    from research import research_dashboard as dashboard
    monkeypatch.setattr(dashboard,'ROOT',tmp_path)
    monkeypatch.setattr(dashboard,'DATA_ROOT',tmp_path)
    # Freshness itself has its own runtime tests; assert the route passes the
    # actual loaded manifest, not a report or invented generation alias.
    def freshness(manifest): return {'current':manifest.get('test_current',False)}
    monkeypatch.setattr(dashboard,'_generation_freshness_meta',freshness)
    published=tmp_path/dashboard.PUBLISHED_REPORTS_DIR; published.mkdir()
    filename='conservative_shadow_terminal_report.json'
    manifest={**MAN,'test_current':True,'reports':[]}
    path=published/dashboard.REPORT_MANIFEST_FILE
    def get():
        path.write_text(json.dumps(manifest))
        return dashboard.app.test_client().get('/api/research-design').get_json()['shadow_tiers']
    report={'generation':GEN,'complete_replay_count':2,'conditional_report':{'generation':GEN,'complete_replay_count':3},
        'conditional_delayed_variant_reports':[{'timing_model_sha256':'a'*64,'report':{'generation':GEN,'complete_replay_count':4}}]}
    (published/filename).write_text(json.dumps(report))
    assert get()['rows']==[]  # loose file is not sufficient
    manifest['reports']=[{'file':filename}]
    assert [row['complete'] for row in get()['rows']]==[2,3,4]
    manifest['test_current']=False
    assert get()['rows']==[]
    manifest['test_current']=True; manifest['source_revision']='other'
    assert get()['status']=='UNAVAILABLE_GENERATION_IDENTITY'
    manifest['source_revision']=GEN['source_revision']
    report['conditional_delayed_variant_reports'][0]['report']['generation']={**GEN,'generation_key':'other'}
    (published/filename).write_text(json.dumps(report))
    assert get()['rows'][2]['complete'] is None
