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

    def test_active_owner_blocks_after_fence_without_deleting(self):
        queued, _ = self.queue()
        result = execute_operation(
            state_root=self.state,
            operation_id=queued["operation_id"],
            owner_auditor=lambda *_: {"safe": False, "owners": [{"pid": 7}]},
        )
        self.assertEqual(result["status"], "BLOCKED")
        self.assertTrue((self.canonical / "raw" / "events.jsonl").exists())
        self.assertIsNotNone(read_local_generation_fence(self.canonical))

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
