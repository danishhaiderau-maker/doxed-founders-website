from pathlib import Path

bot_path = Path("services/btc-conservative-agent/bot.py")
text = bot_path.read_text(encoding="utf-8")
old = '''def _data_sync_receipt_bootstrap_gate() -> dict:
    """Return the fail-closed lifecycle receipt-bootstrap admission state."""
    runtime = _lifecycle_pipeline_runtime_status()
    if runtime.get("available") is False:
        return {"required": True, "status": "BLOCKED", "complete": False,
                "blocked": True, "error_code": "LIFECYCLE_STATUS_UNAVAILABLE",
                "ledger": None, "ledgers_checked": 0, "records_indexed": 0, "bytes_indexed": 0}
    bootstrap = (
        runtime.get("receipt_bootstrap")
        if isinstance(runtime.get("receipt_bootstrap"), dict) else {}
    )
    status = str(bootstrap.get("status") or "PENDING").upper()
    required = bootstrap.get("required") is True
    explicitly_not_required = bootstrap.get("required") is False
    complete = bool(
        bootstrap.get("complete") is True
        and bootstrap.get("blocked") is not True
        and (
            (required and status == "COMPLETE")
            or (explicitly_not_required and status == "NOT_REQUIRED")
        )
    )
    if status not in {"PENDING", "COMPLETE", "BLOCKED", "NOT_REQUIRED"}:
        status = "PENDING"
    return {
        "required": required,
        "status": status,
        "complete": complete,
        "blocked": bootstrap.get("blocked") is True,
        "ledger": bootstrap.get("ledger"),
        "ledgers_checked": max(0, int(bootstrap.get("ledgers_checked") or 0)),
        "records_indexed": max(0, int(bootstrap.get("records_indexed") or 0)),
        "bytes_indexed": max(0, int(bootstrap.get("bytes_indexed") or 0)),
        "cursor": (
            bootstrap.get("cursor")
            if isinstance(bootstrap.get("cursor"), int)
            and not isinstance(bootstrap.get("cursor"), bool)
            and bootstrap.get("cursor") >= 0 else None
        ),
    }'''
new = '''def _data_sync_receipt_bootstrap_gate() -> dict:
    """Return the fail-closed lifecycle receipt-bootstrap admission state."""
    runtime = _lifecycle_pipeline_runtime_status()
    if runtime.get("available") is False:
        return {"required": True, "status": "BLOCKED", "complete": False,
                "blocked": True, "error_code": "LIFECYCLE_STATUS_UNAVAILABLE",
                "ledger": None, "ledgers_checked": 0, "records_indexed": 0, "bytes_indexed": 0}
    bootstrap = (
        runtime.get("receipt_bootstrap")
        if isinstance(runtime.get("receipt_bootstrap"), dict) else {}
    )
    status = str(bootstrap.get("status") or "PENDING").upper()
    required = bootstrap.get("required") is True
    # Bootstrap is only mandatory when the lifecycle owner explicitly marks it
    # required. A dead/not-started owner (or no-epoch projection) previously
    # left required=false/missing with status=PENDING and starved inventory at
    # WAITING_RECEIPT_BOOTSTRAP forever on shared-cpu Fly.
    if not required and bootstrap.get("blocked") is not True:
        return {
            "required": False,
            "status": "NOT_REQUIRED",
            "complete": True,
            "blocked": False,
            "ledger": bootstrap.get("ledger"),
            "ledgers_checked": max(0, int(bootstrap.get("ledgers_checked") or 0)),
            "records_indexed": max(0, int(bootstrap.get("records_indexed") or 0)),
            "bytes_indexed": max(0, int(bootstrap.get("bytes_indexed") or 0)),
            "cursor": (
                bootstrap.get("cursor")
                if isinstance(bootstrap.get("cursor"), int)
                and not isinstance(bootstrap.get("cursor"), bool)
                and bootstrap.get("cursor") >= 0 else None
            ),
        }
    complete = bool(
        bootstrap.get("complete") is True
        and bootstrap.get("blocked") is not True
        and status == "COMPLETE"
    )
    if status not in {"PENDING", "COMPLETE", "BLOCKED", "NOT_REQUIRED"}:
        status = "PENDING"
    return {
        "required": required,
        "status": status,
        "complete": complete,
        "blocked": bootstrap.get("blocked") is True,
        "ledger": bootstrap.get("ledger"),
        "ledgers_checked": max(0, int(bootstrap.get("ledgers_checked") or 0)),
        "records_indexed": max(0, int(bootstrap.get("records_indexed") or 0)),
        "bytes_indexed": max(0, int(bootstrap.get("bytes_indexed") or 0)),
        "cursor": (
            bootstrap.get("cursor")
            if isinstance(bootstrap.get("cursor"), int)
            and not isinstance(bootstrap.get("cursor"), bool)
            and bootstrap.get("cursor") >= 0 else None
        ),
    }'''
if old not in text:
    raise SystemExit("OLD_GATE_NOT_FOUND")
bot_path.write_text(text.replace(old, new, 1), encoding="utf-8")
print("A1_GATE_PATCHED")

test_path = Path("services/btc-conservative-agent/test_fly_data_sync_contract.py")
test_text = test_path.read_text(encoding="utf-8")
old_test = '''    bootstrap.clear()
    bootstrap.update({
        "required": False, "status": "NOT_REQUIRED", "complete": True,
        "blocked": False,
    })
    not_required = namespace["_data_sync_receipt_bootstrap_gate"]()
    assert not_required["required"] is False
    assert not_required["status"] == "NOT_REQUIRED"
    assert not_required["complete"] is True
    bootstrap["complete"] = False
    assert namespace["_data_sync_receipt_bootstrap_gate"]()["complete"] is False'''
new_test = '''    bootstrap.clear()
    bootstrap.update({
        "required": False, "status": "PENDING", "complete": False,
        "blocked": False,
    })
    not_required = namespace["_data_sync_receipt_bootstrap_gate"]()
    assert not_required["required"] is False
    assert not_required["status"] == "NOT_REQUIRED"
    assert not_required["complete"] is True
    # Stale PENDING/incomplete flags must not block when required is false.
    bootstrap.update({"status": "PENDING", "complete": False})
    assert namespace["_data_sync_receipt_bootstrap_gate"]()["complete"] is True
    # Missing required key (dead lifecycle owner) also admits inventory.
    bootstrap.clear()
    missing = namespace["_data_sync_receipt_bootstrap_gate"]()
    assert missing["required"] is False and missing["complete"] is True'''
if old_test not in test_text:
    raise SystemExit("TEST_SNIPPET_NOT_FOUND")
test_path.write_text(test_text.replace(old_test, new_test, 1), encoding="utf-8")
print("TEST_PATCHED")
