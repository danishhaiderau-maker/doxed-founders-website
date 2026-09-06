import json
import pytest
import research_reset_progress as module


def make(root):
    return module.make_reset_progress_callback(root,attempt_id='a'*32,reset_id='b'*24)


def event(n=0,phase='EXECUTOR_FINGERPRINT'):
    return dict(phase=phase,completed_targets=n,total_targets=10,fingerprinted_bytes=n*4)


def test_throttle_boundaries_and_no_raw_path(tmp_path,monkeypatch):
    clock=[1.]
    monkeypatch.setattr(module.time,'monotonic',lambda:clock[0])
    callback=make(tmp_path)
    assert callback(event())
    assert not callback(event(1))
    clock[0]=6.
    assert callback(event(2))
    assert callback(event(3,'DELETER_FINGERPRINT'))
    assert callback(event(10,'DELETER_FINGERPRINT'))
    files=list((tmp_path/'research_reset_receipts'/'_progress').iterdir())
    assert len(files)==1
    raw=files[0].read_bytes(); value=json.loads(raw)
    assert len(raw)<=4096 and str(tmp_path).encode() not in raw
    assert value['attempt_id']=='a'*32 and value['authority']=='ADVISORY_ONLY'


def test_bad_identity_and_counts(tmp_path):
    with pytest.raises(ValueError):
        module.make_reset_progress_callback(tmp_path,attempt_id='../bad',reset_id='b'*24)
    callback=make(tmp_path)
    assert not callback(event(-1))
    assert not callback(event(11))
    assert not callback(event(0,'UNKNOWN'))
    assert not (tmp_path/'research_reset_receipts').exists()


def test_runtime_attempt_shape_and_all_executor_wiring(tmp_path):
    import ast
    from pathlib import Path
    with pytest.raises(ValueError):
        module.make_reset_progress_callback(tmp_path,attempt_id='a'*24,reset_id='b'*24)
    with pytest.raises(ValueError):
        module.make_reset_progress_callback(tmp_path,attempt_id='a'*32,reset_id='b'*32)
    tree=ast.parse(Path(__file__).with_name('bot.py').read_text(encoding='utf-8'))
    function=next(n for n in tree.body if isinstance(n,ast.FunctionDef)
                  and n.name=='_perform_fresh_collection_reset_quiesced')
    calls=[n for n in ast.walk(function) if isinstance(n,ast.Call)
           and isinstance(n.func,ast.Name) and n.func.id=='execute_research_reset']
    assert len(calls)==3
    for call in calls:
        kwargs={k.arg:k.value for k in call.keywords}
        callback=kwargs['progress_callback']
        assert isinstance(callback,ast.Call) and callback.func.id=='make_reset_progress_callback'
        assert ast.unparse(callback.args[0])=='root'
        identity={k.arg:ast.unparse(k.value) for k in callback.keywords}
        assert identity['attempt_id']=='diagnostic_attempt_id'
        assert identity['reset_id']=='reset_id'
        if 'scope_name' in kwargs:
            assert identity['scope_name']==ast.unparse(kwargs['scope_name'])


def test_storage_failure_nonfatal(tmp_path,monkeypatch):
    callback=make(tmp_path)
    def failed(*a,**k): raise OSError('disk full')
    monkeypatch.setattr(module.os,'replace',failed)
    assert callback(event()) is False
    assert list((tmp_path/'research_reset_receipts'/'_progress').iterdir())==[]


def test_failed_storage_attempts_are_throttled(tmp_path,monkeypatch):
    callback=make(tmp_path)
    clock=[1.]
    attempts=[]
    monkeypatch.setattr(module.time,'monotonic',lambda:clock[0])
    def failed(*a,**k):
        attempts.append(True)
        raise OSError(28,'disk full')
    monkeypatch.setattr(module.tempfile,'mkstemp',failed)
    assert callback(event()) is False
    assert callback(event(1)) is False
    assert len(attempts)==1
    clock[0]=6.
    assert callback(event(2)) is False
    assert len(attempts)==2
