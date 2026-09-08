import ast
import hashlib
import json
from pathlib import Path
import pytest


def test_actual_provider_preserves_signature_without_epoch_or_registry_reads():
    tree=ast.parse(Path(__file__).with_name('bot.py').read_text(encoding='utf-8'))
    functions=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in {
        '_current_collection_config_signature','_data_sync_lifecycle_cleanup_current_identity'}]
    registration=next(n for n in ast.walk(tree) if isinstance(n,ast.Expr)
        and isinstance(n.value,ast.Call) and isinstance(n.value.func,ast.Name)
        and n.value.func.id=='set_collection_config_signature_provider')
    providers=[]
    state={'b':2,'a':False,'unrelated':'ignored'}
    def forbidden(): pytest.fail('provider read unrelated identity')
    ns=dict(state=state,hashlib=hashlib,json=json,_persistent_config_keys=lambda:['b','missing','a'],
            _collector_v22_epoch_id=forbidden,active_tile_registry_signature=forbidden,
            _runtime_git_rev=forbidden,set_collection_config_signature_provider=providers.append)
    exec(compile(ast.Module(body=functions+[registration],type_ignores=[]),'<actual-provider>','exec'),ns)
    def legacy():
        material={k:state.get(k) for k in sorted(ns['_persistent_config_keys']()) if k in state}
        return hashlib.sha256(json.dumps(material,separators=(',',':'),sort_keys=True).encode('utf-8')).hexdigest()
    provider=providers[0]
    initial=provider()
    assert initial==legacy()
    state['unrelated']='changed'
    assert provider()==initial
    state['a']=True
    assert provider()==legacy() and provider()!=initial
    ns.update(_collector_v22_epoch_id=lambda:'epoch',active_tile_registry_signature=lambda:'registry',
              _runtime_git_rev=lambda:'revision')
    assert ns['_data_sync_lifecycle_cleanup_current_identity']()=={
        'source_git_rev':'revision','deployed_git_rev':'revision','collection_epoch_id':'epoch',
        'tile_registry_signature':'registry','config_signature':provider()}
