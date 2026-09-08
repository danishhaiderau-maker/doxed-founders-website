import hashlib
import json
from test_lifecycle_pipeline import _append, _patch_provenance
from test_lane_no_order_disposition import audit, KEY, PROV
from research.research_v3_report import build_safe_policy_genome_v3_report, REPORT_FILE
from research.shared_context_coverage import build_shared_context_coverage


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
    assert coverage['lanes'][0]['provenance']['source_revision'] == [PROV['source_revision']]
    assert coverage['lanes'][0]['references'][0]['row_sha256'] == hashlib.sha256(raw).hexdigest()
    assert 'source_row' not in str(coverage)
    assert not coverage['qualification_authority'] and not coverage['cleanup_authority']
    assert not report['real_bitfinex_trading_allowed']
    stored_lane = json.loads((tmp_path/'v3/ledgers/lifecycle.jsonl').read_text())
    limited = build_shared_context_coverage(tmp_path, KEY.collection_epoch_id, [stored_lane], max_bytes=1)
    assert limited['truncated'] and limited['bound_lanes'] == 0
    assert limited['lanes'][0]['references'] == []
    assert (tmp_path/'v3/ledgers/opportunity.jsonl').read_bytes() == raw
    foreign = {**stored_lane, 'epoch_id':'old-epoch'}
    assert build_shared_context_coverage(tmp_path, KEY.collection_epoch_id, [foreign])['lanes'] == []
