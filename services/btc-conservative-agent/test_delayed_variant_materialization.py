import test_declared_directional_context_integration as fixture
from research.entry_baseline_replay import materialize_v3_opportunity_replay
from research.latency_schedule_replay import TREATMENT
from research.entry_baseline_replay import delayed_variant_cohorts


def test_no_declarations_no_variants(tmp_path):
    generation,manifest=fixture.dataset(tmp_path)
    report=materialize_v3_opportunity_replay(tmp_path,generation=generation,canonical_manifest=manifest)
    assert all(not e['delayed_variants'] for e in report['episode_receipts'])


def test_actual_pinned_declarations_both_directions(tmp_path,monkeypatch):
    original=fixture.materialize_signal_time_baseline_schedules
    def producer(opportunity):
        snapshot=original(opportunity)
        opportunity['research_timing_declarations']=[dict(schema='declared_submission_timing_v1',
            evidence_basis='DECLARED_SIMULATION',provenance='FIXTURE_ONLY',declared_at_ts=100,
            source_capture_signature=c['capture_signature'],delay_sec=delay,ordering_treatment=TREATMENT)
            for c in snapshot['directional_schedules'].values() for delay in (0,2)]
        return snapshot
    monkeypatch.setattr(fixture,'materialize_signal_time_baseline_schedules',producer)
    generation,manifest=fixture.dataset(tmp_path)
    report=materialize_v3_opportunity_replay(tmp_path,generation=generation,canonical_manifest=manifest)
    assert len(report['episode_receipts'])==2
    for episode in report['episode_receipts']:
        assert len(episode['delayed_variants'])==2
        assert len({v['timing_model_sha256'] for v in episode['delayed_variants']})==2
        assert any(r.get('model_context_status')=='SUPPORTED'
                   for v in episode['delayed_variants'] for r in v['results'])
        original_ids={r['baseline_id'] for r in episode['results']}
        assert all({r['baseline_id'] for r in v['results']}==original_ids for v in episode['delayed_variants'])
    cohorts=delayed_variant_cohorts(report)
    assert len(cohorts)==2
    assert all(len(c['episode_receipts'])==2 for c in cohorts.values())


def test_plural_pinned_segments_deduplicate_and_reject_conflicts():
    import json,hashlib,pytest
    from research.baseline_execution_context import verified_segment_rows,_sha,IDENTITY_FIELDS
    parent={key:'fixture-'+key for key in IDENTITY_FIELDS}
    pins={}
    def envelope(name,row):
        raw=json.dumps(row).encode(); pins[name]=hashlib.sha256(raw).hexdigest()
        return dict(source_id=name,raw_bytes=raw,row=row,row_sha256=_sha(row))
    def pair(name,rows):
        segment=envelope(name,dict(schema='market_segment_v3',symbol='BTC',rows=rows))
        link=envelope(name+'-binding',{**parent,'segment_ref':dict(sha256=pins[name],relative_path=name)})
        return segment,link
    a,ab=pair('a',[{'bucket_ts':100},{'bucket_ts':101}])
    b,bb=pair('b',[{'bucket_ts':101},{'bucket_ts':102}])
    rows,hashes=verified_segment_rows([a,b],[ab,bb],pins,parent,'BTC')
    assert len(rows)==3 and len(hashes)==4
    bad,link=pair('bad',[{'bucket_ts':101,'conflict':True}])
    with pytest.raises(ValueError,match='TIMESTAMP_CONFLICT'):
        verified_segment_rows([a,bad],[ab,link],pins,parent,'BTC')
