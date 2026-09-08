import ast
from pathlib import Path
from microstructure_bucket_clock import observed_bucket


def test_actual_telemetry_helper_tracks_gaps_without_backfill():
    tree=ast.parse(Path(__file__).with_name('bot.py').read_text(encoding='utf-8-sig'))
    fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='_record_microstructure_capture_observation')
    ns={'_microstructure_capture_observation':dict(skipped_buckets_this_process=0,last_gap=None,last_capture_lag_sec=None)}
    exec(compile(ast.Module(body=[fn],type_ignores=[]),'bot.py','exec'),ns)
    record=ns['_record_microstructure_capture_observation']
    record(observed_bucket(100,110.25),100)
    status=ns['_microstructure_capture_observation']
    assert status['skipped_buckets_this_process']==10
    assert status['last_capture_lag_sec']==10.25
    assert status['last_gap']['skipped_start_ts']==100
    record(observed_bucket(111,111.01),111)
    current=ns['_microstructure_capture_observation']
    assert current['skipped_buckets_this_process']==10
    assert current['last_gap']==status['last_gap']
    assert current['last_capture_lag_sec']<.02
    record(observed_bucket(112,111.5),112)
    assert ns['_microstructure_capture_observation']==current


def test_actual_loop_and_public_status_are_wired():
    tree=ast.parse(Path(__file__).with_name('bot.py').read_text(encoding='utf-8-sig'))
    loop=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='microstructure_capture_loop')
    calls=[n for n in ast.walk(loop) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='_record_microstructure_capture_observation']
    assert len(calls)==1 and [a.id for a in calls[0].args]==['scheduling','next_bucket']
    dictionaries=[n for n in ast.walk(tree) if isinstance(n,ast.Dict) and any(isinstance(k,ast.Constant) and k.value=='microstructure_tape' for k in n.keys)]
    assert any(any(k is None and isinstance(v,ast.Name) and v.id=='_microstructure_capture_observation' for k,v in zip(value.keys,value.values))
        for d in dictionaries for key,value in zip(d.keys,d.values)
        if isinstance(key,ast.Constant) and key.value=='microstructure_tape' and isinstance(value,ast.Dict))
