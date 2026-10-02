import json
from types import SimpleNamespace

import crash_exception_receipt as receipt


def test_original_type_errno_and_frames_without_secret_message(monkeypatch):
    writes = []
    monkeypatch.setattr(receipt.os, "write", lambda fd, data: writes.append((fd, data)))
    try:
        raise OSError(28, "SECRET_TOKEN_DO_NOT_PRINT")
    except OSError as exc:
        receipt.emit_original_exception_receipt(type(exc), exc, exc.__traceback__)
    fd, data = writes[0]
    parsed = json.loads(data)
    assert fd == 2
    assert parsed["exception_type"] == "OSError"
    assert parsed["errno"] == 28
    assert parsed["frames"][0]["file"] == "test_crash_exception_receipt.py"
    assert parsed["frames"][0]["function"] == "test_original_type_errno_and_frames_without_secret_message"
    assert parsed["frames"][0]["line"] > 0
    assert b"SECRET_TOKEN" not in data
    assert b"locals" not in data


def test_long_traceback_has_bounded_traversal_and_valid_capped_json(monkeypatch):
    writes = []
    monkeypatch.setattr(receipt.os, "write", lambda fd, data: writes.append(data))
    tail = SimpleNamespace(tb_frame=SimpleNamespace(f_code=SimpleNamespace(
        co_filename="C:/private/operator/" + "\u2603" * 300,
        co_name="\u2603" * 300)), tb_lineno=123, tb_next=None)
    tail.tb_next = tail  # A cycle also verifies traversal cannot run forever.
    receipt.emit_original_exception_receipt(ValueError, ValueError("secret"), tail)
    assert len(writes) == 1
    assert len(writes[0]) <= receipt.MAX_RECEIPT_BYTES
    parsed = json.loads(writes[0])
    assert 0 < len(parsed["frames"]) <= 16
    assert parsed["frames_truncated"] is True
    assert parsed["errno"] is None
    assert b"private" not in writes[0]


def test_sink_enospc_is_silent(monkeypatch):
    def full(fd, data):
        raise OSError(28, "sink full")
    monkeypatch.setattr(receipt.os, "write", full)
    receipt.emit_original_exception_receipt(RuntimeError, RuntimeError("secret"), None)


def test_no_traceback_still_emits_identity(monkeypatch):
    writes = []
    monkeypatch.setattr(receipt.os, "write", lambda fd, data: writes.append(data))
    receipt.emit_original_exception_receipt(RuntimeError, RuntimeError("secret"), None)
    parsed = json.loads(writes[0])
    assert parsed["exception_type"] == "RuntimeError"
    assert parsed["frames"] == []
    assert parsed["frames_truncated"] is False
def test_global_hook_emits_before_failing_logger(monkeypatch):
    import ast
    from pathlib import Path
    from types import SimpleNamespace
    import crash_exception_receipt as module
    events = []
    monkeypatch.setattr(module, 'emit_original_exception_receipt', lambda *args: events.append('receipt'))
    def broken_logger(*args):
        events.append('logger')
        raise OSError(28, 'disk full')
    tree = ast.parse(Path(__file__).with_name('bot.py').read_text(encoding='utf8'))
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'global_exception_handler')
    env = {'logger': SimpleNamespace(critical=broken_logger)}
    exec(compile(ast.Module(body=[node], type_ignores=[]), 'bot.py', 'exec'), env)
    try:
        env['global_exception_handler'](OSError, OSError(28, 'secret'), None)
    except OSError:
        pass
    assert events == ['receipt', 'logger']


def test_provider_result_receipt_records_error_and_source_identity():
    import ast
    import threading
    import time
    from pathlib import Path

    source = Path(__file__).with_name("bot.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    node = next(
        n for n in tree.body
        if isinstance(n, ast.FunctionDef)
        and n.name == "_record_ai_provider_result_receipt"
    )
    state = {}
    env = {"state": state, "state_lock": threading.RLock(), "time": time}
    exec(compile(ast.Module(body=[node], type_ignores=[]), "bot.py", "exec"), env)
    env["_record_ai_provider_result_receipt"](
        {
            "source": "ERROR",
            "ai_error": True,
            "synthetic_response": False,
            "shared_ai_call_id": "call-1",
        }
    )
    assert state["last_ai_provider_result_source"] == "ERROR"
    assert state["last_ai_provider_result_error"] is True
    assert state["last_ai_provider_result_status"] == "ERROR"
    assert state["last_ai_provider_evaluation_error"] is None
    assert state["last_ai_provider_result_synthetic"] is False
    assert state["last_ai_provider_result_call_id"] == "call-1"
    assert state["last_ai_provider_result_ts"] > 0


def test_post_provider_evaluation_error_annotation_preserves_call_identity():
    import ast
    import threading
    from pathlib import Path

    source = Path(__file__).with_name("bot.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    node = next(
        n for n in tree.body
        if isinstance(n, ast.FunctionDef)
        and n.name == "_record_ai_provider_evaluation_error"
    )
    state = {
        "last_ai_provider_result_call_id": "call-1",
        "last_ai_provider_evaluation_error": None,
    }
    env = {"state": state, "state_lock": threading.RLock()}
    exec(compile(ast.Module(body=[node], type_ignores=[]), "bot.py", "exec"), env)
    env["_record_ai_provider_evaluation_error"]("ValueError", "call-1")
    assert state["last_ai_provider_evaluation_error"] == "ValueError"
    env["_record_ai_provider_evaluation_error"]("RuntimeError", "other-call")
    assert state["last_ai_provider_evaluation_error"] == "ValueError"


def test_provider_boundary_contract_distinguishes_cassette_and_post_provider_failure():
    import ast
    from pathlib import Path

    source = Path(__file__).with_name("bot.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    evaluate = next(
        n for n in tree.body
        if isinstance(n, ast.FunctionDef) and n.name == "evaluate_signal_with_ai"
    )
    provider_calls = [
        n for n in ast.walk(evaluate)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == "_record_ai_provider_result_receipt"
    ]
    # Exactly one call records a successful provider/cassette result and one
    # records an attempted provider failure; later gate errors must not call it.
    assert len(provider_calls) == 2
    assert '"source": "CASSETTE" if cassette_resp else "FRESH"' in source
    assert '"synthetic_response": bool(cassette_resp)' in source
    assert '"provider_result_status": "SUCCEEDED"' in source
    assert 'ai_result["provider_evaluation_error"] = type(e).__name__' in source
    assert 'ai_result["provider_result_status"] = "NOT_CALLED"' in source
    assert "_record_ai_provider_evaluation_error" in source


def test_provider_receipt_precedes_success_telemetry_and_exports_explicit_error_fields():
    from pathlib import Path

    source = Path(__file__).with_name("bot.py").read_text(encoding="utf-8")
    block = source.split("cassette_resp = None", 1)[1].split(
        'logger.info(f"[AI RAW RESPONSE]', 1
    )[0]
    assert block.index("_record_ai_provider_result_receipt(provider_result_meta)") < block.index(
        "log_pipeline_event("
    )
    error_log = source.split("def log_ai_error_row", 1)[1].split(
        "def log_ai_tranche_outcome", 1
    )[0]
    assert '"ai_error": bool(ai_result.get("ai_error", False))' in error_log
    assert '"provider_evaluation_error": ai_result.get("provider_evaluation_error")' in error_log


def test_provider_provenance_is_in_durable_ai_log_rows():
    from pathlib import Path

    source = Path(__file__).with_name("bot.py").read_text(encoding="utf-8")
    input_log = source.split("def log_ai_input_full", 1)[1].split(
        "def _compose_ai_history_reason", 1
    )[0]
    error_log = source.split("def log_ai_error_row", 1)[1].split(
        "def log_ai_tranche_outcome", 1
    )[0]
    for block in (input_log, error_log):
        assert "provider_result_status" in block
        assert "synthetic_response" in block
        assert "shared_ai_call_id" in block
