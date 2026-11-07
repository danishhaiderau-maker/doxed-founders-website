"""End-to-end fixture receipt, determinism, launcher, and scope proof."""

from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout

from research.coverage_audit.cli import main as cli_main
from research.coverage_audit.guards import (
    STDLIB_STARTUP_EFFECTS,
    TINY_SYNTHETIC_PROFILE,
    canonical_bytes,
    sha256_hex,
    unexpected_schema_keywords,
    REPORT_SCHEMA,
    REGISTRY_SCHEMA,
)
from research.coverage_audit.launch_audit import main as launch_main
from research.coverage_audit.tests.test_synthetic_formats import (
    BASE,
    BUILDERS,
    CLOCK,
    ROOT,
    _run_built,
    load_cases,
    registry_bytes,
    run_group,
)

_ALLOWED = {
    "__init__.py",
    "README.md",
    "auditor.py",
    "cadence.py",
    "cli.py",
    "guards.py",
    "launch_audit.py",
    "parsers.py",
    "requirements.lock",
    "schemas/coverage_report.schema.json",
    "schemas/source_registry.schema.json",
    "source_registry.json",
    "tests/fixtures/synthetic_cases.json",
    "tests/test_cadence_semantics.py",
    "tests/test_limits_and_integrity.py",
    "tests/test_redaction_and_guards.py",
    "tests/test_synthetic_formats.py",
    "tests/test_synthetic_pipeline.py",
}


class PipelineTests(unittest.TestCase):
    def test_pipeline_fixture(self):
        run_group(self, "pipeline")

    def test_catalog_covers_every_fixture_id(self):
        ids = {case["id"] for case in load_cases()}
        self.assertEqual(ids, set(BUILDERS))

    def test_repeat_is_byte_identical_and_seed_is_bound(self):
        case = next(item for item in load_cases() if item["id"] == "periodic_complete")
        built = BUILDERS[case["id"]]()
        first, _extra = _run_built(built)
        second, _extra = _run_built(built)
        self.assertEqual(canonical_bytes(first), canonical_bytes(second))
        self.assertEqual(first["status"], "COMPLETE")
        built["seed"] = 1
        third, _extra = _run_built(built)
        self.assertNotEqual(first["run"]["rng_fingerprint"], third["run"]["rng_fingerprint"])
        self.assertEqual(first["run"]["event_order_sha256"], third["run"]["event_order_sha256"])
        self.assertEqual(third["status"], "COMPLETE")

    def test_cli_uses_explicit_arguments_and_ignores_path_text(self):
        built = BUILDERS["periodic_complete"]()
        report, _extra = _run_built(built)
        self.assertEqual(report["status"], "COMPLETE")
        registry = registry_bytes()
        allowlist = {
            "allowlist_id": "synthetic_fixture",
            "registry_sha256": sha256_hex(registry),
            "profile_id": "tiny-synthetic-v1",
            "inputs": [],
        }
        # Rebuild the allowlist from the same builder by replaying _run_built's shape.
        from research.coverage_audit.tests.test_synthetic_formats import _plain, _periodic_rows, _csv

        payload = _csv("event_ts,note", _periodic_rows())
        item = _plain("heartbeat", _periodic_rows())
        allowlist["inputs"] = [
            {
                "logical_id": item["logical_id"],
                "sha256": sha256_hex(payload),
                "size": len(payload),
                "feed_ref": item["feed_ref"],
                "members": [],
            }
        ]
        from research.coverage_audit.tests.test_synthetic_formats import MemorySource

        source = MemorySource(payload)
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code, cli_report = cli_main(
            [
                "--registry",
                "fxmarka.json",
                "--allowlist",
                "fxmarka.json",
                "--allowlist-sha256",
                sha256_hex(canonical_bytes(allowlist)),
                "--base-commit",
                BASE,
                "--clock-unix-ms",
                str(CLOCK),
                "--seed",
                "0",
                "--resource-profile",
                "tiny-synthetic-v1",
            ],
            registry_bytes=registry,
            allowlist_obj=allowlist,
            sources={item["logical_id"]: source},
            meter=BUILDERS["cpu_at"]()["meter"].__class__(0, 0),
            )
        self.assertNotIn("fxmarka", stdout.getvalue())
        self.assertNotIn("fxmarka", stderr.getvalue())
        self.assertEqual(code, 0, cli_report["reasons"])
        self.assertEqual(cli_report["status"], "COMPLETE")
        self.assertNotIn("fxmarka", json.dumps(cli_report))
        self.assertEqual(cli_report["run"]["base_commit"], BASE)
        self.assertFalse(cli_report["scope"]["real_interval_claim"])

    def test_launch_rejects_old_python_and_foreign_profile(self):
        code, report = launch_main(["--resource-profile", "other"], version=(3, 10))
        self.assertEqual(code, 2)
        self.assertIn("PYTHON_VERSION_UNSUPPORTED", report["reasons"])
        self.assertEqual(report["status"], "INCOMPLETE_FAIL")
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code, report = cli_main(
            [
                "--registry",
                "injected.json",
                "--allowlist",
                "injected.json",
                "--allowlist-sha256",
                "0" * 64,
                "--base-commit",
                BASE,
                "--clock-unix-ms",
                str(CLOCK),
                "--seed",
                "0",
                "--resource-profile",
                "relaxed-profile",
            ],
            registry_bytes=registry_bytes(),
            allowlist_obj={"allowlist_id": "synthetic_fixture", "registry_sha256": "0" * 64, "profile_id": "x", "inputs": []},
            sources={},
        )
        self.assertEqual(code, 4)
        self.assertIn("PROFILE_INVALID", report["reasons"])
        self.assertNotEqual(report["status"], "COMPLETE")

    def test_package_scope_is_exactly_eighteen_files(self):
        found = []
        for path in ROOT.rglob("*"):
            if not path.is_file():
                continue
            if "__pycache__" in path.parts or path.suffix == ".pyc":
                continue
            found.append(path.relative_to(ROOT).as_posix())
        self.assertEqual(sorted(found), sorted(_ALLOWED))

    def test_startup_effects_and_lock_and_profile_are_pinned(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("does not read environment", readme)
        self.assertIn("does not read environment", STDLIB_STARTUP_EFFECTS)
        self.assertIn("test bound", readme)
        for key, value in TINY_SYNTHETIC_PROFILE.items():
            if key == "profile_id":
                self.assertIn(value, readme)
            else:
                self.assertIn(str(value), readme)
        lock = json.loads((ROOT / "requirements.lock").read_text(encoding="utf-8"))
        self.assertEqual(lock["distributions"], [])
        self.assertEqual(lock["install"], "DO_NOT_PIP_INSTALL")
        self.assertEqual(unexpected_schema_keywords(REPORT_SCHEMA), [])
        self.assertEqual(unexpected_schema_keywords(REGISTRY_SCHEMA), [])
        document = json.loads(registry_bytes().decode("utf-8"))
        self.assertEqual(document["facts_policy"], "UNSOURCED_REAL_FACTS_REMAIN_UNVERIFIED")
        unverified = [feed for feed in document["feeds"] if feed["verification_status"] == "UNVERIFIED"]
        self.assertEqual(len(unverified), 1)
        self.assertIsNone(unverified[0]["cadence_ms"])
        self.assertIsNone(unverified[0]["permissible_publication_delay_ms"])

    def test_fixture_card_pins_match_clearance(self):
        payload = json.loads((ROOT / "tests" / "fixtures" / "synthetic_cases.json").read_text(encoding="utf-8"))
        self.assertEqual(payload["base_commit"], BASE)
        self.assertEqual(payload["card_sha256"], "3272c6425b592aac79163c789c66e5667a29be27466ad23f8ecab659a7c207d5")


def mutation_receipt():
    rows = []
    for case in load_cases():
        if not case.get("mutation"):
            continue
        report, _extra = _run_built(BUILDERS[case["id"]]())
        rows.append(
            {
                "id": case["id"],
                "status": report["status"],
                "reasons": report["reasons"],
                "expect_status": case["expect_status"],
                "expect_reasons": case.get("expect_reasons", []),
                "matched": report["status"] == case["expect_status"]
                and all(reason in report["reasons"] for reason in case.get("expect_reasons", [])),
            }
        )
    return rows


if __name__ == "__main__":
    unittest.main()
