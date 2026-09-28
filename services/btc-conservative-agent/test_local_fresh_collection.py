import json
import hashlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from local_fresh_collection import (  # noqa: E402
    CONFIRMATION,
    InjectedResetCrash,
    LocalFreshCollectionRejected,
    PROTOCOL,
    SCOPE_VERSION,
    capability_status,
    execute_operation,
    queue_operation,
    read_operation,
)
from local_fresh_collection_owner_audit import (  # noqa: E402
    OWNER_COMMAND_PATTERNS,
    RELAUNCH_ACTION_PATTERNS,
    RELAUNCH_TASK_NAMES,
)
from research.local_generation_fence import (  # noqa: E402
    BLOCKED_STATE,
    LocalGenerationFenced,
    assert_local_generation_available,
    read_local_generation_fence,
)
from research.mirror_generation_lease import (  # noqa: E402
    LEASE_FILE_NAME,
    MirrorGenerationLease,
)


def safe_audit(_canonical, _archives):
    return {
        "schema": "fixture_owner_audit_v1",
        "safe": True,
        "owners": [],
        "running_tasks": [],
    }


class LocalFreshCollectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        self.canonical = base / "canonical-research-data"
        self.archives = base / "research_session_archives"
        self.state = base / "state"
        self.canonical.mkdir()
        self.archives.mkdir()
        (self.canonical / "canonical_dataset_current.json").write_text(
            json.dumps({"dataset_epoch": "fly-epoch-kept"}), encoding="utf-8"
        )
        (self.canonical / "raw").mkdir()
        (self.canonical / "raw" / "events.jsonl").write_text("one\ntwo\n", encoding="utf-8")
        (self.canonical / "analyzer").mkdir()
        (self.canonical / "analyzer" / "report.json").write_text("{}", encoding="utf-8")
        (self.archives / "old").mkdir()
        (self.archives / "old" / "report.html").write_text("old", encoding="utf-8")
        self.request = {
            "request_id": "1" * 32,
            "confirmation": CONFIRMATION,
            "expected_local_generation": "fly-epoch-kept",
        }

    def tearDown(self):
        self.temp.cleanup()

    def queue(self, request=None):
        return queue_operation(
            canonical_root=self.canonical,
            archive_root=self.archives,
            state_root=self.state,
            request=request or self.request,
        )

    def test_capability_contract_exposes_exact_scope_without_fly_mutation(self):
        result = capability_status(canonical_root=self.canonical, archive_root=self.archives)
        self.assertEqual(result["protocol"], PROTOCOL)
        self.assertEqual(result["scope_version"], SCOPE_VERSION)
        self.assertEqual(result["scope"], "LAPTOP_RESEARCH_ONLY")
        self.assertEqual(result["current_local_generation"], "fly-epoch-kept")
        self.assertTrue(result["root_topology_ready"])
        self.assertEqual(
            result["readiness_scope"],
            "local_research_owners_and_relaunch_authorities_v1",
        )
        self.assertFalse(result["fly_mutation_supported"])
        self.assertFalse(result["fly_mutation_requested"])

    def test_queue_is_durable_idempotent_and_changed_replay_conflicts(self):
        queued, replay = self.queue()
        self.assertFalse(replay)
        self.assertEqual(queued["status"], "QUEUED")
        same, replay = self.queue()
        self.assertTrue(replay)
        self.assertEqual(same["operation_id"], queued["operation_id"])
        changed = dict(self.request, confirmation="delete it")
        with self.assertRaisesRegex(LocalFreshCollectionRejected, "LOCAL_RESET_REPLAY_CONFLICT"):
            self.queue(changed)
        changed = dict(self.request, expected_local_generation="other")
        with self.assertRaisesRegex(LocalFreshCollectionRejected, "LOCAL_RESET_REPLAY_CONFLICT"):
            self.queue(changed)

    def test_complete_deletes_only_eligible_roots_and_leaves_durable_fence(self):
        queued, _ = self.queue()
        result = execute_operation(
            state_root=self.state,
            operation_id=queued["operation_id"],
            owner_auditor=safe_audit,
        )
        self.assertEqual(result["status"], "COMPLETE")
        self.assertEqual(result["deleted_file_count"], 4)
        self.assertEqual(result["remote_http_writes"], 0)
        self.assertFalse(result["fly_mutation_requested"])
        self.assertTrue(result["exact_hash_reconciliation"])
        self.assertTrue(result["deletion_reconciled"])
        completion = Path(result["completion_receipt_path"])
        self.assertTrue(completion.is_file())
        self.assertEqual(
            hashlib.sha256(completion.read_bytes()).hexdigest(),
            result["completion_receipt_sha256"],
        )
        completion_body = json.loads(completion.read_text(encoding="utf-8"))
        self.assertEqual(completion_body["remaining_rows"], [])
        self.assertEqual(completion_body["deleted_file_count"], 4)
        self.assertTrue((self.canonical / LEASE_FILE_NAME).exists())
        fence = read_local_generation_fence(self.canonical)
        self.assertEqual(fence["state"], BLOCKED_STATE)
        with self.assertRaises(LocalGenerationFenced):
            assert_local_generation_available(self.canonical, stage="test")
        self.assertEqual(list(self.archives.rglob("*")), [])
        self.assertTrue((self.state / "operations" / queued["operation_id"] / "operation.json").exists())
        again = execute_operation(
            state_root=self.state,
            operation_id=queued["operation_id"],
            owner_auditor=lambda *_: self.fail("completed replay must not re-audit"),
        )
        self.assertEqual(again["status"], "COMPLETE")

    def test_active_owner_preflight_blocks_without_fence_then_exact_replay_completes(self):
        queued, _ = self.queue()
        result = execute_operation(
            state_root=self.state,
            operation_id=queued["operation_id"],
            owner_auditor=lambda *_: {"safe": False, "owners": [{"pid": 7}]},
        )
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["error"], "LOCAL_RESET_READINESS_BLOCKED")
        self.assertTrue(result["retryable_preflight"])
        self.assertFalse(result["mutation_started"])
        self.assertFalse(result["fence_persisted"])
        self.assertEqual(result["deleted_file_count"], 0)
        self.assertEqual(result["deleted_bytes"], 0)
        self.assertEqual(result["blocker_categories"], ["active_local_research_owner"])
        self.assertNotIn("pid", json.dumps(result["owner_evidence"]))
        self.assertNotIn("fence", result)
        self.assertTrue((self.canonical / "raw" / "events.jsonl").exists())
        self.assertIsNone(read_local_generation_fence(self.canonical))

        replayed, replay = self.queue()
        self.assertTrue(replay)
        self.assertEqual(replayed["operation_id"], queued["operation_id"])
        self.assertNotIn("fence", replayed)
        completed = execute_operation(
            state_root=self.state,
            operation_id=queued["operation_id"],
            owner_auditor=safe_audit,
        )
        self.assertEqual(completed["status"], "COMPLETE")
        self.assertFalse(completed.get("retryable_preflight", False))
        self.assertTrue(completed["mutation_started"])
        self.assertTrue(completed["fence_persisted"])

    def test_enabled_unknown_relauncher_preflight_is_retryable_and_unmutated(self):
        queued, _ = self.queue()
        result = execute_operation(
            state_root=self.state,
            operation_id=queued["operation_id"],
            owner_auditor=lambda *_: {
                "safe": False,
                "blocker_categories": ["enabled_relaunch_authority"],
                "active_owner_categories": [],
                "relaunch_authority_categories": ["unknown_relaunch_authority"],
            },
        )
        self.assertEqual(result["status"], "BLOCKED")
        self.assertTrue(result["retryable_preflight"])
        self.assertEqual(
            result["owner_evidence"]["preflight"]["relaunch_authority_categories"],
            ["unknown_relaunch_authority"],
        )
        self.assertIsNone(read_local_generation_fence(self.canonical))
        self.assertTrue((self.canonical / "raw" / "events.jsonl").exists())

    def test_owner_race_under_lease_stays_fenced_and_reuses_same_tombstone(self):
        queued, _ = self.queue()
        audits = iter(
            [
                safe_audit(self.canonical, self.archives),
                {
                    "safe": False,
                    "blocker_categories": ["active_local_research_owner"],
                    "active_owner_categories": ["sync_owner"],
                    "relaunch_authority_categories": [],
                },
            ]
        )
        blocked = execute_operation(
            state_root=self.state,
            operation_id=queued["operation_id"],
            owner_auditor=lambda *_: next(audits),
        )
        self.assertEqual(blocked["status"], "BLOCKED")
        self.assertEqual(blocked["error"], "LOCAL_RESET_ACTIVE_OWNER")
        self.assertTrue(blocked["mutation_started"])
        self.assertTrue(blocked["fence_persisted"])
        self.assertFalse(blocked.get("retryable_preflight", False))
        first_tombstone = blocked["fence"]["tombstone_id"]
        self.assertIsNotNone(read_local_generation_fence(self.canonical))

        completed = execute_operation(
            state_root=self.state,
            operation_id=queued["operation_id"],
            owner_auditor=safe_audit,
        )
        self.assertEqual(completed["status"], "COMPLETE")
        self.assertEqual(completed["fence"]["tombstone_id"], first_tombstone)

    def test_concurrent_lease_owner_fails_without_unlinking_lock(self):
        queued, _ = self.queue()
        lease = MirrorGenerationLease(self.canonical, owner="fixture-reader").acquire(timeout_seconds=0)
        try:
            result = execute_operation(
                state_root=self.state,
                operation_id=queued["operation_id"],
                owner_auditor=safe_audit,
            )
        finally:
            lease.release()
        self.assertEqual(result["status"], "FAILED")
        self.assertIn("MIRROR_GENERATION_LEASE_TIMEOUT", result["error"])
        self.assertTrue((self.canonical / LEASE_FILE_NAME).exists())
        self.assertTrue((self.canonical / "raw" / "events.jsonl").exists())

    def test_crash_after_fence_resumes_same_inventory_and_completes(self):
        queued, _ = self.queue()
        with self.assertRaises(InjectedResetCrash):
            execute_operation(
                state_root=self.state,
                operation_id=queued["operation_id"],
                owner_auditor=safe_audit,
                crash_at="after_fence",
            )
        persisted = read_operation(state_root=self.state, operation_id=queued["operation_id"])
        self.assertEqual(persisted["status"], "RUNNING")
        self.assertIsNotNone(read_local_generation_fence(self.canonical))
        result = execute_operation(
            state_root=self.state,
            operation_id=queued["operation_id"],
            owner_auditor=safe_audit,
        )
        self.assertEqual(result["status"], "COMPLETE")

    def test_crash_before_fence_leaves_queue_recoverable_and_data_untouched(self):
        queued, _ = self.queue()
        with self.assertRaises(InjectedResetCrash):
            execute_operation(
                state_root=self.state,
                operation_id=queued["operation_id"],
                owner_auditor=safe_audit,
                crash_at="before_fence",
            )
        persisted = read_operation(state_root=self.state, operation_id=queued["operation_id"])
        self.assertEqual(persisted["status"], "QUEUED")
        self.assertIsNone(read_local_generation_fence(self.canonical))
        self.assertTrue((self.canonical / "raw" / "events.jsonl").exists())

    def test_crash_after_unlink_reconciles_missing_file_idempotently(self):
        queued, _ = self.queue()
        with self.assertRaises(InjectedResetCrash):
            execute_operation(
                state_root=self.state,
                operation_id=queued["operation_id"],
                owner_auditor=safe_audit,
                crash_at="after_unlink_0",
            )
        result = execute_operation(
            state_root=self.state,
            operation_id=queued["operation_id"],
            owner_auditor=safe_audit,
        )
        self.assertEqual(result["status"], "COMPLETE")
        self.assertEqual(len(result["deleted"]), len(result["inventory"]))

    def test_changed_file_after_inventory_refuses_partial_plan(self):
        queued, _ = self.queue()
        with self.assertRaises(InjectedResetCrash):
            execute_operation(
                state_root=self.state,
                operation_id=queued["operation_id"],
                owner_auditor=safe_audit,
                crash_at="after_inventory",
            )
        (self.canonical / "raw" / "events.jsonl").write_text("changed", encoding="utf-8")
        result = execute_operation(
            state_root=self.state,
            operation_id=queued["operation_id"],
            owner_auditor=safe_audit,
        )
        self.assertIn(result["status"], {"FAILED", "PARTIAL"})
        self.assertEqual(result["error"], "LOCAL_RESET_FILE_CHANGED")

    def test_new_file_after_inventory_yields_partial_not_complete(self):
        queued, _ = self.queue()
        with self.assertRaises(InjectedResetCrash):
            execute_operation(
                state_root=self.state,
                operation_id=queued["operation_id"],
                owner_auditor=safe_audit,
                crash_at="after_inventory",
            )
        (self.canonical / "late-writer.json").write_text("late", encoding="utf-8")
        result = execute_operation(
            state_root=self.state,
            operation_id=queued["operation_id"],
            owner_auditor=safe_audit,
        )
        self.assertEqual(result["status"], "PARTIAL")
        self.assertEqual(result["error"], "LOCAL_RESET_RECONCILIATION_INCOMPLETE")
        self.assertEqual(result["remaining"][0]["relative_path"], "late-writer.json")
        self.assertTrue(result["mutation_started"])
        self.assertTrue(result["fence_persisted"])
        self.assertFalse(result.get("retryable_preflight", False))
        self.assertIsNotNone(read_local_generation_fence(self.canonical))

    def test_nested_config_credentials_and_accounting_are_preserved(self):
        protected = {
            self.canonical / "nested" / "config" / "service.json": "config",
            self.canonical / "nested" / "credentials" / "api.txt": "credential",
            self.canonical / "accounting" / "lane_pnl_ledger.json": "accounting",
            self.archives / "old" / "nested" / "config" / "settings.json": "archive-config",
        }
        for path, value in protected.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(value, encoding="utf-8")
        queued, _ = self.queue()
        result = execute_operation(
            state_root=self.state,
            operation_id=queued["operation_id"],
            owner_auditor=safe_audit,
        )
        self.assertEqual(result["status"], "COMPLETE")
        self.assertEqual(result["retained_file_count"], len(protected))
        self.assertEqual(
            result["retained_inventory_sha256"],
            hashlib.sha256(
                json.dumps(
                    result["retained"], sort_keys=True, separators=(",", ":")
                ).encode()
            ).hexdigest(),
        )
        for path, value in protected.items():
            self.assertEqual(path.read_text(encoding="utf-8"), value)
        self.assertFalse((self.canonical / "raw" / "events.jsonl").exists())
        self.assertFalse((self.archives / "old" / "report.html").exists())
        reasons = {row["reason"] for row in result["retained"]}
        self.assertIn("ESSENTIAL_CONFIG_OR_CREDENTIAL", reasons)
        self.assertIn("ESSENTIAL_ORDER_PAPER_OR_ACCOUNTING_STATE", reasons)

    def test_nested_recovery_or_wal_blocks_before_any_delete(self):
        recovery = self.canonical / "nested" / "recovery" / "owner-state.json"
        recovery.parent.mkdir(parents=True)
        recovery.write_text("recovery", encoding="utf-8")
        ledger = self.canonical / "v3" / "ledgers" / "lifecycle.jsonl"
        ledger.parent.mkdir(parents=True)
        ledger.write_text("ledger\n", encoding="utf-8")
        wal = Path(str(ledger) + "-wal")
        wal.write_text("wal", encoding="utf-8")
        queued, _ = self.queue()
        result = execute_operation(
            state_root=self.state,
            operation_id=queued["operation_id"],
            owner_auditor=safe_audit,
        )
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["error"], "LOCAL_RESET_PROTECTED_RECOVERY_REQUIRES_AUDIT")
        self.assertEqual(result["deleted"], [])
        self.assertTrue(recovery.exists())
        self.assertTrue(ledger.exists())
        self.assertTrue(wal.exists())
        self.assertTrue((self.canonical / "raw" / "events.jsonl").exists())
        replay = execute_operation(
            state_root=self.state,
            operation_id=queued["operation_id"],
            owner_auditor=lambda *_: self.fail("protection block must be durable"),
        )
        self.assertEqual(replay["status"], "BLOCKED")

    def test_known_sqlite_sidecars_block_with_database_dependency_before_unlink(self):
        databases = (
            (
                self.canonical / "research_accumulator" / "research_trades_v983.db",
                "-journal",
            ),
            (
                self.canonical / "v3" / "lifecycle_bundle_index" / "lifecycle_index.sqlite3",
                "-wal",
            ),
            (
                self.canonical
                / "derived"
                / "policy-evidence"
                / ("generation-" + "a" * 64)
                / "results.sqlite",
                "-shm",
            ),
        )
        for database, suffix in databases:
            database.parent.mkdir(parents=True, exist_ok=True)
            database.write_bytes(b"sqlite-fixture")
            Path(str(database) + suffix).write_bytes(b"sidecar-fixture")
        queued, _ = self.queue()
        result = execute_operation(
            state_root=self.state,
            operation_id=queued["operation_id"],
            owner_auditor=safe_audit,
        )
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["error"], "LOCAL_RESET_PROTECTED_RECOVERY_REQUIRES_AUDIT")
        self.assertEqual(result["deleted"], [])
        self.assertEqual(
            len(
                [
                    blocker
                    for blocker in result["protected_blockers"]
                    if blocker.startswith("PROTECTED_SQLITE_SIDECAR_REQUIRES_AUDIT:")
                ]
            ),
            3,
        )
        retained = {(row["root"], row["relative_path"]) for row in result["retained"]}
        for database, suffix in databases:
            self.assertTrue(database.exists())
            self.assertTrue(Path(str(database) + suffix).exists())
            relative = database.relative_to(self.canonical).as_posix()
            self.assertIn(("canonical", relative), retained)
            self.assertIn(("canonical", relative + suffix), retained)
        self.assertTrue((self.canonical / "raw" / "events.jsonl").exists())

    def test_protected_source_copy_retains_complete_archive_receipt_closure(self):
        session = self.archives / "source-copy"
        payload = session / "payload"
        payload.mkdir(parents=True)
        protected_copy = payload / "9f4a.bin"
        protected_copy.write_text("credential-copy", encoding="utf-8")
        research_copy = payload / "research.bin"
        research_copy.write_text("research-copy", encoding="utf-8")
        metadata = {
            "schema": "research_archive_receipt_v2",
            "source_inventory": [
                {
                    "path": "nested/credentials/provider-token.json",
                    "preserved_path": "payload/9f4a.bin",
                    "preserved_bytes": len(b"credential-copy"),
                    "preserved_sha256": hashlib.sha256(b"credential-copy").hexdigest(),
                },
                {
                    "path": "signal_snapshot.jsonl",
                    "preserved_path": "payload/research.bin",
                    "preserved_bytes": len(b"research-copy"),
                    "preserved_sha256": hashlib.sha256(b"research-copy").hexdigest(),
                },
            ],
        }
        receipt = session / "archive_meta.json"
        receipt.write_text(json.dumps(metadata), encoding="utf-8")
        queued, _ = self.queue()
        result = execute_operation(
            state_root=self.state,
            operation_id=queued["operation_id"],
            owner_auditor=safe_audit,
        )
        self.assertEqual(result["status"], "COMPLETE")
        self.assertTrue(receipt.exists())
        self.assertTrue(protected_copy.exists())
        self.assertTrue(research_copy.exists())
        closure = [
            row for row in result["retained"]
            if row["reason"].startswith("PROTECTED_ARCHIVE_DEPENDENCY_CLOSURE:")
        ]
        self.assertEqual(len(closure), 3)

    def test_recovery_source_copy_blocks_without_deleting_archive_or_canonical(self):
        session = self.archives / "recovery-copy"
        payload = session / "payload"
        payload.mkdir(parents=True)
        recovery_copy = payload / "opaque.bin"
        recovery_copy.write_text("recovery", encoding="utf-8")
        (session / "archive_meta.json").write_text(
            json.dumps(
                {
                    "schema": "research_archive_receipt_v2",
                    "source_inventory": [
                        {
                            "path": "nested/recovery/pending.json",
                            "preserved_path": "payload/opaque.bin",
                            "preserved_bytes": len(b"recovery"),
                            "preserved_sha256": hashlib.sha256(b"recovery").hexdigest(),
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        queued, _ = self.queue()
        result = execute_operation(
            state_root=self.state,
            operation_id=queued["operation_id"],
            owner_auditor=safe_audit,
        )
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["deleted"], [])
        self.assertTrue(recovery_copy.exists())
        self.assertTrue((self.canonical / "raw" / "events.jsonl").exists())

    def test_unresolved_archive_receipt_blocks_before_any_delete(self):
        session = self.archives / "unresolved-copy"
        payload = session / "payload"
        payload.mkdir(parents=True)
        (payload / "opaque.bin").write_text("unknown", encoding="utf-8")
        (session / "archive_meta.json").write_text(
            json.dumps(
                {
                    "schema": "research_archive_receipt_v2",
                    "source_inventory": [
                        {
                            "path": "nested/config/provider.json",
                            "preserved_path": "payload/opaque.bin",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        queued, _ = self.queue()
        result = execute_operation(
            state_root=self.state,
            operation_id=queued["operation_id"],
            owner_auditor=safe_audit,
        )
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("UNRESOLVED_ARCHIVE_METADATA_REQUIRES_AUDIT", result["protected_blockers"][0])
        self.assertTrue((self.canonical / "raw" / "events.jsonl").exists())
        self.assertTrue((payload / "opaque.bin").exists())

    def test_owner_audit_covers_bundle_batch_resume_and_retirement_entrypoints(self):
        self.assertTrue(
            {
                "fly-sync-bundle-client",
                "start-fly-batch-sync",
                "fly-sync-generation-resume",
                "resume-current-epoch-batch-20260921.ps1",
                "small-sync-client-20260920",
                "raw_generation_cleanup_owner",
                "canonical_generation_retirement",
                "research-stability-supervisor.py",
            }.issubset(set(OWNER_COMMAND_PATTERNS))
        )
        self.assertIn("DcfShowcaseBotAutostart", RELAUNCH_TASK_NAMES)
        self.assertIn("DoxxedResearchStabilitySupervisor", RELAUNCH_TASK_NAMES)
        self.assertIn("DoxedSupervisorWatchdog", RELAUNCH_TASK_NAMES)
        self.assertIn(
            "resume-current-epoch-batch-20260921.ps1", RELAUNCH_ACTION_PATTERNS
        )
        self.assertIn("small-sync-client-20260920", RELAUNCH_ACTION_PATTERNS)
        self.assertIn("home-stack-supervisor-watchdog.ps1", RELAUNCH_ACTION_PATTERNS)

    def test_capability_refuses_split_runtime_root_and_duplicate_target(self):
        different_runtime = Path(self.temp.name) / "different-agent-root"
        different_runtime.mkdir()
        with self.assertRaisesRegex(
            LocalFreshCollectionRejected, "LOCAL_RESET_RUNTIME_ROOT_MISMATCH"
        ):
            capability_status(
                canonical_root=self.canonical,
                archive_root=self.archives,
                expected_canonical_root=self.canonical,
                expected_archive_root=self.archives,
                runtime_agent_root=different_runtime,
            )
        with mock.patch("local_fresh_collection.os.path.samefile", return_value=True):
            with self.assertRaisesRegex(
                LocalFreshCollectionRejected, "LOCAL_RESET_DUPLICATE_TARGET"
            ):
                capability_status(
                    canonical_root=self.canonical,
                    archive_root=self.archives,
                )

    @unittest.skipUnless(hasattr(os, "symlink"), "symlink support required")
    def test_link_escape_is_refused(self):
        external = Path(self.temp.name) / "external.txt"
        external.write_text("protect", encoding="utf-8")
        link = self.canonical / "escape"
        try:
            os.symlink(external, link)
        except OSError:
            self.skipTest("symlink creation unavailable")
        queued, _ = self.queue()
        result = execute_operation(
            state_root=self.state,
            operation_id=queued["operation_id"],
            owner_auditor=safe_audit,
        )
        self.assertEqual(result["status"], "FAILED")
        self.assertEqual(result["error"], "LOCAL_RESET_LINK_REFUSED")
        self.assertEqual(external.read_text(encoding="utf-8"), "protect")

    def test_reparse_target_is_refused_even_when_host_cannot_create_links(self):
        (self.canonical / "escape").write_text("do-not-follow", encoding="utf-8")
        queued, _ = self.queue()
        original = __import__("local_fresh_collection")._is_link_or_reparse

        def simulated_reparse(path):
            return path.name == "escape" or original(path)

        with mock.patch(
            "local_fresh_collection._is_link_or_reparse", side_effect=simulated_reparse
        ):
            result = execute_operation(
                state_root=self.state,
                operation_id=queued["operation_id"],
                owner_auditor=safe_audit,
            )
        self.assertEqual(result["status"], "FAILED")
        self.assertEqual(result["error"], "LOCAL_RESET_LINK_REFUSED")

    def test_worker_has_no_remote_http_surface(self):
        with mock.patch("urllib.request.urlopen", side_effect=AssertionError("network forbidden")):
            queued, _ = self.queue()
            result = execute_operation(
                state_root=self.state,
                operation_id=queued["operation_id"],
                owner_auditor=safe_audit,
            )
        self.assertEqual(result["status"], "COMPLETE")

    def test_complete_status_refuses_tampered_reconciliation_receipt(self):
        queued, _ = self.queue()
        result = execute_operation(
            state_root=self.state,
            operation_id=queued["operation_id"],
            owner_auditor=safe_audit,
        )
        Path(result["completion_receipt_path"]).write_text("{}\n", encoding="utf-8")
        with self.assertRaisesRegex(
            LocalFreshCollectionRejected, "LOCAL_RESET_COMPLETION_PIN_INVALID"
        ):
            read_operation(state_root=self.state, operation_id=queued["operation_id"])

    def test_worker_revalidates_exact_production_roots_from_receipt(self):
        queued, _ = self.queue()
        with self.assertRaisesRegex(
            LocalFreshCollectionRejected, "LOCAL_RESET_CANONICAL_ROOT_REQUIRED"
        ):
            execute_operation(
                state_root=self.state,
                operation_id=queued["operation_id"],
                owner_auditor=safe_audit,
                expected_canonical_root=Path(self.temp.name) / "different-root",
                expected_archive_root=self.archives,
            )
        self.assertTrue((self.canonical / "raw" / "events.jsonl").exists())


if __name__ == "__main__":
    unittest.main()
