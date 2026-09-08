import ast
from pathlib import Path
from types import SimpleNamespace


def test_bounded_latest_maximum_and_invalid_samples():
    source=Path(__file__).with_name('bot.py').read_text(encoding='utf-8')
    tree=ast.parse(source)
    helper=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='_record_microstructure_capture_duration')
    clock=[10.0]
    namespace={'time':SimpleNamespace(monotonic=lambda:clock[0]),
               '_microstructure_capture_observation':{'skipped_buckets_this_process':3}}
    exec(compile(ast.Module(body=[helper],type_ignores=[]),'capture-timing','exec'),namespace)
    record=namespace['_record_microstructure_capture_duration']
    record('quote_lock_wait',8)
    record('quote_lock_wait',9)
    record('trade_tape_lock_wait',7)
    record('append_total',5)
    for phase in ('unbounded-new-phase','quote_lock_wait'):
        record(phase,float('nan'))
    record('append_total',11)
    result=namespace['_microstructure_capture_observation']
    assert result['skipped_buckets_this_process']==3
    assert result['timing_seconds']=={
        'quote_lock_wait':{'latest':1,'maximum':2},
        'trade_tape_lock_wait':{'latest':3,'maximum':3},
        'append_total':{'latest':5,'maximum':5}}
    # Existing public collection projection carries the bounded observation map.
    assert '**_microstructure_capture_observation' in source
