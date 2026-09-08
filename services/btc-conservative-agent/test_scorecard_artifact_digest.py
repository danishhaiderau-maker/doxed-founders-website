import ast
import hashlib
from pathlib import Path


def test_actual_digest_matches_bytes_and_manifest_wires_it(tmp_path):
    source = Path('analyzer_research_engine_v62.py').read_text(encoding='utf-8-sig')
    tree = ast.parse(source)
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == '_report_artifact_digest')
    scope = {}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), 'actual-digest', 'exec'), scope)
    path = tmp_path / 'report.json'
    for raw in (b'', b'{"value":1}\r\n', b'x' * (1024 * 1024 + 9)):
        path.write_bytes(raw)
        assert scope['_report_artifact_digest'](path) == hashlib.sha256(raw).hexdigest()
    entries = [n for n in ast.walk(tree) if isinstance(n, ast.Dict)
               and any(isinstance(v, ast.Name) and v.id == 'DISCOVERY_COHORT_SCORECARD_REPORT_FILE' for v in n.values)]
    assert any(any(isinstance(k, ast.Constant) and k.value == 'artifact_sha256'
                   and isinstance(v, ast.Call) and isinstance(v.func, ast.Name)
                   and v.func.id == '_report_artifact_digest'
                   for k, v in zip(n.keys, n.values)) for n in entries)
