import sys
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from research.local_generation_fence import (  # noqa: E402
    LocalGenerationFenced,
    persist_local_generation_fence,
)
from research.canonical_data_store import append_manifest, archive_before_cleanup  # noqa: E402


class LocalFreshCollectionIntegrationTests(unittest.TestCase):
    def test_bridge_contract_is_authenticated_local_only_and_legacy_wipe_is_retired(self):
        bridge = (REPO / "scripts" / "home-stack-launcher.ps1").read_text(encoding="utf-8")
        routes = (REPO / "scripts" / "local-reset-http.ps1").read_text(encoding="utf-8")
        worker = (REPO / "scripts" / "home-stack-cmd-worker.ps1").read_text(encoding="utf-8")
        self.assertIn('local-reset-http.ps1', bridge)
        self.assertIn("Invoke-LocalResetApiRoute", bridge)
        self.assertIn("/api/local-research-reset/v1/capability", routes)
        self.assertIn("/api/local-research-reset/v1/requests", routes)
        self.assertIn("X-Local-Reset-Capability", routes)
        self.assertIn("Test-LocalResetOrigin", routes)
        self.assertIn("LOCAL_RESET_CAPABILITY_NOT_PROVISIONED", routes)
        self.assertIn("Laptop reset unavailable; connect local controller", routes)
        self.assertIn("-Status 202", routes)
        self.assertIn("-Status 405", routes)
        self.assertIn("LEGACY_WIPE_RESEARCH_RETIRED", bridge)
        self.assertNotIn('Invoke-WebRequest -Uri "http://127.0.0.1:$BotPort/api/reset"', worker)

    def test_sync_loop_standalone_analyzer_and_publishers_have_fence_checks(self):
        standalone = (REPO / "scripts" / "sync-fly-bot-data.ps1").read_text(encoding="utf-8")
        loop = (REPO / "scripts" / "sync-fly-bot-data-loop.ps1").read_text(encoding="utf-8")
        launcher = (REPO / "scripts" / "start-home-analyzer.ps1").read_text(encoding="utf-8")
        analyzer = (HERE / "analyzer_research_engine_v62.py").read_text(encoding="utf-8")
        for marker in (
            "standalone_sync_start",
            "file_atomic_promotion",
            "bundle_file_atomic_promotion",
            "remote_",
            "canonical_manifest_promotion",
            "analyzer_report_remote_publish",
        ):
            self.assertIn(marker, standalone)
        self.assertIn("sync_loop_iteration", loop)
        self.assertIn("sync_loop_under_lease", loop)
        self.assertIn("analyzer_launcher_start", launcher)
        self.assertIn("analyzer_iteration_start", analyzer)
        self.assertIn("analyzer_iteration_under_lease", analyzer)
        self.assertIn("analyzer_generation_atomic_swap", analyzer)
        self.assertIn("analyzer_session_archive", analyzer)

    def test_canonical_manifest_promotion_refuses_fenced_generation(self):
        import tempfile

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            persist_local_generation_fence(
                root,
                {
                    "operation_id": "1" * 32,
                    "local_generation": "local-reset-fixture",
                    "tombstone_id": "tombstone-fixture",
                },
            )
            with self.assertRaises(LocalGenerationFenced):
                append_manifest(root, {})
            with self.assertRaises(LocalGenerationFenced):
                archive_before_cleanup(root, root / "old.json", reason="fixture")


if __name__ == "__main__":
    unittest.main()
