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
