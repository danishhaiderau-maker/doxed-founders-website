"""VerifiedLedgerRowIndex.__exit__: cleanup always runs, the body's exception is never masked."""
from pathlib import Path

import pytest

from research.baseline_execution_context import VerifiedLedgerRowIndex


class _BadConnection:
    def __init__(self, real):
        self._real = real

    def close(self):
        self._real.close()
        raise RuntimeError("close failed")


def test_clean_exit_removes_temp_dir():
    with VerifiedLedgerRowIndex() as index:
        root = Path(index._temporary.name)
        assert root.exists()
    assert not root.exists()


def test_body_exception_is_not_masked_by_close_failure_and_temp_removed():
    with pytest.raises(ValueError, match="body"):
        with VerifiedLedgerRowIndex() as index:
            root = Path(index._temporary.name)
            index._connection = _BadConnection(index._connection)
            raise ValueError("body")
    assert not root.exists()


def test_close_failure_on_clean_exit_raises_but_temp_still_removed():
    with pytest.raises(RuntimeError, match="close failed"):
        with VerifiedLedgerRowIndex() as index:
            root = Path(index._temporary.name)
            index._connection = _BadConnection(index._connection)
    assert not root.exists()


def test_init_failure_removes_temp_dir(monkeypatch):
    seen = []
    real_create = VerifiedLedgerRowIndex._create_schema

    def boom(self):
        seen.append(Path(self._temporary.name))
        raise RuntimeError("schema failed")

    monkeypatch.setattr(VerifiedLedgerRowIndex, "_create_schema", boom)
    with pytest.raises(RuntimeError, match="schema failed"):
        VerifiedLedgerRowIndex()
    assert seen and not seen[0].exists()
    monkeypatch.setattr(VerifiedLedgerRowIndex, "_create_schema", real_create)
