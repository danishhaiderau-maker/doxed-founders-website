"""Canary absence, forbidden-operation guards, and call-site proof."""

from __future__ import annotations

import ast
import traceback
import unittest
import zlib

from research.coverage_audit.guards import AuditStop, forbid
from research.coverage_audit.parsers import Fingerprint, _parse_gzip
from research.coverage_audit.tests.test_synthetic_formats import (
    ROOT,
    _gzip,
    _profile,
    registry_bytes,
    run_group,
)
from research.coverage_audit.guards import FixedMeter, Ledger

_IMPL = (
    "__init__.py",
    "auditor.py",
    "cadence.py",
    "cli.py",
    "guards.py",
    "launch_audit.py",
    "parsers.py",
)
_BANNED_MODULES = {
    "socket",
    "urllib",
    "http",
    "requests",
    "subprocess",
    "tempfile",
    "shutil",
    "pickle",
    "ctypes",
    "multiprocessing",
    "webbrowser",
    "logging",
}
_BANNED_ATTRS = {
    "environ",
    "extract",
    "extractall",
    "chmod",
    "remove",
    "unlink",
    "rename",
    "utime",
    "rmtree",
    "getenv",
}


class RedactionTests(unittest.TestCase):
    def test_probe_failures_do_not_read(self):
        run_group(self, "redaction")

    def test_forbid_discards_detail_and_fails_closed(self):
        expected = {
            "env_read": "ENV_READ_FORBIDDEN",
            "network": "NETWORK_FORBIDDEN",
            "http": "NETWORK_FORBIDDEN",
            "vault_read": "VAULT_READ_FORBIDDEN",
            "chmod": "SOURCE_MUTATION_FORBIDDEN",
            "utime": "SOURCE_MUTATION_FORBIDDEN",
            "rename": "SOURCE_MUTATION_FORBIDDEN",
            "delete": "SOURCE_MUTATION_FORBIDDEN",
            "cleanup": "SOURCE_MUTATION_FORBIDDEN",
            "unlink": "SOURCE_MUTATION_FORBIDDEN",
            "write_source": "SOURCE_MUTATION_FORBIDDEN",
            "truncate": "SOURCE_MUTATION_FORBIDDEN",
        }
        for op, code in expected.items():
            with self.assertRaises(AuditStop) as caught:
                forbid(op, "SECRET_CANARY")
            self.assertEqual(caught.exception.code, code)
            self.assertNotIn("SECRET", str(caught.exception))
            text = "".join(traceback.format_exception(caught.exception))
            self.assertNotIn("SECRET_CANARY", text)

    def test_decompressor_exception_text_is_not_retained(self):
        payload = b"event_ts,note\n1700000000,alpha\n"
        blob = _gzip(payload)
        real = zlib.decompressobj

        def boom(*_args, **_kwargs):
            raise RuntimeError("SECRET_CANARY_EXC")

        zlib.decompressobj = boom
        pin = [{"kind": "gzip_member", "member_id": "g0", "sha256": "0" * 64}]
        try:
            with self.assertRaises(AuditStop) as caught:
                _parse_gzip(blob, {}, pin, Ledger(_profile(), FixedMeter(0, 0)), None, Fingerprint())
        finally:
            zlib.decompressobj = real
        self.assertEqual(caught.exception.code, "GZIP_CORRUPT")
        formatted = traceback.TracebackException.from_exception(caught.exception, capture_locals=True)
        rendered = "".join(formatted.format())
        self.assertNotIn("SECRET_CANARY_EXC", rendered)
        self.assertIn("GZIP_CORRUPT", str(caught.exception))
        for frame in formatted.stack:
            if "parsers.py" in frame.filename and frame.locals:
                self.assertNotIn("SECRET_CANARY_EXC", " ".join(str(value) for value in frame.locals.values()))

    def test_implementation_call_sites_are_closed(self):
        for name in _IMPL:
            path = ROOT / name
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=name)
            visitor = _CallVisitor()
            visitor.visit(tree)
            self.assertEqual(visitor.hits, [], (name, visitor.hits))

    def test_registry_and_implementation_do_not_contain_canaries(self):
        blob = registry_bytes().decode("utf-8")
        for name in _IMPL:
            blob += (ROOT / name).read_text(encoding="utf-8")
        for canary in ("SECRET_CANARY", "PX_CANARY_12.5", "SZ_CANARY_7"):
            self.assertNotIn(canary, blob)


class _CallVisitor(ast.NodeVisitor):
    def __init__(self):
        self.hits = []

    def visit_Import(self, node):
        for alias in node.names:
            root = alias.name.split(".")[0]
            if root in _BANNED_MODULES or root == "os":
                self.hits.append(alias.name)
        self.generic_visit(node)

    def visit_ImportFrom(self, node):
        if node.module and node.module.split(".")[0] in _BANNED_MODULES | {"os"}:
            self.hits.append(node.module)
        self.generic_visit(node)

    def visit_Attribute(self, node):
        if node.attr in _BANNED_ATTRS:
            self.hits.append(node.attr)
        self.generic_visit(node)

    def visit_Call(self, node):
        name = ""
        if isinstance(node.func, ast.Name):
            name = node.func.id
        elif isinstance(node.func, ast.Attribute):
            name = node.func.attr
        if name in {"system", "popen", "exec", "eval", "print", "getenv"}:
            self.hits.append(name)
        if name == "open":
            mode = None
            if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
                mode = node.args[1].value
            for keyword in node.keywords:
                if keyword.arg == "mode" and isinstance(keyword.value, ast.Constant):
                    mode = keyword.value.value
            if mode not in (None, "r", "rb"):
                self.hits.append("open-write")
        self.generic_visit(node)


if __name__ == "__main__":
    unittest.main()
