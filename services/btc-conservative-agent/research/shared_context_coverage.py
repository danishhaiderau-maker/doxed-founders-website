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
                    # This cache is derived, not evidence. A promoted mirror
                    # generation invalidates only this ledger atomically.
                    db.execute('DELETE FROM refs WHERE ledger=?', (ledger,))
                    db.execute('DELETE FROM cursor WHERE ledger=?', (ledger,))
                    db.commit()
                    offset = 0
                stream.seek(offset)
                while byte_budget > 0 and scanned < row_budget:
                    start = stream.tell()
                    raw = stream.readline(byte_budget+1)
                    if not raw:
                        break
                    if len(raw) > byte_budget:
                        if byte_budget == 2*1024*1024:
                            raise ValueError('SHARED_SOURCE_ROW_EXCEEDS_SCAN_LIMIT')
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
    overflow = set()
    for row in anchors:
        if row.get('resolution_scope') != 'LANE_ENTRY':
            continue
        try:
            key = lifecycle_key(row)
        except ValueError:
            continue
        if key.collection_epoch_id != epoch_id:
            continue
        if len(groups[key]) < 16:
            groups[key].append(row)
        else:
            overflow.add(key)
    all_keys = sorted(groups)
    total_lanes = len(all_keys)
    page_start = 0
    page_limit = min(max(1, int(max_lanes)), 64)
    page_keys = all_keys[:page_limit]
    incomplete_episodes = set()
    defects = []
    db = None
    indexed_through_bytes = 0
    try:
        db, scanned, complete = _advance_context_index(root, remaining, row_limit)
        indexed_through_bytes = sum(r[0] for r in db.execute('SELECT offset FROM cursor'))
        truncated = truncated or not complete
        # Pagination is not missing source evidence. Persist the next lane
        # only against this exact anchor cohort and indexed source generation.
        cohort = hashlib.sha256(json.dumps([
            epoch_id, [(k.as_dict(), groups[k], k in overflow) for k in all_keys],
            [tuple(r) for r in db.execute('SELECT * FROM cursor ORDER BY ledger')],
        ], sort_keys=True, default=str).encode()).hexdigest()
        db.execute('CREATE TABLE IF NOT EXISTS coverage_page(id INTEGER PRIMARY KEY,cohort TEXT,next_lane INTEGER)')
        previous = db.execute('SELECT cohort,next_lane FROM coverage_page WHERE id=1').fetchone()
        if previous and previous[0] == cohort and previous[1] < total_lanes:
            page_start = previous[1]
        page_keys = all_keys[page_start:page_start+page_limit]
        query_bytes, query_rows = 0, 0
        for episode in sorted({key.episode_id for key in page_keys}):
            indexed = db.execute('SELECT * FROM refs WHERE epoch=? AND episode=? ORDER BY ledger,offset LIMIT 2001', (epoch_id,episode)).fetchall()
            for ref in indexed:
                query_bytes += ref['length']
                query_rows += 1
                if query_bytes > 2*1024*1024 or query_rows > 2000:
                    incomplete_episodes.add(episode)
                    break
                path = Path(root)/'v3'/'ledgers'/(ref['ledger']+'.jsonl')
                with path.open('rb') as stream:
                    stream.seek(ref['offset'])
                    raw = stream.read(ref['length'])
                if len(raw) != ref['length'] or hashlib.sha256(raw).hexdigest() != ref['sha']:
                    raise ValueError('SHARED_CONTEXT_SOURCE_CHANGED')
                references[episode].append({'ledger':ref['ledger'], 'byte_offset':ref['offset'],
                    'row_length':ref['length'], 'row_sha256':ref['sha'], 'source_row':json.loads(raw)})
    except (ValueError, OSError, sqlite3.Error) as exc:
        defects.append('SHARED_SOURCE_ROW_EXCEEDS_SCAN_LIMIT' if str(exc) == 'SHARED_SOURCE_ROW_EXCEEDS_SCAN_LIMIT'
                       else 'SHARED_CONTEXT_INDEX_OR_SOURCE_INVALID')
    finally:
        if db is not None:
            db.close()
    lanes = []
    for key in page_keys:
        rows = groups[key]
        binding = attach_shared_context(key, rows, references.get(key.episode_id, []))[0]['shared_context_binding']
        # A partial scan cannot exclude a later conflicting shared reference.
        local_blockers = (['SHARED_CONTEXT_ANCHOR_LIMIT_EXCEEDED'] if key in overflow else [])
        if key.episode_id in incomplete_episodes:
            local_blockers.append('SHARED_CONTEXT_QUERY_LIMIT_EXCEEDED')
        usable = binding['status'] == 'BOUND' and not truncated and not defects and not local_blockers
        provenance = {field: sorted({str(row.get(field) or '') for row in rows}) for field in (
            'source_revision', 'deployed_revision', 'config_signature', 'tile_config_signature')}
        lanes.append({'identity': key.as_dict(), 'provenance': provenance, 'status': 'BOUND' if usable else 'UNBOUND',
                      'blockers': sorted(set(binding['blockers'] + (['SHARED_CONTEXT_MISSING'] if not references.get(key.episode_id) else []) + (['SHARED_CONTEXT_SCAN_INCOMPLETE'] if truncated else []) + defects + local_blockers)),
                      'references': [{k: ref[k] for k in ('ledger','byte_offset','row_length','row_sha256')}
                                     for ref in binding['references']] if usable else []})
    evaluated_lanes = bound_total = 0
    if not truncated and not defects:
        # Retain only sanitized verdicts for this exact generation. A new
        # source/anchor cohort invalidates prior counts rather than mixing them.
        with sqlite3.connect(Path(root)/'analyzer/shared-context-index.sqlite3', timeout=.1) as results:
            results.execute('CREATE TABLE IF NOT EXISTS coverage_result(cohort TEXT,lane TEXT,status TEXT,PRIMARY KEY(cohort,lane))')
            results.execute('DELETE FROM coverage_result WHERE cohort<>?', (cohort,))
            for lane in lanes:
                results.execute('INSERT OR REPLACE INTO coverage_result VALUES(?,?,?)',
                    (cohort,json.dumps(lane['identity'],sort_keys=True),lane['status']))
            results.execute('INSERT OR REPLACE INTO coverage_page VALUES(1,?,?)', (cohort,page_start+len(page_keys)))
            evaluated_lanes, bound_total = results.execute(
                "SELECT count(*),coalesce(sum(status='BOUND'),0) FROM coverage_result WHERE cohort=?", (cohort,)
            ).fetchone()
    return {'schema':'shared_context_coverage_v1', 'epoch_id':epoch_id, 'qualification_authority':False,
            'cleanup_authority':False, 'truncated':truncated, 'rows_scanned':scanned,
            'indexed_through_bytes':indexed_through_bytes,
            'eligible_lanes':total_lanes, 'page_start':page_start,
            'page_lanes':len(lanes), 'omitted_from_page':total_lanes-len(lanes),
            'evaluated_through_lane':page_start+len(lanes),
            'paginated':total_lanes>len(lanes), 'counts_scope':'CURRENT_PAGE_ONLY',
            'cohort_evaluated_lanes':evaluated_lanes, 'cohort_bound_lanes':bound_total,
            'cohort_pending_lanes':total_lanes-evaluated_lanes,
            'cohort_evaluation_complete':not truncated and not defects and evaluated_lanes==total_lanes,
            'bound_lanes':sum(r['status']=='BOUND' for r in lanes), 'lanes':lanes}
