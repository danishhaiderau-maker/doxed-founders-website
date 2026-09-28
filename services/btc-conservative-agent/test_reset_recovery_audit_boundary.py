"""Execute the real bounded recovery auditor without importing the runtime."""
import ast
import logging
from pathlib import Path


def auditor(root):
    source = Path(__file__).with_name("bot.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                    and node.name == "_audit_lifecycle_purge_recovery")
    namespace = {"_data_sync_volume_root": lambda: root, "logger": logging.getLogger(__name__)}
    exec(compile(ast.Module(body=[function], type_ignores=[]), "bot.py", "exec"), namespace)
    return namespace[function.name]


def test_missing_root_is_empty(tmp_path):
    assert auditor(tmp_path)() == []


def test_interrupted_transaction_is_reported(tmp_path):
    tx = tmp_path / "v3/lifecycle_purge_transactions/one"
    tx.mkdir(parents=True)
    (tx / "PREPARED.json").write_text("{}")
    assert auditor(tmp_path)() == [{"transaction": "one", "status": "PREPARED_REQUIRES_EXPLICIT_REPLAY"}]
    assert (tx / "PREPARED.json").read_text() == "{}"


def test_cap_cannot_report_false_clean(tmp_path):
    root = tmp_path / "v3/lifecycle_purge_transactions"
    root.mkdir(parents=True)
    for index in range(129):
        (root / str(index)).mkdir()
    rows = auditor(tmp_path)()
    assert {"status": "RECOVERY_AUDIT_INCOMPLETE", "limit": 128} in rows


def test_exact_cap_complete(tmp_path):
    root = tmp_path / "v3/lifecycle_purge_transactions"
    root.mkdir(parents=True)
    for index in range(128):
        (root / str(index)).mkdir()
    assert auditor(tmp_path)() == []
