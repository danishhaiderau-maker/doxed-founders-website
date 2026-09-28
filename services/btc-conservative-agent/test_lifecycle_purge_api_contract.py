import ast
from pathlib import Path


BOT_PATH = Path(__file__).with_name("bot.py")
BOT = BOT_PATH.read_text(encoding="utf-8")
TREE = ast.parse(BOT)


def _function(name):
    return next(
        node for node in TREE.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )


def test_purge_route_is_admin_guarded_default_disabled_and_exactly_confirmed():
    assert "@app.before_request\ndef _emergency_api_guard" in BOT
    assert "@app.route('/api/data-sync/lifecycle-purge/execute', methods=['POST'])" in BOT
    body = ast.unparse(_function("api_data_sync_lifecycle_purge_execute"))
    assert "LIFECYCLE_PURGE_ENABLED" in body and "os.getenv" in body
    assert "_LIFECYCLE_PURGE_CONFIRM_PREFIX" in body
    assert "hmac.compare_digest" in body
    assert "_LIFECYCLE_PURGE_LOCK.acquire(blocking=False)" in body
    assert "_lifecycle_purge_resolve_inputs(bundle_id)" in body
    assert "request" not in ast.unparse(_function("_lifecycle_purge_resolve_inputs"))


def test_purge_resolution_accepts_only_internal_bundle_identity_and_v2_commit():
    body = ast.unparse(_function("_lifecycle_purge_resolve_inputs"))
    assert "re.fullmatch" in body and "lifecycle-[0-9a-f]{64}" in body
    assert "_data_sync_lifecycle_ack_path(value)" in body
    assert "transaction.tx_root" in body
    assert "lifecycle_cleanup_transaction_v2" in body
    assert "COMMITTED" in body
    endpoint = ast.unparse(_function("api_data_sync_lifecycle_purge_execute"))
    for forbidden in ("bundle_manifest_path", "committed_path", "quarantine"):
        assert f"body.get('{forbidden}')" not in endpoint


def test_purge_age_has_non_bypassable_floor_and_startup_is_audit_only():
    age = ast.unparse(_function("_lifecycle_purge_minimum_age_seconds"))
    assert "max(_LIFECYCLE_PURGE_MINIMUM_AGE_FLOOR_SECONDS, configured)" in age
    assert "_LIFECYCLE_PURGE_MINIMUM_AGE_FLOOR_SECONDS = 86_400" in BOT
    audit = ast.unparse(_function("_audit_lifecycle_purge_recovery"))
    assert "execute_purge" not in audit
    assert "unlink" not in audit
    assert "rmtree" not in audit
    assert "_audit_lifecycle_purge_recovery()" in BOT


def test_purge_response_is_bounded_and_never_exposes_attestation_secret():
    body = ast.unparse(_function("api_data_sync_lifecycle_purge_execute"))
    assert '[:8]' in body
    assert "LIFECYCLE_LAPTOP_ATTESTATION_KEY" in body and "os.getenv" in body
    assert "secret" not in body.split("return jsonify")[-1]
