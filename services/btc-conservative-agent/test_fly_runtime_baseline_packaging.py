"""Exercise the pre-AI import using only explicitly shipped research files."""
from pathlib import Path
import ast
import shutil
import subprocess
import sys


ROOT = Path(__file__).parent


def test_pre_ai_baseline_import_in_allowlisted_source_tree(tmp_path):
    rules = (ROOT / '.dockerignore').read_text(encoding='utf-8').splitlines()
    required = '!research/runtime_baseline_declaration.py'
    assert required in rules
    assert rules.index(required) > rules.index('research/*')
    package = tmp_path / 'research'
    package.mkdir()
    for rule in rules:
        if rule.startswith('!research/') and rule.endswith('.py'):
            relative = rule[1:]
            target = tmp_path / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / relative, target)
    # -I prevents the checkout/PYTHONPATH from hiding a missing image dependency.
    result = subprocess.run([
        sys.executable, '-I', '-c',
        'import sys; sys.path.insert(0, sys.argv[1]); '
        'from research.runtime_baseline_declaration import build_runtime_baseline_declaration; '
        'import research.scan_counterfactual_unavailable, research.reset_writer_barrier; '
        'assert callable(build_runtime_baseline_declaration)', str(tmp_path),
    ], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


def test_image_build_smokes_pre_ai_import():
    dockerfile = (ROOT / 'Dockerfile').read_text(encoding='utf-8')
    assert 'RUN python -c "import research.runtime_baseline_declaration"' in dockerfile
    assert 'RUN python -c "import research.scan_counterfactual_unavailable, research.reset_writer_barrier"' in dockerfile


def test_runtime_research_import_closure_is_shipped():
    rules = (ROOT / '.dockerignore').read_text(encoding='utf-8').splitlines()
    pending = ['bot', 'research_v3_bridge', 'research_timing_capture']
    seen = set()
    while pending:
        module = pending.pop()
        if module in seen:
            continue
        seen.add(module)
        path = ROOT.joinpath(*module.split('.')).with_suffix('.py')
        if not path.exists():
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
            names = ([node.module] if isinstance(node, ast.ImportFrom) and node.module
                     else [name.name for name in node.names] if isinstance(node, ast.Import) else [])
            for name in names:
                # Deliberately optional desktop module: runtime has a dedicated,
                # tested local sealing fallback (test_seal_past_analysis_fallback).
                if name == 'research.past_analysis':
                    continue
                if name.startswith('research.'):
                    assert '!' + name.replace('.', '/') + '.py' in rules, name
                if ROOT.joinpath(*name.split('.')).with_suffix('.py').exists():
                    pending.append(name)
