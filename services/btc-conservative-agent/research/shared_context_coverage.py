"""Bounded original-byte shared-context coverage for analyzer report/export."""
import hashlib
import json
import sqlite3
from collections import defaultdict
from pathlib import Path
from lifecycle_bundles import lifecycle_key
from lifecycle_shared_context import attach_shared_context


def _advance_context_index(root, byte_budget, row_budget):
    """Local derived index, independent of the collector's lifecycle DB."""
    root = Path(root)
    cache = root / 'analyzer' / 'shared-context-index.sqlite3'
    cache.parent.mkdir(parents=True, exist_ok=True)
    if cache.is_symlink() or (cache.exists() and cache.stat().st_size > 256*1024*1024):
        raise ValueError('SHARED_CONTEXT_INDEX_UNSAFE_OR_TOO_LARGE')
    db = sqlite3.connect(cache, timeout=.1)
    db.row_factory = sqlite3.Row
    scanned = 0
    try:
        db.executescript('''CREATE TABLE IF NOT EXISTS cursor(ledger TEXT PRIMARY KEY,dev INTEGER,ino INTEGER,offset INTEGER,anchor TEXT);
        CREATE TABLE IF NOT EXISTS refs(ledger TEXT,offset INTEGER,length INTEGER,sha TEXT,epoch TEXT,episode TEXT,PRIMARY KEY(ledger,offset));
        CREATE INDEX IF NOT EXISTS refs_lookup ON refs(epoch,episode);''')
        for ledger in ('opportunity', 'market_segment'):
            path = root / 'v3' / 'ledgers' / (ledger + '.jsonl')
            if not path.exists():
                continue
            if path.is_symlink():
                raise ValueError('SHARED_SOURCE_LINK_REJECTED')
            stat = path.stat()
            previous = db.execute('SELECT * FROM cursor WHERE ledger=?', (ledger,)).fetchone()
            offset = previous['offset'] if previous else 0
            with path.open('rb') as stream:
                stream.seek(max(0, offset-4096))
                anchor = hashlib.sha256(stream.read(min(offset,4096))).hexdigest()
                if previous and (stat.st_dev != previous['dev'] or stat.st_ino != previous['ino'] or stat.st_size < offset or anchor != previous['anchor']):
                    raise ValueError('SHARED_SOURCE_CHANGED_REBUILD_REQUIRED')
                stream.seek(offset)
                while byte_budget > 0 and scanned < row_budget:
                    start = stream.tell()
                    raw = stream.readline(byte_budget+1)
                    if not raw:
                        break
                    if len(raw) > byte_budget:
                        break
                    if not raw.endswith(b'\n'):
                        raise ValueError('SHARED_SOURCE_UNTERMINATED_ROW')
                    row = json.loads(raw)
                    if type(row) is not dict:
                        raise ValueError('SHARED_SOURCE_INVALID_ROW')
                    byte_budget -= len(raw)
                    scanned += 1
                    offset = stream.tell()
                    if not row.get('policy_signature') and not row.get('research_lane'):
                        db.execute('INSERT OR IGNORE INTO refs VALUES(?,?,?,?,?,?)',
                            (ledger,start,len(raw),hashlib.sha256(raw).hexdigest(),
                             str(row.get('epoch_id') or row.get('collection_epoch_id') or ''),str(row.get('episode_id') or '')))
                stream.seek(max(0,offset-4096))
                anchor = hashlib.sha256(stream.read(min(offset,4096))).hexdigest()
            db.execute('INSERT OR REPLACE INTO cursor VALUES(?,?,?,?,?)',(ledger,stat.st_dev,stat.st_ino,offset,anchor))
        db.commit()
        complete = True
        for ledger in ('opportunity','market_segment'):
            path = root/'v3'/'ledgers'/(ledger+'.jsonl')
            if path.exists():
                cursor = db.execute('SELECT offset FROM cursor WHERE ledger=?',(ledger,)).fetchone()
                complete = complete and cursor is not None and cursor[0] == path.stat().st_size
        return db, scanned, complete
    except BaseException:
        db.rollback()
        db.close()
        raise


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
    db = None
    indexed_through_bytes = 0
    try:
        db, scanned, complete = _advance_context_index(root, remaining, row_limit)
        indexed_through_bytes = sum(r[0] for r in db.execute('SELECT offset FROM cursor'))
        truncated = truncated or not complete
        query_bytes, query_rows = 0, 0
        for episode in {key.episode_id for key in groups}:
            indexed = db.execute('SELECT * FROM refs WHERE epoch=? AND episode=? ORDER BY ledger,offset LIMIT 2001', (epoch_id,episode)).fetchall()
            for ref in indexed:
                query_bytes += ref['length']
                query_rows += 1
                if query_bytes > 2*1024*1024 or query_rows > 2000:
                    truncated = True
                    break
                path = Path(root)/'v3'/'ledgers'/(ref['ledger']+'.jsonl')
                with path.open('rb') as stream:
                    stream.seek(ref['offset'])
                    raw = stream.read(ref['length'])
                if len(raw) != ref['length'] or hashlib.sha256(raw).hexdigest() != ref['sha']:
                    raise ValueError('SHARED_CONTEXT_SOURCE_CHANGED')
                references[episode].append({'ledger':ref['ledger'], 'byte_offset':ref['offset'],
                    'row_length':ref['length'], 'row_sha256':ref['sha'], 'source_row':json.loads(raw)})
    except (ValueError, OSError, sqlite3.Error):
        defects.append('SHARED_CONTEXT_INDEX_OR_SOURCE_INVALID')
    finally:
        if db is not None:
            db.close()
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
            'indexed_through_bytes':indexed_through_bytes,
            'bound_lanes':sum(r['status']=='BOUND' for r in lanes), 'lanes':lanes}
