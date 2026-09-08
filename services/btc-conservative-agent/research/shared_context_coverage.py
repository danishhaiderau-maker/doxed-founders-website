"""Bounded original-byte shared-context coverage for analyzer report/export."""
import hashlib
import json
from collections import defaultdict
from pathlib import Path
from lifecycle_bundles import lifecycle_key
from lifecycle_shared_context import attach_shared_context


def build_shared_context_coverage(root, epoch_id, anchors, *, max_bytes=2*1024*1024, max_rows=2000, max_lanes=64):
    remaining = min(max(0, int(max_bytes)), 2*1024*1024)
    row_limit = min(max(0, int(max_rows)), 2000)
    groups, references = defaultdict(list), defaultdict(list)
    truncated, scanned = False, 0
    for row in anchors:
        if row.get('resolution_scope') != 'LANE_ENTRY':
            continue
        try:
            key = lifecycle_key(row)
        except ValueError:
            continue
        if key.collection_epoch_id != epoch_id:
            continue
        if key not in groups and len(groups) >= min(max_lanes, 64):
            truncated = True
            continue
        if len(groups[key]) < 16:
            groups[key].append(row)
        else:
            truncated = True
    defects = []
    for ledger in ('opportunity', 'market_segment'):
        path = Path(root) / 'v3' / 'ledgers' / (ledger + '.jsonl')
        if not path.exists():
            continue
        if path.is_symlink():
            defects.append('SHARED_SOURCE_LINK_REJECTED')
            continue
        with path.open('rb') as handle:
            while remaining > 0 and scanned < row_limit:
                offset = handle.tell()
                raw = handle.readline(remaining + 1)
                if not raw:
                    break
                if len(raw) > remaining or not raw.endswith(b'\n'):
                    truncated = True
                    break
                remaining -= len(raw)
                scanned += 1
                try:
                    row = json.loads(raw)
                except ValueError:
                    defects.append('SHARED_SOURCE_INVALID_JSON')
                    continue
                if type(row) is not dict:
                    defects.append('SHARED_SOURCE_INVALID_ROW')
                    continue
                if row.get('policy_signature') or row.get('research_lane'):
                    continue
                if (row.get('epoch_id') or row.get('collection_epoch_id')) != epoch_id:
                    continue
                references[str(row.get('episode_id') or '')].append({
                    'ledger': ledger, 'byte_offset': offset, 'row_length': len(raw),
                    'row_sha256': hashlib.sha256(raw).hexdigest(), 'source_row': row})
            if handle.read(1):
                truncated = True
    lanes = []
    for key, rows in groups.items():
        binding = attach_shared_context(key, rows, references.get(key.episode_id, []))[0]['shared_context_binding']
        # A partial scan cannot exclude a later conflicting shared reference.
        usable = binding['status'] == 'BOUND' and not truncated and not defects
        provenance = {field: sorted({str(row.get(field) or '') for row in rows}) for field in (
            'source_revision', 'deployed_revision', 'config_signature', 'tile_config_signature')}
        lanes.append({'identity': key.as_dict(), 'provenance': provenance, 'status': 'BOUND' if usable else 'UNBOUND',
                      'blockers': sorted(set(binding['blockers'] + (['SHARED_CONTEXT_MISSING'] if not references.get(key.episode_id) else []) + (['SHARED_CONTEXT_SCAN_INCOMPLETE'] if truncated else []) + defects)),
                      'references': [{k: ref[k] for k in ('ledger','byte_offset','row_length','row_sha256')}
                                     for ref in binding['references']] if usable else []})
    return {'schema':'shared_context_coverage_v1', 'epoch_id':epoch_id, 'qualification_authority':False,
            'cleanup_authority':False, 'truncated':truncated, 'rows_scanned':scanned,
            'bound_lanes':sum(r['status']=='BOUND' for r in lanes), 'lanes':lanes}
