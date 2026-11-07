"""Synthetic boundary checks for the pinned-root reader. No real inputs."""

from __future__ import annotations

import io
import json
import os
import unittest
from contextlib import redirect_stdout

from research.coverage_audit.guards import TINY_SYNTHETIC_PROFILE, canonical_bytes, sha256_hex
from research.coverage_audit.real_access_cli import main
from research.coverage_audit.real_file_reader import PinError, PinnedRoot

ROOT = "/opt/cursor/artifacts/coverage-reader-engineering-c71f-20261108/fixtures/boundary"
BASE = "0" * 40
CLOCK = 1700000000000
SEED = 1


def _registry() -> dict:
    return {
        "registry_schema": "source_registry_v1",
        "facts_policy": "UNSOURCED_REAL_FACTS_REMAIN_UNVERIFIED",
        "feeds": [
            {
                "venue": "synthetic",
                "source": "fixture",
                "feed": "heartbeat",
                "instrument": "casefile",
                "series": "v1",
                "era": "era1",
                "verification_status": "SYNTHETIC_DECLARED",
                "observation": "sparse",
                "cadence_ms": None,
                "tolerance_ms": None,
                "calendar": "UTC_CONTINUOUS",
                "sparse_rule": "NO_EVENT_NOT_OUTAGE",
                "timestamp": {"field": "event_ts", "unit": "unix_ms", "timezone": "UTC"},
                "optional_timestamps": {"receive": None, "publication": None, "vintage": None},
                "permissible_publication_delay_ms": None,
                "audit_publication_delay": False,
                "sequence_contract": None,
                "fields": [
                    {
                        "name": "event_ts",
                        "role": "event_timestamp",
                        "type": "timestamp",
                        "index": 0,
                        "required": True,
                    }
                ],
                "format": {
                    "container": "plain",
                    "encoding": "utf-8",
                    "kind": "header_csv",
                    "delimiter": ",",
                    "quote": '"',
                    "header": True,
                },
                "depth": None,
                "crosscheck_feed_ref": None,
            }
        ],
    }


def _allow(body: bytes, digest: str | None = None) -> dict:
    ref = "synthetic/fixture/heartbeat/casefile/era1"
    return {
        "allowlist_id": "synthetic_fixture",
        "registry_sha256": "",
        "profile_id": TINY_SYNTHETIC_PROFILE["profile_id"],
        "inputs": [
            {
                "logical_id": "case_main",
                "sha256": digest if digest is not None else sha256_hex(body),
                "size": len(body),
                "feed_ref": ref,
                "members": [],
            }
        ],
    }


class BoundaryTests(unittest.TestCase):
    def setUp(self):
        os.makedirs(ROOT, exist_ok=True)
        for name in os.listdir(ROOT):
            path = os.path.join(ROOT, name)
            if os.path.islink(path) or os.path.isfile(path):
                os.remove(path)

    def test_allowlisted_synthetic_completes(self):
        code, report, text = _run(b"event_ts\n1700000000000\n")
        self.assertEqual(code, 0)
        self.assertEqual(report["status"], "COMPLETE")
        self.assertEqual(report["reasons"], [])
        self.assertNotIn("CLI_ARGS", text)

    def test_parent_escape_refused(self):
        code, report, text = _run(b"event_ts\n1700000000000\n", rel="../rows.csv")
        self.assertEqual(code, 4)
        self.assertEqual(report["status"], "INCOMPLETE_FAIL")
        self.assertIn("CLI_ARGS", report["reasons"])
        self.assertNotIn("..", text)

    def test_symlink_refused_even_if_follow_flag(self):
        os.symlink("/etc/hostname", os.path.join(ROOT, "rows.csv"))
        root = PinnedRoot(ROOT)
        with self.assertRaises(PinError):
            root.open_read("rows.csv", follow=True)

    def test_replaced_bytes_are_source_changed(self):
        body = b"event_ts\n1700000000000\n"
        _materialize(body)
        opened = PinnedRoot(ROOT).open_read("rows.csv")
        fd = os.open(os.path.join(ROOT, "rows.csv"), os.O_WRONLY | os.O_APPEND)
        try:
            os.write(fd, b"\n")
        finally:
            os.close(fd)
        code, report, _text = _run_open(opened, body)
        self.assertNotEqual(report["status"], "COMPLETE")
        self.assertIn("SOURCE_CHANGED", report["reasons"])
        self.assertNotEqual(code, 0)

    def test_bad_hash_refused(self):
        body = b"event_ts\n1700000000000\n"
        code, report, _text = _run(body, digest="0" * 64)
        self.assertNotEqual(report["status"], "COMPLETE")
        self.assertIn("INPUT_HASH_MISMATCH", report["reasons"])
        self.assertNotEqual(code, 0)

    def test_over_profile_input_cap(self):
        body = b"x" * (TINY_SYNTHETIC_PROFILE["max_input_bytes"] + 1)
        code, report, _text = _run(body)
        self.assertNotEqual(report["status"], "COMPLETE")
        self.assertIn("INPUT_SIZE_CAP", report["reasons"])
        self.assertNotEqual(code, 0)


def _materialize(body: bytes, rel: str = "rows.csv") -> None:
    if rel.startswith("../"):
        path = os.path.join(os.path.dirname(ROOT), "rows.csv")
    else:
        path = os.path.join(ROOT, rel)
    with open(path, "wb") as handle:
        handle.write(body)


def _run(body: bytes, rel: str = "rows.csv", digest: str | None = None):
    if not rel.startswith("../"):
        _materialize(body, rel)
    registry = _registry()
    reg_bytes = canonical_bytes(registry)
    allow = _allow(body, digest)
    allow["registry_sha256"] = sha256_hex(reg_bytes)
    _write("registry.json", reg_bytes)
    _write("allowlist.json", canonical_bytes(allow))
    manifest = {
        "root": ROOT,
        "registry": "registry.json",
        "allowlist": "allowlist.json",
        "files": {"case_main": rel},
        "base_commit": BASE,
        "clock_unix_ms": CLOCK,
        "seed": SEED,
        "profile_id": TINY_SYNTHETIC_PROFILE["profile_id"],
    }
    _write("manifest.json", canonical_bytes(manifest))
    return _invoke(os.path.join(ROOT, "manifest.json"))


def _run_open(opened, body: bytes):
    from research.coverage_audit.auditor import run_audit

    registry = _registry()
    reg_bytes = canonical_bytes(registry)
    allow = _allow(body)
    allow["registry_sha256"] = sha256_hex(reg_bytes)
    report = run_audit(
        registry_bytes=reg_bytes,
        allowlist=allow,
        allowlist_sha256=sha256_hex(canonical_bytes(allow)),
        sources={"case_main": opened},
        base_commit=BASE,
        clock_unix_ms=CLOCK,
        seed=SEED,
        profile=dict(TINY_SYNTHETIC_PROFILE),
    )
    return 0 if report["status"] == "COMPLETE" else 4, report, ""


def _write(name: str, payload: bytes) -> None:
    with open(os.path.join(ROOT, name), "wb") as handle:
        handle.write(payload)


def _invoke(manifest: str):
    sink = io.StringIO()
    with redirect_stdout(sink):
        code, report = main(["--manifest", manifest])
    return code, report, sink.getvalue()


if __name__ == "__main__":
    unittest.main()
