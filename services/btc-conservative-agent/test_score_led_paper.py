import ast
import copy
import os
from pathlib import Path
import subprocess
import sys

import pytest
from score_led_paper import project_score_led_paper, TREATMENT_ID

SPEC = {"admission_treatment": TREATMENT_ID, "paper_only": True,
        "platform_relay_eligible": False, "live_copy_eligible": False}

def call(ai, **kw):
    return project_score_led_paper(ai, spec=SPEC, force_paper=True, live_armed=False, **kw)

@pytest.mark.parametrize("verdict", ["REJECT", "NO_TRADE", "APPROVE", "SOFT_REJECT"])
def test_original_verdict_retained(verdict):
    ai = {"decision": verdict, "direction": "NO_TRADE", "long_score": 20,
          "short_score": 35, "approved": False, "model": "m", "prompt_id": "p"}
    before = copy.deepcopy(ai)
    child, reason = call(ai)
    assert child["direction"] == "SHORT"
    assert child["original_ai_snapshot"] == before == ai
    assert child["admission_is_ai_approval"] is False
    assert reason == "SCORE_LED_HIGHER_DIRECTION_PAPER_ONLY"
    child["original_ai_snapshot"]["decision"] = "changed"
    assert ai == before

@pytest.mark.parametrize("scores,reason", [((0, 1), None), ((1, 0), None),
    ((20,20), "TIE"), ((None,35), "MISSING"), (("20",35), "MALFORMED"),
    ((True,35), "MALFORMED"), ((float("nan"),35), "RANGE"), ((101,35), "RANGE")])
def test_score_contract(scores, reason):
    child, why = call({"decision":"REJECT", "long_score":scores[0], "short_score":scores[1]})
    assert (child is None) == (reason is not None)
    if reason:
        assert reason in why

@pytest.mark.parametrize("changes", [{"force_paper":False}, {"live_armed":True},
    {"live_armed":None}, {"inverted":True}, {"spec":{**SPEC,"platform_relay_eligible":True}},
    {"spec":{**SPEC,"admission_treatment":"AI_FILTERED_V1"}}])
def test_boundaries(changes):
    kwargs = dict(spec=SPEC, force_paper=True, live_armed=False)
    kwargs.update(changes)
    assert project_score_led_paper({"long_score":20,"short_score":35}, **kwargs)[0] is None

def test_error_never_projected():
    assert call({"ai_error":True,"long_score":20,"short_score":35})[0] is None

def test_startup_registry_identity_and_module_parity():
    code = '''
import importlib,json
from combo_pathway_config import ACTIVE_TILE_REGISTRY, validate_tile_registry, active_tile_lifecycle_manifest
assert validate_tile_registry() == ()
assert all(row['admission_treatment'] for row in active_tile_lifecycle_manifest())
for spec in ACTIVE_TILE_REGISTRY.values():
 module=importlib.import_module(spec['implementation_modules'][0][:-3])
 assert module.POLICY_ID == spec['raw_policy_id']
 assert not spec['live_copy_eligible'] and not spec['default_enabled']
print(json.dumps([(s['raw_policy_id'],s['policy_signature']) for s in ACTIVE_TILE_REGISTRY.values()]))
'''
    outputs = []
    for enabled in ('0','1'):
        env = {**os.environ, 'SCORE_LED_PAPER_RESEARCH_ENABLED':enabled}
        outputs.append(subprocess.check_output([sys.executable,'-c',code],env=env,text=True))
    assert outputs[0] != outputs[1]
    assert 'SCORE_LED_PAPER_V1::' not in outputs[0]
    assert 'SCORE_LED_PAPER_V1::' in outputs[1]

def test_runtime_fanout_rejects_original_but_enqueues_projected_child():
    tree = ast.parse(Path('bot.py').read_text(encoding='utf-8-sig'))
    fn = next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='spawn_combo_lanes_from_ai_scan')
    captured=[]
    env = dict(is_ai_scan_lane=lambda x:True,is_research_data_collection=lambda:True,
        state={'live_armed':False},COMBO_EXECUTION_LANES=('L',),
        COMBO_LANE_SPECS={'L':{**SPEC,'combo_key':'treatment'}},
        _force_paper_mode_active=lambda:True,invert_signal_active=lambda:False,
        compute_directional_spread=lambda *a:1,_enrich_combo_lane_features=lambda *a:{},
        is_independent_ai_lane=lambda x:False,is_shared_ai_direction_lane=lambda x:False,
        is_deterministic_bracket_lane=lambda x:False,combo_lane_match_detail=lambda *a,**k:{'passes':True},
        is_research_lane_enabled=lambda x:True,_shared_ai_call_id=lambda **k:'call',
        _v3_lane_policy_material=lambda x:{},_stamp_shared_ai_lane_verdict=lambda *a,**k:None,
        _write_v3_shared_lane_decision=lambda *a,**k:True,
        _enqueue_combo_lane_execution=lambda *a:captured.append(a))
    exec(compile(ast.Module(body=[fn],type_ignores=[]),'bot.py','exec'),env)
    ai={'decision':'REJECT','direction':'NO_TRADE','long_score':20,'short_score':35}
    env[fn.name]({},ai,0,{},'AI_SCAN')
    assert captured[0][1]['direction']=='SHORT'
    assert ai['decision']=='REJECT'
    captured.clear()
    env['state']['live_armed'] = True
    env[fn.name]({},ai,0,{},'AI_SCAN')
    assert not captured
