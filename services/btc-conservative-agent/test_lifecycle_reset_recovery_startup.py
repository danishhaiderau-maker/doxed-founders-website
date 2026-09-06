import pytest
import lifecycle_reset_recovery_startup as startup


@pytest.mark.parametrize('fail', [False, True])
def test_actual_bot_initializes_before_starting_owner(monkeypatch, tmp_path, fail):
    import ast
    import os
    import re
    from pathlib import Path
    from types import SimpleNamespace
    events = []
    tree = ast.parse(Path(__file__).with_name('bot.py').read_text(encoding='utf-8'))
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                    and n.name == '_start_lifecycle_pipeline_runtime')
    def initialize(**kwargs):
        events.append('initialize')
        assert kwargs['owner_quiescent']() is True
        assert kwargs['epoch_provider']() == 'epoch-test'
        assert kwargs['reset_lock'] is lock
        if fail:
            raise ValueError('RESET_RECOVERY_STARTUP_RESET_BUSY')
    def start(*args, **kwargs):
        events.append('start')
        return True
    lock = object()
    runtime = SimpleNamespace(start=start, status=lambda: {})
    namespace = dict(os=os, re=re, importlib=SimpleNamespace(import_module=lambda _: runtime),
        logger=SimpleNamespace(info=lambda *a: None, error=lambda *a: None),
        _runtime_git_rev_exact=lambda: 'a'*40, _data_sync_runtime_root=lambda: tmp_path,
        _collector_v22_epoch_id=lambda: 'epoch-test', _fresh_collection_lock=lock,
        _LIFECYCLE_PIPELINE_RUNTIME=None, _lifecycle_pipeline_pressure_probe=lambda: [],
        _lifecycle_pipeline_overlap_probe=lambda: [])
    monkeypatch.setattr(startup, 'initialize_reset_recovery', initialize)
    exec(compile(ast.Module(body=[function], type_ignores=[]), '<bot-startup>', 'exec'), namespace)
    assert namespace['_start_lifecycle_pipeline_runtime']() is (not fail)
    assert events == (['initialize'] if fail else ['initialize', 'start'])


class Lock:
    def __init__(self, events, available=True):
        self.events, self.available = events, available
        self.held = False
    def acquire(self, *, blocking):
        assert blocking is False
        self.events.append('lock')
        self.held = self.available
        return self.available
    def release(self):
        assert self.held
        self.held = False
        self.events.append('release')


def config(events, **changes):
    value = dict(root='/runtime', operation_path='/runtime/research_reset_receipts/r/operation.json',
                 operation_sha256='a'*64, trigger='SOURCE_LEDGER_TRUNCATED:opportunity.jsonl',
                 epoch_provider=lambda: events.append('epoch') or 'epoch-new',
                 reset_lock=Lock(events), owner_quiescent=lambda: events.append('owner') or True)
    value.update(changes)
    return value


def test_one_step_under_lock_with_exact_arguments(monkeypatch):
    events=[]
    cfg=config(events)
    def recover(root, trigger, **kwargs):
        assert cfg['reset_lock'].held
        events.append('recover')
        assert root == '/runtime' and trigger == cfg['trigger']
        assert kwargs == dict(operation_path=cfg['operation_path'],operation_sha256='a'*64,current_epoch_id='epoch-new')
        return {'complete':False,'status':'COPY'}
    monkeypatch.setattr(startup,'recover_reset_index',recover)
    result=startup.initialize_reset_recovery(**cfg)
    assert events==['lock','owner','epoch','recover','release']
    assert result['recovery']['status']=='COPY'


@pytest.mark.parametrize('case', ['owner','epoch','recover','lock'])
def test_failures_never_escape_lock_or_invoke_early(monkeypatch,case):
    events=[]; cfg=config(events)
    if case=='owner': cfg['owner_quiescent']=lambda: False
    if case=='epoch': cfg['epoch_provider']=lambda: None
    if case=='lock': cfg['reset_lock']=Lock(events,False)
    def recover(*a,**kw):
        events.append('recover')
        raise ValueError('failed')
    monkeypatch.setattr(startup,'recover_reset_index',recover)
    with pytest.raises(ValueError): startup.initialize_reset_recovery(**cfg)
    assert not cfg['reset_lock'].held
    assert ('recover' in events)==(case=='recover')
    assert ('release' in events)==(case!='lock')


def test_missing_is_noop_partial_is_failure():
    events=[]; cfg=config(events,operation_path=None,operation_sha256=None,trigger=None)
    assert startup.initialize_reset_recovery(**cfg)['status']=='NOT_CONFIGURED'
    assert events==[]
    cfg['operation_sha256']='a'*64
    with pytest.raises(ValueError,match='CONFIG_INVALID'): startup.initialize_reset_recovery(**cfg)
    assert events==[]


@pytest.mark.parametrize('trigger', ['SOURCE_LEDGER_ROTATED:opportunity.jsonl','SOURCE_LEDGER_TRUNCATED:../x.jsonl',''])
def test_no_generic_recovery_configuration(trigger):
    with pytest.raises(ValueError,match='CONFIG_INVALID'):
        startup.initialize_reset_recovery(**config([],trigger=trigger))
