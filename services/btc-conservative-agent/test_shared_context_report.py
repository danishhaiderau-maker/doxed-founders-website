import hashlib
import json
from test_lifecycle_pipeline import _append, _patch_provenance
from test_lane_no_order_disposition import audit, KEY, PROV
from research.research_v3_report import build_safe_policy_genome_v3_report, REPORT_FILE
from research.shared_context_coverage import build_shared_context_coverage


def test_full_cohort_paginates_and_anchor_overflow_is_local(tmp_path):
    anchors, contexts = [], []
    for index in range(70):
        episode = 'episode-%03d' % index
        lane = {**audit(), 'episode_id':episode, 'opportunity_id':'opportunity:'+episode}
        anchors.append(lane)
        contexts.append({**PROV, 'epoch_id':KEY.collection_epoch_id, 'episode_id':episode,
            'shared_ai_call_id':lane['shared_ai_call_id'], 'record_id':lane['opportunity_id']})
    # More than sixteen anchors for one lane cannot invalidate unrelated lanes.
    anchors.extend(dict(anchors[0]) for _ in range(16))
    path = tmp_path/'v3/ledgers/opportunity.jsonl'
    path.parent.mkdir(parents=True)
    path.write_text(''.join(json.dumps(row)+'\n' for row in contexts))
    first = build_shared_context_coverage(tmp_path, KEY.collection_epoch_id, anchors)
    assert first['eligible_lanes'] == 70 and first['page_lanes'] == 64
    assert first['omitted_from_page'] == 6 and first['bound_lanes'] == 63
    assert first['paginated'] and not first['truncated']
    assert first['cohort_evaluated_lanes'] == 64 and first['cohort_pending_lanes'] == 6
    assert not first['cohort_evaluation_complete']
    assert 'SHARED_CONTEXT_ANCHOR_LIMIT_EXCEEDED' in first['lanes'][0]['blockers']
    second = build_shared_context_coverage(tmp_path, KEY.collection_epoch_id, anchors)
    assert second['page_start'] == 64 and second['page_lanes'] == 6
    assert second['bound_lanes'] == 6 and second['evaluated_through_lane'] == 70
    identities = [row['identity']['episode_id'] for page in (first,second) for row in page['lanes']]
    assert len(set(identities)) == 70
    assert second['counts_scope'] == 'CURRENT_PAGE_ONLY'
    assert second['cohort_evaluated_lanes'] == 70 and second['cohort_bound_lanes'] == 69
    assert second['cohort_pending_lanes'] == 0 and second['cohort_evaluation_complete']
    anchors[1] = {**anchors[1], 'config_signature':'changed-config'}
    changed = build_shared_context_coverage(tmp_path, KEY.collection_epoch_id, anchors)
    assert changed['page_start'] == 0 and changed['cohort_evaluated_lanes'] == 64
    assert changed['cohort_pending_lanes'] == 6 and not changed['cohort_evaluation_complete']


def test_actual_analyzer_report_exports_verified_context_without_qualification(tmp_path, monkeypatch):
    _patch_provenance(monkeypatch)
    lane = {**audit(), 'record_id':'lane', 'opportunity_id':'opportunity:' + KEY.episode_id}
    opportunity = {**PROV, 'ledger':'opportunity', 'record_id':lane['opportunity_id'],
                   'episode_id':KEY.episode_id, 'epoch_id':KEY.collection_epoch_id,
                   'shared_ai_call_id':lane['shared_ai_call_id'], 'signal_ts':100,
                   'event_id':'scan-event', 'symbol':'BTC', 'direction':'LONG'}
    _append(tmp_path, opportunity)
    _append(tmp_path, lane)
    raw = (tmp_path/'v3/ledgers/opportunity.jsonl').read_bytes()
    report = build_safe_policy_genome_v3_report(tmp_path, tmp_path/'reports', candidates=[])
    saved = json.loads((tmp_path/'reports'/REPORT_FILE).read_text())
    coverage = saved['shared_context_coverage']
    assert coverage == report['shared_context_coverage']
    assert coverage['bound_lanes'] == 1
    assert coverage['epoch_id'] == saved['epoch_id'] == KEY.collection_epoch_id
    assert coverage['lanes'][0]['provenance']['source_revision'] == [json.loads(raw)['source_revision']]
    assert coverage['lanes'][0]['references'][0]['row_sha256'] == hashlib.sha256(raw).hexdigest()
    assert 'source_row' not in str(coverage)
    assert not coverage['qualification_authority'] and not coverage['cleanup_authority']
    assert not report['real_bitfinex_trading_allowed']
    stored_lane = json.loads((tmp_path/'v3/ledgers/lifecycle.jsonl').read_text())
    limited = build_shared_context_coverage(tmp_path, KEY.collection_epoch_id, [stored_lane], max_bytes=1)
    assert not limited['truncated'] and limited['bound_lanes'] == 1
    assert limited['rows_scanned'] == 0  # Reuse the complete derived cursor.
    assert (tmp_path/'v3/ledgers/opportunity.jsonl').read_bytes() == raw
    foreign = {**stored_lane, 'epoch_id':'old-epoch'}
    assert build_shared_context_coverage(tmp_path, KEY.collection_epoch_id, [foreign])['lanes'] == []


def test_report_index_advances_past_two_mib_without_repeating_prefix(tmp_path):
    lane = {**audit(), 'opportunity_id':'opportunity:' + KEY.episode_id}
    directory = tmp_path/'v3/ledgers'
    directory.mkdir(parents=True)
    old = {'epoch_id':'old', 'episode_id':'old', 'payload':'x'*750000}
    current = {**PROV, 'epoch_id':KEY.collection_epoch_id, 'episode_id':KEY.episode_id,
               'shared_ai_call_id':lane['shared_ai_call_id'], 'record_id':lane['opportunity_id']}
    with (directory/'opportunity.jsonl').open('w') as stream:
        for row in [old,old,old,current]:
            stream.write(json.dumps(row)+'\n')
    first = build_shared_context_coverage(tmp_path, KEY.collection_epoch_id, [lane])
    assert first['truncated'] and first['bound_lanes'] == 0 and first['rows_scanned'] == 2
    second = build_shared_context_coverage(tmp_path, KEY.collection_epoch_id, [lane])
    assert not second['truncated'] and second['bound_lanes'] == 1 and second['rows_scanned'] == 2
    third = build_shared_context_coverage(tmp_path, KEY.collection_epoch_id, [lane])
    assert third['rows_scanned'] == 0 and third['bound_lanes'] == 1
    with (directory/'opportunity.jsonl').open('a') as stream:
        stream.write('{"incomplete":true}')
    broken = build_shared_context_coverage(tmp_path, KEY.collection_epoch_id, [lane])
    assert broken['bound_lanes'] == 0
    assert 'SHARED_CONTEXT_INDEX_OR_SOURCE_INVALID' in broken['lanes'][0]['blockers']


def test_replaced_source_invalidates_old_refs_and_converges(tmp_path):
    import os
    import sqlite3
    lane = {**audit(), 'opportunity_id':'opportunity:' + KEY.episode_id}
    directory = tmp_path/'v3/ledgers'
    directory.mkdir(parents=True)
    path = directory/'opportunity.jsonl'
    row = {**PROV, 'epoch_id':KEY.collection_epoch_id, 'episode_id':KEY.episode_id,
           'shared_ai_call_id':lane['shared_ai_call_id'], 'record_id':lane['opportunity_id']}
    path.write_text(json.dumps(row)+'\n')
    assert build_shared_context_coverage(tmp_path, KEY.collection_epoch_id, [lane])['bound_lanes'] == 1
    replacement = directory/'replacement.tmp'
    old = {'epoch_id':'old', 'episode_id':'old', 'payload':'x'*750000}
    replacement.write_text(''.join(json.dumps(r)+'\n' for r in [old,old,old,row]))
    os.replace(replacement, path)
    first = build_shared_context_coverage(tmp_path, KEY.collection_epoch_id, [lane])
    assert first['truncated'] and first['bound_lanes'] == 0
    db = sqlite3.connect(tmp_path/'analyzer/shared-context-index.sqlite3')
    assert db.execute('SELECT count(*) FROM refs WHERE episode=?',(KEY.episode_id,)).fetchone()[0] == 0
    db.close()
    assert build_shared_context_coverage(tmp_path, KEY.collection_epoch_id, [lane])['bound_lanes'] == 1


def test_oversized_row_is_explicitly_unsupported(tmp_path):
    lane = {**audit(), 'opportunity_id':'opportunity:' + KEY.episode_id}
    path = tmp_path/'v3/ledgers/opportunity.jsonl'
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({'payload':'x'*(2*1024*1024)})+'\n')
    result = build_shared_context_coverage(tmp_path, KEY.collection_epoch_id, [lane])
    assert result['bound_lanes'] == 0
    assert 'SHARED_SOURCE_ROW_EXCEEDS_SCAN_LIMIT' in result['lanes'][0]['blockers']
