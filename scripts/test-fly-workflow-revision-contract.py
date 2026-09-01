"""Static parity checks for Fly deploy and revision-monitor path semantics."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEPLOY = (ROOT / ".github/workflows/fly-bot-deploy.yml").read_text(encoding="utf-8")
MONITOR = (ROOT / ".github/workflows/fly-bot-monitor.yml").read_text(encoding="utf-8")
TEST_PATH = "services/btc-conservative-agent/test*.py"


def test_test_only_changes_are_excluded_from_deploy_and_expected_revision():
    assert f'- "!{TEST_PATH}"' in DEPLOY
    assert f"':(exclude,glob){TEST_PATH}'" in MONITOR


def test_runtime_and_deploy_contract_changes_remain_revision_relevant():
    for path in (
        "services/btc-conservative-agent/**",
        "scripts/check-relay-flat.mjs",
        ".github/workflows/fly-bot-deploy.yml",
    ):
        assert path in DEPLOY
        assert path.replace("/**", "") in MONITOR


def test_stalled_runtime_recovery_is_bound_to_guarded_receipts_and_durable_flatness():
    assert "recover-stalled-runtime" in DEPLOY
    assert "inputs.mode == 'recover-stalled-runtime'" in DEPLOY
    assert "Stalled runtime recovery anchored to guarded deployment" in DEPLOY
    assert 'run.get("conclusion") != "success"' in DEPLOY
    assert '"Deploy the exact source revision"' in DEPLOY
    assert '"Prove liveness, execution safety, and exact revision"' in DEPLOY
    assert 'DURABLE_RELAYS_ONLY_RECOVERY: "YES"' in DEPLOY
    assert 'REQUIRE_CANONICAL_FLY_OWNER: "NO"' in DEPLOY
