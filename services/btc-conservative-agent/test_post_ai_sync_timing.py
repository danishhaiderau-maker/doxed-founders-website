import ast
import math
import threading
import pytest
from pathlib import Path
from types import SimpleNamespace

SOURCE = Path(__file__).with_name('bot.py').read_text(encoding='utf-8')
TREE = ast.parse(SOURCE)


def environment():
    clock = [10.0]
    state = {'owner_ident': threading.get_ident(), 'stage': 'POST_AI_ENQUEUE'}
    ns = dict(math=math, threading=threading, state_lock=threading.RLock(),
              scheduled_ai_cycle_state=state,
              time=SimpleNamespace(monotonic=lambda: clock[0], time=lambda: clock[0]))
    function = next(n for n in TREE.body if isinstance(n, ast.FunctionDef)
                    and n.name == '_mark_scheduled_post_ai_stage')
    exec(compile(ast.Module(body=[function], type_ignores=[]), '<timing>', 'exec'), ns)
    return ns, state, clock


def test_actual_fanout_stage_call_sequence_and_durations():
    ns, state, clock = environment()
    function = next(n for n in TREE.body if isinstance(n, ast.FunctionDef) and n.name == 'process_signal')
    calls = {'_arm_shared_compressed_shadow_chase': 'POST_AI_SHADOW',
             '_arm_shared_discovery_touch_grid': 'POST_AI_GRID',
             'spawn_combo_lanes_from_ai_scan': 'POST_AI_COMBO',
             'spawn_continuous_lane_from_ai_scan': 'POST_AI_CONTINUOUS'}
    block = next(n for n in ast.walk(function) if isinstance(n, ast.If)
                 and any(isinstance(x, ast.Expr) and isinstance(x.value, ast.Call)
                         and isinstance(x.value.func, ast.Name)
                         and x.value.func.id == '_arm_shared_compressed_shadow_chase' for x in n.body))
    observed = []
    def run(stage):
        assert state['stage'] == stage
        observed.append(stage)
        clock[0] += 2
    for name, stage in calls.items():
        ns[name] = lambda *args, stage=stage: run(stage)
    ns.update(ctx={}, ai={}, edge_score=1, features={}, research_lane='scan')
    exec(compile(ast.Module(body=block.body, type_ignores=[]), '<actual-fanout>', 'exec'), ns)
    assert observed == list(calls.values())
    assert state['stage'] == 'POST_AI_PIPELINE'
    assert all(state['post_ai_stage_seconds'][stage] == 2 for stage in observed)
    clock[0] += 3
    ns['_mark_scheduled_post_ai_stage']('IDLE')
    assert state['post_ai_stage_seconds']['POST_AI_PIPELINE'] == 3
    assert 'post_ai_stage_monotonic' not in state


def test_nonowner_cannot_change_cycle_and_scheduler_finally_preserved():
    ns, state, clock = environment()
    state['owner_ident'] = -1
    before = dict(state)
    ns['_mark_scheduled_post_ai_stage']('POST_AI_EVIDENCE')
    assert state == before
    scheduler = next(n for n in TREE.body if isinstance(n, ast.FunctionDef)
                     and n.name == 'periodic_pipeline_loop')
    final = next(n.finalbody for n in ast.walk(scheduler) if isinstance(n, ast.Try)
                 and any('scheduled_ai_cycle_lock.release()' in ast.unparse(x) for x in n.finalbody))
    assert ast.unparse(final[0]) == "_mark_scheduled_post_ai_stage('IDLE')"
    assert "'completed_ts': time.time()" in ast.unparse(ast.Module(body=final, type_ignores=[]))
    assert "'stage': 'IDLE'" in ast.unparse(ast.Module(body=final, type_ignores=[]))
    state['owner_ident'] = threading.get_ident()
    ns['_mark_scheduled_post_ai_stage']('POST_AI_PIPELINE')
    clock[0] += 4
    released = []
    ns['scheduled_ai_cycle_lock'] = SimpleNamespace(release=lambda: released.append(True))
    exec(compile(ast.Module(body=final, type_ignores=[]), '<actual-scheduler-finally>', 'exec'), ns)
    assert released == [True]
    assert state['stage'] == 'IDLE' and state['owner_ident'] is None
    assert state['completed_ts'] == clock[0]
    assert state['post_ai_stage_seconds']['POST_AI_PIPELINE'] == 4


def test_nested_enqueue_and_repeated_stages_accumulate_bounded_timings():
    ns, state, clock = environment()
    mark = ns['_mark_scheduled_post_ai_stage']
    mark('POST_AI_COMBO')
    clock[0] += 2
    enqueue = next(n for n in TREE.body if isinstance(n, ast.FunctionDef)
                   and n.name == 'enqueue_post_ai_research_hooks')
    ns.update(uuid=SimpleNamespace(uuid4=lambda: None),
              _get_post_ai_evidence_worker=lambda hook: SimpleNamespace(submit=lambda *a, **k: True),
              _post_ai_evidence_status={'submitted': 0, 'rejected': 0})
    exec(compile(ast.Module(body=[enqueue], type_ignores=[]), '<actual-enqueue>', 'exec'), ns)
    ns['enqueue_post_ai_research_hooks']({'trade_id': 'child'}, {}, 'lane')
    assert state['stage'] == 'POST_AI_COMBO'
    assert state['post_ai_stage_monotonic'] == 10
    clock[0] += 3
    mark('POST_AI_CONTINUOUS')
    mark('POST_AI_COMBO')
    clock[0] += 4
    mark('IDLE')
    assert state['post_ai_stage_seconds']['POST_AI_COMBO'] == 9
    assert len(state['post_ai_stage_seconds']) <= 6


def test_failure_during_timed_work_executes_actual_scheduler_cleanup():
    ns, state, clock = environment()
    scheduler = next(n for n in TREE.body if isinstance(n, ast.FunctionDef)
                     and n.name == 'periodic_pipeline_loop')
    final = next(n.finalbody for n in ast.walk(scheduler) if isinstance(n, ast.Try)
                 and any('scheduled_ai_cycle_lock.release()' in ast.unparse(x) for x in n.finalbody))
    released = []
    ns['scheduled_ai_cycle_lock'] = SimpleNamespace(release=lambda: released.append(True))
    ns['_mark_scheduled_post_ai_stage']('POST_AI_COMBO')
    clock[0] += 7
    failing = ast.parse("raise RuntimeError('timed work failed')").body
    tree = ast.fix_missing_locations(ast.Module(body=[ast.Try(body=failing, handlers=[],
                                                               orelse=[], finalbody=final)], type_ignores=[]))
    with pytest.raises(RuntimeError, match='timed work failed'):
        exec(compile(tree, '<actual-failure-finally>', 'exec'), ns)
    assert state['stage'] == 'IDLE' and state['owner_ident'] is None
    assert state['post_ai_stage_seconds']['POST_AI_COMBO'] == 7
    assert released == [True]
