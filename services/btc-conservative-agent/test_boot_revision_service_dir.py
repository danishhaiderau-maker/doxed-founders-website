"""Regression: laptop LOCAL_FRESH start must not NameError on ``_SERVICE_DIR``.

Observed at master ``bd415e9d4901``: sealed ``start-local-paper-bot`` died inside
``_read_boot_revision`` (~L73) when ``ensure_series_break`` asked for
``source_git_rev`` — ``_SERVICE_DIR`` was only assigned after that call.
"""
from __future__ import annotations

import ast
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path

import local_fresh_runtime as lfr

ENTRY = Path(__file__).resolve().parent / "btc_conservative_agent.py"
ENTRY_TEXT = ENTRY.read_text(encoding="utf-8")


def _load_read_boot_revision(*, bind_service_dir: bool = True):
    """Exec the real ``_read_boot_revision`` without importing the full entrypoint."""
    tree = ast.parse(ENTRY_TEXT, filename=str(ENTRY))
    fn_node = next(
        n
        for n in tree.body
        if isinstance(n, ast.FunctionDef) and n.name == "_read_boot_revision"
    )
    module = types.ModuleType("_boot_revision_service_dir_probe")
    module.__file__ = str(ENTRY)
    module.os = os
    module.sys = sys
    module.Path = Path
    if bind_service_dir:
        module._SERVICE_DIR = os.path.dirname(str(ENTRY))
    code = compile(ast.Module(body=[fn_node], type_ignores=[]), str(ENTRY), "exec")
    exec(code, module.__dict__)
    return module._read_boot_revision


class TestBootRevisionServiceDir(unittest.TestCase):
    def test_service_dir_assigned_before_boot_revision_use(self):
        assign = ENTRY_TEXT.index(
            "_SERVICE_DIR = os.path.dirname(os.path.abspath(__file__))"
        )
        use = ENTRY_TEXT.index("Path(_SERVICE_DIR)")
        series_call = ENTRY_TEXT.index(
            "ensure_series_break(_data_dir, source_git_rev=_read_boot_revision())"
        )
        self.assertLess(assign, use)
        self.assertLess(assign, series_call)
        self.assertEqual(
            ENTRY_TEXT.count(
                "_SERVICE_DIR = os.path.dirname(os.path.abspath(__file__))"
            ),
            1,
        )
        # Must sit above the LOCAL_FRESH gate (the crash path).
        self.assertLess(assign, ENTRY_TEXT.index("if _LOCAL_FRESH:"))

    def test_read_boot_revision_without_env_no_nameerror(self):
        # Clear SOURCE_GIT_REV so the helper walks git via ``_SERVICE_DIR``.
        env_had = "SOURCE_GIT_REV" in os.environ
        old = os.environ.pop("SOURCE_GIT_REV", None)
        try:
            read_boot = _load_read_boot_revision(bind_service_dir=True)
            rev = read_boot()
            self.assertIsInstance(rev, str)
            self.assertGreaterEqual(len(rev), 1)
            self.assertLessEqual(len(rev), 12)
        finally:
            if env_had and old is not None:
                os.environ["SOURCE_GIT_REV"] = old

        # Mutation proof: missing ``_SERVICE_DIR`` reproduces the sealed-start crash.
        broken = _load_read_boot_revision(bind_service_dir=False)
        with self.assertRaises(NameError) as ctx:
            broken()
        self.assertIn("_SERVICE_DIR", str(ctx.exception))

    def test_ensure_series_break_accepts_boot_revision(self):
        env_had = "SOURCE_GIT_REV" in os.environ
        old = os.environ.pop("SOURCE_GIT_REV", None)
        try:
            read_boot = _load_read_boot_revision(bind_service_dir=True)
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                series = lfr.ensure_series_break(
                    root, now=1_700_000_000.0, source_git_rev=read_boot()
                )
                self.assertTrue((root / "series_break.json").is_file())
                self.assertIn("series_break_ts", series)
        finally:
            if env_had and old is not None:
                os.environ["SOURCE_GIT_REV"] = old


if __name__ == "__main__":
    unittest.main()
