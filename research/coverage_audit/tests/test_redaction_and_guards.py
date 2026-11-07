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
                forbid(op, "fxmarka")
            self.assertEqual(caught.exception.code, code)
            self.assertNotIn("SECRET", str(caught.exception))
            text = "".join(traceback.format_exception(caught.exception))
            self.assertNotIn("fxmarka", text)

    def test_decompressor_exception_text_is_not_retained(self):
        payload = b"event_ts,note\n1700000000,alpha\n"
        blob = _gzip(payload)
        real = zlib.decompressobj

        def boom(*_args, **_kwargs):
            raise RuntimeError("fxexca")

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
        self.assertNotIn("fxexca", rendered)
        self.assertIn("GZIP_CORRUPT", str(caught.exception))
        for frame in formatted.stack:
            if "parsers.py" in frame.filename and frame.locals:
                self.assertNotIn("fxexca", " ".join(str(value) for value in frame.locals.values()))

    def test_implementation_call_sites_are_closed(self):
        for name in _IMPL:
            path = ROOT / name
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=name)
            visitor = _CallVisitor(allow_runtime_denies=name == "guards.py")
            visitor.visit(tree)
            self.assertEqual(visitor.hits, [], (name, visitor.hits))

    def test_registry_and_implementation_do_not_contain_canaries(self):
        blob = registry_bytes().decode("utf-8")
        for name in _IMPL:
            blob += (ROOT / name).read_text(encoding="utf-8")
        for canary in ("fxmarka", "fxmarkb", "fxmarkc"):
            self.assertNotIn(canary, blob)

    def test_runtime_denies_fire_before_outer_trap(self):
        import builtins
        import os
        import socket

        from research.coverage_audit.guards import activate_runtime_denies, restore_runtime_denies

        class OuterRefusal(Exception):
            def __init__(self, path):
                super().__init__(path)
                self.path = path

        outer = {"environ": 0, "getenv": 0, "dns": 0, "socket": 0, "connect": 0, "write": 0}
        saved = (os.getenv, os.environ, socket.socket, socket.getaddrinfo, socket.create_connection, builtins.open)

        def bump(path):
            outer[path] += 1
            raise OuterRefusal(path)

        def outer_getenv(*_args, **_kwargs):
            bump("getenv")

        def outer_socket(*_args, **_kwargs):
            bump("socket")

        def outer_getaddrinfo(*_args, **_kwargs):
            bump("dns")

        def outer_connect(*_args, **_kwargs):
            bump("connect")

        def outer_open(file, mode="r", *args, **kwargs):
            if any(flag in str(mode) for flag in ("w", "a", "x", "+")):
                bump("write")
            return saved[5](file, mode, *args, **kwargs)

        class OuterEnviron:
            def __getitem__(self, key):
                bump("environ")

            def get(self, key, default=None):
                bump("environ")

            def __iter__(self):
                bump("environ")

            def keys(self):
                bump("environ")

            def items(self):
                bump("environ")

            def values(self):
                bump("environ")

            def copy(self):
                bump("environ")

            def __contains__(self, key):
                bump("environ")

            def __len__(self):
                bump("environ")

        def is_kill(exc, path, before, after):
            if type(exc) is not OuterRefusal or exc.path != path:
                return False
            if after[path] != before[path] + 1:
                return False
            return all(after[key] == before[key] for key in after if key != path)

        calls = (
            ("environ", "ENV_READ_FORBIDDEN", lambda: os.environ["LANG"]),
            ("getenv", "ENV_READ_FORBIDDEN", lambda: os.getenv("LANG")),
            ("dns", "NETWORK_FORBIDDEN", lambda: socket.getaddrinfo("example.invalid", 1)),
            ("socket", "NETWORK_FORBIDDEN", lambda: socket.socket()),
            ("connect", "NETWORK_FORBIDDEN", lambda: socket.create_connection(("example.invalid", 1))),
            ("write", "SOURCE_MUTATION_FORBIDDEN", lambda: builtins.open("unused", "w")),
        )
        os.getenv = outer_getenv
        os.environ = OuterEnviron()
        socket.socket = outer_socket
        socket.getaddrinfo = outer_getaddrinfo
        socket.create_connection = outer_connect
        builtins.open = outer_open
        state = activate_runtime_denies()
        try:
            for path, code, invoke in calls:
                with self.assertRaises(AuditStop) as caught:
                    invoke()
                self.assertEqual(caught.exception.code, code)
                self.assertEqual(outer[path], 0)
                self.assertEqual(sum(outer.values()), 0)

            zeros = {key: 0 for key in outer}
            try:
                raise RuntimeError("unrelated")
            except RuntimeError as exc:
                self.assertFalse(is_kill(exc, "getenv", zeros, zeros))
            try:
                self.assertEqual(1, 0)
            except AssertionError as exc:
                self.assertFalse(is_kill(exc, "getenv", zeros, zeros))
            bumped = dict(zeros)
            bumped["dns"] = 1
            wrong = OuterRefusal("getenv")
            self.assertFalse(is_kill(wrong, "dns", zeros, bumped))
            self.assertFalse(is_kill(wrong, "getenv", zeros, bumped))

            for path, _code, invoke in calls:
                for guard in state["guards"].values():
                    guard.weaken = False
                state["guards"][path].weaken = True
                for key in outer:
                    outer[key] = 0
                before = dict(outer)
                try:
                    invoke()
                except OuterRefusal as exc:
                    self.assertTrue(is_kill(exc, path, before, outer))
                    print(f"MUTANT inner_{path} KILLED by test_runtime_denies_fire_before_outer_trap")
                else:
                    self.fail(path)
                state["guards"][path].weaken = False
        finally:
            restore_runtime_denies(state)
            os.getenv, os.environ, socket.socket, socket.getaddrinfo, socket.create_connection, builtins.open = saved

    def test_guard_mutants_are_killed(self):
        from research.coverage_audit import auditor, guards, parsers
        from research.coverage_audit.tests.test_synthetic_formats import BUILDERS, _rows_csv, _run_built, _with_profile

        def kill(mutant_id, control, weakened):
            control()
            try:
                weakened()
            except AssertionError:
                print(f"MUTANT {mutant_id} KILLED by test_guard_mutants_are_killed")
                return
            self.fail(mutant_id)

        real_tail = parsers.gzip_tail_is_bad
        real_crc = parsers.gzip_crc_is_bad
        real_name = parsers.member_name_mismatch
        real_hash = parsers.member_hash_mismatch
        real_exceeds = (guards.exceeds, parsers.exceeds)
        real_status = auditor._status
        real_redact = guards.redaction_ok
        try:
            def control_tail():
                report, _extra = _run_built(BUILDERS["gzip_tail"]())
                self.assertIn("GZIP_TRUNCATED_TAIL", report["reasons"])

            def weak_tail():
                parsers.gzip_tail_is_bad = lambda _following, _pad: False
                report, _extra = _run_built(BUILDERS["gzip_tail"]())
                self.assertIn("GZIP_TRUNCATED_TAIL", report["reasons"])

            kill("gzip_tail", control_tail, weak_tail)
            parsers.gzip_tail_is_bad = real_tail

            def control_crc():
                report, _extra = _run_built(BUILDERS["gzip_flip"]())
                self.assertIn("GZIP_CRC", report["reasons"])

            def weak_crc():
                parsers.gzip_crc_is_bad = lambda _computed, _expected: False
                report, _extra = _run_built(BUILDERS["gzip_flip"]())
                self.assertIn("GZIP_CRC", report["reasons"])

            kill("gzip_crc", control_crc, weak_crc)
            parsers.gzip_crc_is_bad = real_crc

            def control_order():
                report, _extra = _run_built(BUILDERS["zip_reorder"]())
                self.assertIn("MEMBER_ORDER_MISMATCH", report["reasons"])

            def weak_order():
                parsers.member_name_mismatch = lambda _actual, _pinned: False
                report, _extra = _run_built(BUILDERS["zip_reorder"]())
                self.assertIn("MEMBER_ORDER_MISMATCH", report["reasons"])

            kill("member_order", control_order, weak_order)
            parsers.member_name_mismatch = real_name

            def control_hash():
                built = BUILDERS["zip_multi"]()
                built["inputs"][0]["members"][1]["sha256"] = "0" * 64
                report, _extra = _run_built(built)
                self.assertIn("MEMBER_HASH_MISMATCH", report["reasons"])

            def weak_hash():
                parsers.member_hash_mismatch = lambda _actual, _pinned: False
                built = BUILDERS["zip_multi"]()
                built["inputs"][0]["members"][1]["sha256"] = "0" * 64
                report, _extra = _run_built(built)
                self.assertIn("MEMBER_HASH_MISMATCH", report["reasons"])

            kill("member_hash", control_hash, weak_hash)
            parsers.member_hash_mismatch = real_hash

            def control_cap():
                report, _extra = _run_built(_with_profile(_rows_csv((0, 60, 120)), memory_bytes=2048))
                self.assertIn("MEMORY_CAP", report["reasons"])

            def weak_cap():
                guards.exceeds = lambda _used, _limit: False
                parsers.exceeds = guards.exceeds
                report, _extra = _run_built(_with_profile(_rows_csv((0, 60, 120)), memory_bytes=2048))
                self.assertIn("MEMORY_CAP", report["reasons"])

            kill("cap_exceeds", control_cap, weak_cap)
            guards.exceeds, parsers.exceeds = real_exceeds

            def control_status():
                report, _extra = _run_built(BUILDERS["periodic_hole"]())
                self.assertEqual(report["status"], "INCOMPLETE_FAIL")

            def weak_status():
                auditor._status = lambda *_args, **_kwargs: "COMPLETE"
                report, _extra = _run_built(BUILDERS["periodic_hole"]())
                self.assertEqual(report["status"], "INCOMPLETE_FAIL")

            kill("status_branch", control_status, weak_status)
            auditor._status = real_status

            def control_redaction():
                self.assertFalse(guards.redaction_ok({"leak": "fxmarka"}, guards.static_trusted()))

            def weak_redaction():
                guards.redaction_ok = lambda *_args, **_kwargs: True
                self.assertFalse(guards.redaction_ok({"leak": "fxmarka"}, guards.static_trusted()))

            kill("redaction_ok", control_redaction, weak_redaction)
        finally:
            parsers.gzip_tail_is_bad = real_tail
            parsers.gzip_crc_is_bad = real_crc
            parsers.member_name_mismatch = real_name
            parsers.member_hash_mismatch = real_hash
            guards.exceeds, parsers.exceeds = real_exceeds
            auditor._status = real_status
            guards.redaction_ok = real_redact


class _CallVisitor(ast.NodeVisitor):
    def __init__(self, allow_runtime_denies: bool = False):
        self.hits = []
        self.allow_runtime_denies = allow_runtime_denies

    def visit_Import(self, node):
        for alias in node.names:
            root = alias.name.split(".")[0]
            if root in _BANNED_MODULES or root == "os":
                if not (self.allow_runtime_denies and root in {"os", "socket"}):
                    self.hits.append(alias.name)
        self.generic_visit(node)

    def visit_ImportFrom(self, node):
        if node.module and node.module.split(".")[0] in _BANNED_MODULES | {"os"}:
            root = node.module.split(".")[0]
            if not (self.allow_runtime_denies and root in {"os", "socket"}):
                self.hits.append(node.module)
        self.generic_visit(node)

    def visit_Attribute(self, node):
        if node.attr in _BANNED_ATTRS:
            if not (self.allow_runtime_denies and node.attr in {"environ", "getenv"}):
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
