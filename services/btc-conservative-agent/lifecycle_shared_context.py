"""Causal references to original shared rows, never per-lane source rewrites."""
import copy


def attach_shared_context(key, rows, refs):
    if not rows:
        return rows
    fields = ('source_revision', 'deployed_revision', 'config_signature', 'tile_config_signature', 'shared_ai_call_id')
    def epoch(row):
        a, b = row.get('epoch_id'), row.get('collection_epoch_id')
        return None if a and b and a != b else a or b
    def present(value):
        return type(value) is str and value.strip().upper() not in {'', 'UNKNOWN', 'NOT_DEPLOYED_LOCAL'}
    anchors = [r for r in rows if r.get('resolution_scope') == 'LANE_ENTRY']
    bound, blockers = [], []
    if refs and not anchors:
        blockers.append('SHARED_CONTEXT_LANE_ANCHOR_MISSING')
    opportunity_ids = {r.get('opportunity_id') for r in anchors}
    expected = {f: {r.get(f) for r in anchors} for f in fields}
    valid_anchor = bool(anchors) and all(epoch(r) == key.collection_epoch_id and r.get('episode_id') == key.episode_id for r in anchors)
    valid_anchor = valid_anchor and all(len(v) == 1 and present(next(iter(v))) for v in expected.values())
    valid_anchor = valid_anchor and len(opportunity_ids) == 1 and present(next(iter(opportunity_ids)))
    seen = set()
    for ref in refs:
        row = ref['source_row']
        identity = (ref['ledger'], row.get('record_id'))
        if identity in seen:
            blockers.append('SHARED_CONTEXT_AMBIGUOUS')
        seen.add(identity)
        matches = valid_anchor and epoch(row) == key.collection_epoch_id and row.get('episode_id') == key.episode_id
        matches = matches and all(row.get(f) in expected[f] for f in fields)
        matches = matches and (row.get('record_id') in opportunity_ids if ref['ledger'] == 'opportunity' else row.get('opportunity_id') in opportunity_ids)
        if not matches:
            blockers.append('SHARED_CONTEXT_CAUSAL_BINDING_MISMATCH')
        else:
            bound.append(copy.deepcopy(ref))
    output = list(rows)
    output[0] = dict(rows[0])
    output[0]['shared_context_binding'] = {
        'schema': 'lifecycle_shared_context_binding_v1',
        'status': 'BOUND' if bound and not blockers else 'UNBOUND',
        'blockers': sorted(set(blockers)),
        'references': bound if not blockers else [],
    }
    return output
