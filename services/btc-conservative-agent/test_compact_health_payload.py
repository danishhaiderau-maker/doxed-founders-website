import ast
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent
BOT = (ROOT / "bot.py").read_text(encoding="utf-8")


def _load_compactor():
    tree = ast.parse(BOT)
    node = next(
        item for item in tree.body
        if isinstance(item, ast.FunctionDef)
        and item.name == "_compact_strategy_progress_health_snapshot"
    )
    namespace = {}
    exec(compile(ast.Module(body=[node], type_ignores=[]), "bot.py", "exec"), namespace)
    return namespace[node.name]


def _load_lock_classifier(timeout=2.0):
    tree = ast.parse(BOT)
    node = next(
        item for item in tree.body
        if isinstance(item, ast.FunctionDef)
        and item.name == "_trade_lock_probe_status"
    )
    namespace = {"WATCHDOG_TRADE_LOCK_TIMEOUT_SEC": timeout}
    exec(compile(ast.Module(body=[node], type_ignores=[]), "bot.py", "exec"), namespace)
    return namespace[node.name]


def test_compact_health_preserves_status_and_bounds_dead_letters():
    secret_context = "private-ai-context-" * 10_000
    source = {
        "ok": True,
        "reasons": [],
        "trade_lock_available": True,
        "ws_progressing": True,
        "ai_progressing": True,
        "scheduled_ai_cycle": {"stage": "IDLE", "stack_tail": [secret_context]},
        "post_ai_evidence": {
            "submitted": 7,
            "completed": 6,
            "last_gap": {
                "schema": "post_ai_evidence_gap_v1",
                "hook": "reversal_study",
                "reason": "HOOK_TIMEOUT",
                "key": "safe-key",
                "detail": secret_context,
            },
            "workers": {
                "reversal_study": {
                    "accepting": True,
                    "active": 0,
                    "completed": 6,
                    "queued": 0,
                    "unfinished": 0,
                    "dead_letters": [
                        {
                            "hook": "reversal_study",
                            "reason": "HOOK_TIMEOUT",
                            "key": "safe-key",
                            "context": secret_context,
                            "payload": {"prompt": secret_context},
                        }
                    ],
                }
            },
        },
    }
    compact = _load_compactor()(source)
    encoded = json.dumps(compact)
    assert compact["ok"] is True
    assert compact["trade_lock_available"] is True
    assert compact["ws_progressing"] is True
    assert compact["ai_progressing"] is True
    assert "stack_tail" not in compact["scheduled_ai_cycle"]
    worker = compact["post_ai_evidence"]["workers"]["reversal_study"]
    assert worker["dead_letter_count"] == 1
    assert worker["latest_dead_letter"]["reason"] == "HOOK_TIMEOUT"
    assert "dead_letters" not in worker
    assert "detail" not in compact["post_ai_evidence"]["last_gap"]
    assert secret_context not in encoded
    assert len(encoded) < 2_000


def test_health_and_ready_use_compact_strategy_snapshot():
    health_start = BOT.index("def health():")
    ready_start = BOT.index("def ready():", health_start)
    ready_end = BOT.index("\n@app.route", ready_start + len("def ready():"))
    health_body = BOT[health_start:ready_start]
    ready_body = BOT[ready_start:ready_end]
    marker = "_compact_strategy_progress_health_snapshot("
    assert marker in health_body
    assert marker in ready_body
    assert "_strategy_progress_health_snapshot(now, trade_lock_timeout_sec=0.0)" in health_body
    assert "_strategy_progress_health_snapshot(now, trade_lock_timeout_sec=0.0)" in ready_body


def test_health_lock_probe_is_nonblocking_without_weakening_watchdog_default():
    function_start = BOT.index("def _strategy_progress_health_snapshot(")
    function_end = BOT.index("\ndef _compact_strategy_progress_health_snapshot", function_start)
    body = BOT[function_start:function_end]
    assert "trade_lock_timeout_sec: float | None = None" in body
    assert "WATCHDOG_TRADE_LOCK_TIMEOUT_SEC" in body
    assert "max(0.0, float(trade_lock_timeout_sec))" in body
    assert "trade_lock.acquire(timeout=lock_probe_timeout)" in body
    assert "_trade_lock_probe_status(" in body
    assert '"ok": bool(lock_progressing and ws_progressing and ai_progressing)' in body
    assert '"trade_lock_available": bool(lock_available)' in body
    assert '"trade_lock_busy_transient": lock_busy_transient' in body
    assert '"trade_lock_progressing": lock_progressing' in body


def test_zero_wait_lock_classifier_allows_only_bounded_known_owner_contention():
    classify = _load_lock_classifier(timeout=2.0)
    assert classify(True, {}, 0.0) == (False, True)
    assert classify(False, {"owner_ident": 7, "held_seconds": 0.25}, 0.0) == (True, True)
    assert classify(False, {"owner_ident": 7, "held_seconds": 2.01}, 0.0) == (False, False)
    assert classify(False, {"owner_ident": None, "held_seconds": 0.25}, 0.0) == (False, False)
    assert classify(False, {"owner_ident": 7, "held_seconds": 0.25}, None) == (False, False)
