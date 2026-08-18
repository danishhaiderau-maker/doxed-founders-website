import importlib.util
import pathlib
import unittest


SCRIPT = pathlib.Path(__file__).with_name("fly-dashboard-proxy.py")
SPEC = importlib.util.spec_from_file_location("fly_dashboard_proxy", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class FlyDashboardProxyContractTests(unittest.TestCase):
    def test_only_secure_attribute_is_removed_from_loopback_cookie(self):
        source = (
            "bot_admin_token=opaque; HttpOnly; Secure; "
            "SameSite=Lax; Path=/"
        )
        result = MODULE.loopback_response_header("Set-Cookie", source)
        self.assertEqual(
            result,
            "bot_admin_token=opaque; HttpOnly; SameSite=Lax; Path=/",
        )

    def test_non_cookie_header_is_unchanged(self):
        self.assertEqual(
            MODULE.loopback_response_header("Content-Type", "text/html"),
            "text/html",
        )

    def test_fresh_epoch_reset_is_an_allowed_mirror_mutation(self):
        self.assertIn("/api/fresh_epoch_reset", MODULE.MIRROR_MUTATION_ALLOWLIST)
        self.assertIn("/api/toggle_invert_signal", MODULE.MIRROR_MUTATION_ALLOWLIST)


if __name__ == "__main__":
    unittest.main()
