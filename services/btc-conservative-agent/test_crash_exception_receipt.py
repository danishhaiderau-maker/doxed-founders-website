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
