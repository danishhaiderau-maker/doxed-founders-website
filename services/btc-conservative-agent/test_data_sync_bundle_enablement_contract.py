"""Static contract for the server-side package worker enablement.

The transfer client can request the bundle protocol, but Fly must also run the
server-side worker that builds and retains the content-addressed packages.
Keep this check dependency-free so it can run before Flask/requests are
installed and cannot silently regress to PACKAGE_NOT_BUILT_OR_RETAINED.
"""

from pathlib import Path


SERVICE_DIR = Path(__file__).resolve().parent
FLY_CONFIG = (SERVICE_DIR / "fly.toml").read_text(encoding="utf-8")
BOT_SOURCE = (SERVICE_DIR / "bot.py").read_text(encoding="utf-8")


def test_fly_config_explicitly_enables_server_bundle_worker():
    assert "[env]" in FLY_CONFIG
    assert 'DATA_SYNC_TRANSPORT_BUNDLES_ENABLED = "1"' in FLY_CONFIG


def test_bundle_worker_still_fails_closed_when_flag_is_absent_or_not_one():
    assert 'os.getenv("DATA_SYNC_TRANSPORT_BUNDLES_ENABLED", "0") != "1"' in BOT_SOURCE

