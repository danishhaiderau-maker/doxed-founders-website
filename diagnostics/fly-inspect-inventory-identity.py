from pathlib import Path
import json, os
root = Path('/app/data/runtime')
# find generation metadata for cc9a26ab / 7b2d8f7e
hits = []
for p in root.rglob('*'):
    name = p.name.lower()
    if 'cc9a26ab' in name or '7b2d8f7e' in name or name in {'sync_inventory_current.json','inventory-result.json'}:
        hits.append(str(p))
print('HITS', hits[:40])
for rel in ['sync_inventory_current.json', '.data-sync-snapshots/sync_inventory_current.json']:
    p = root / rel
    if p.is_file():
        d = json.loads(p.read_text(encoding='utf-8'))
        gen = d.get('generation') if isinstance(d.get('generation'), dict) else d
        print(json.dumps({'path':str(p),'status':d.get('status'),'generation_id':(gen or {}).get('generation_id') if isinstance(gen,dict) else d.get('generation_id'),'bundle_identity':(gen or {}).get('bundle_identity') if isinstance(gen,dict) else d.get('bundle_identity'),'keys':sorted(list(d.keys())[:30])},sort_keys=True))
# research session
sp = root / 'research_session.json'
print('SESSION', sp.read_text(encoding='utf-8')[:500] if sp.is_file() else 'missing')
