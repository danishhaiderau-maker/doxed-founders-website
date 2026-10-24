"""CI: the Fly image's .dockerignore context must contain, and be able to import, every module bot.py imports.

Fly deploy run 40063653539 (24 Oct 2026) failed at image build because .dockerignore re-included only some
research/*.py files and research/r3_quarantine.py (imported by the analyzer) was dropped. No PR check built the
image context, so it shipped. This check:

1. walks the imports of bot.py and every process fly-entrypoint.sh starts TRANSITIVELY over local modules (static AST: every import / from-import at any depth,
   plus importlib.import_module("<literal>") / __import__("<literal>")), resolved against the source dir;
2. fails if any reachable local module is missing from the image context dir (built by docker with the real
   .dockerignore, see unit-tests.yml job bot-image-imports);
3. imports every reachable local module inside the context (one subprocess each, cwd=context, sys.path = context
   + site-packages only) and fails on ModuleNotFoundError / ImportError for a LOCAL module.

Usage: python ci_image_import_check.py --src . --ctx /tmp/bot-image-ctx [--no-import] [--entry bot ...]
(default entries: bot + every `python /app/<x>.py` process fly-entrypoint.sh starts, e.g. the analyzer)
"""
from __future__ import annotations

import argparse
import ast
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def _module_file(root: Path, mod: str):
    p = root.joinpath(*mod.split("."))
    for cand in (p.with_suffix(".py"), p / "__init__.py"):
        if cand.is_file():
            return cand
    return None


def _guarded_by_import_error(handlers) -> bool:
    for h in handlers:
        t = h.type
        names = [] if t is None else ([t] if not isinstance(t, ast.Tuple) else list(t.elts))
        if t is None or any(getattr(x, "id", getattr(x, "attr", "")) in ("ImportError", "ModuleNotFoundError",
                                                                         "Exception", "BaseException") for x in names):
            return True
    return False


def _imports(path: Path) -> tuple:
    """(hard, optional) imported module names.

    hard     = executed at module import time and NOT inside try/except ImportError (a missing one crashes the
               process at startup, exactly like run 40063653539);
    optional = inside a function/method body or an ImportError-guarded try (degrades, does not crash import)."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"), filename=str(path))
    except SyntaxError:
        return set(), set()
    hard, opt = set(), set()

    def names_of(n):
        out = set()
        if isinstance(n, ast.Import):
            out |= {a.name for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.level == 0 and n.module:
            out.add(n.module)
            out |= {f"{n.module}.{a.name}" for a in n.names}
        elif isinstance(n, ast.Call) and n.args and isinstance(n.args[0], ast.Constant) \
                and isinstance(n.args[0].value, str):
            f = n.func
            if (f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")) in ("import_module", "__import__"):
                out.add(n.args[0].value)
        return out

    def visit(node, optional):
        for child in ast.iter_child_nodes(node):
            child_opt = optional
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                child_opt = True
            elif isinstance(child, ast.Try) and _guarded_by_import_error(child.handlers):
                for b in child.body:
                    (opt if True else hard).update(names_of(b))
                    visit(b, True)
                for part in (child.handlers, child.orelse, child.finalbody):
                    for b in part:
                        (opt if optional else hard).update(names_of(b))
                        visit(b, optional)
                continue
            (opt if child_opt else hard).update(names_of(child))
            visit(child, child_opt)

    visit(tree, False)
    return hard, opt - hard


def entrypoint_scripts(src: Path) -> list:
    """bot plus every `python /app/<x>.py` process fly-entrypoint.sh starts (analyzer, collectors, ...)."""
    import re
    out = ["bot"]
    ep = src / "fly-entrypoint.sh"
    if ep.is_file():
        for m in re.findall(r"python3?\s+(?:-u\s+)?/app/([A-Za-z0-9_]+)\.py", ep.read_text(errors="replace")):
            if m not in out:
                out.append(m)
    return out


def closure(src: Path, entries=("bot",)) -> tuple:
    """({hard-reachable local module: file}, {optional-only local module: file}) from the entries.

    hard edges are followed transitively from the entries; optional edges are reported (warn) but not followed."""
    if isinstance(entries, str):
        entries = [entries]
    seen, optional, todo = {}, {}, list(entries)
    while todo:
        mod = todo.pop()
        if mod in seen:
            continue
        f = _module_file(src, mod)
        if f is None:
            continue
        seen[mod] = f.relative_to(src)
        parts = mod.split(".")
        for i in range(1, len(parts)):  # parent packages must ship too
            todo.append(".".join(parts[:i]))
        hard, opt = _imports(f)
        todo.extend(hard)
        for o in opt:
            of = _module_file(src, o)
            if of is not None:
                optional[o] = of.relative_to(src)
    return seen, {m: p for m, p in optional.items() if m not in seen}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=".")
    ap.add_argument("--ctx", required=True)
    ap.add_argument("--entry", action="append", help="default: bot + every script fly-entrypoint.sh runs")
    ap.add_argument("--no-import", action="store_true")
    ap.add_argument("--timeout", type=float, default=180.0)
    a = ap.parse_args(argv)
    src, ctx = Path(a.src).resolve(), Path(a.ctx).resolve()
    entries = a.entry or entrypoint_scripts(src)
    mods, optional = closure(src, entries)
    if any(e not in mods for e in entries):
        print(f"FAIL: entry not found in {src}: {[e for e in entries if e not in mods]}")
        return 2
    missing = sorted(f"{m} ({p})" for m, p in mods.items() if not (ctx / p).is_file())
    print(f"entries {entries}\nreachable local modules: {len(mods)}; missing from image context: {len(missing)}")
    for m in missing:
        print(f"  MISSING_FROM_IMAGE_CONTEXT {m}")
    opt_missing = sorted(f"{m} ({p})" for m, p in optional.items() if not (ctx / p).is_file())
    print(f"optional (function-level / ImportError-guarded) local imports missing from context: {len(opt_missing)} (warn only)")
    for m in opt_missing:
        print(f"  WARN_OPTIONAL_NOT_IN_IMAGE {m}")
    bad = []
    if not a.no_import:
        data = tempfile.mkdtemp(prefix="bot-import-data-")
        env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH",)}
        env.update(BOT_DATA_DIR=data, PYTHONDONTWRITEBYTECODE="1", PYTHONPATH=str(ctx))
        local_roots = {m.split(".")[0] for m in mods}
        for m in sorted(mods):
            if m in entries:
                continue  # entry scripts start processes; their (transitive) imports are what is checked
            code = ("import importlib,sys\n"
                    f"sys.path=[p for p in sys.path if p not in ('', {str(src)!r})]\n"
                    "try:\n"
                    f"    importlib.import_module({m!r})\n"
                    "except ImportError as e:\n"
                    "    n=(getattr(e,'name',None) or '').split('.')[0]\n"
                    f"    print('IMPORT_ERROR', n, repr(e)); sys.exit(3 if n in {sorted(local_roots)!r} else 0)\n"
                    "except BaseException as e:\n"
                    "    print('NON_IMPORT_ERROR', type(e).__name__, str(e)[:200]); sys.exit(0)\n")
            try:
                r = subprocess.run([sys.executable, "-c", code], cwd=ctx, env=env, capture_output=True, text=True,
                                   timeout=a.timeout)
            except subprocess.TimeoutExpired:
                print(f"  TIMEOUT {m} (not counted as missing)")
                continue
            tail = (r.stdout + r.stderr).strip().splitlines()[-1:] if (r.stdout or r.stderr) else []
            if r.returncode == 3:
                bad.append(m)
                print(f"  LOCAL_IMPORT_FAILED {m}: {tail}")
            elif r.returncode != 0:
                print(f"  NOTE {m} exit {r.returncode}: {tail}")
        print(f"imported {len(mods) - 1} modules in image context; local import failures: {len(bad)}")
    if missing or bad:
        print("FAIL: a module bot.py needs is not in the Fly image context. Re-include it in .dockerignore.")
        return 1
    print("OK: every module bot.py imports is in the image context and imports there.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
