import errno
import json
import pytest
import research_reset_preflight_diagnostics as subject
from research_reset_inventory import _base_class


def test_receipt_bounded_safe_and_retained(tmp_path):
    result = subject.write_reset_preflight_diagnostic(tmp_path, attempt_id="test-1",
        stage="ALL_SCOPES_PREFLIGHT", error=RuntimeError("SECRET /private/path"))
    path = tmp_path / "research_reset_receipts/_preflight/latest.json"
    assert result["diagnostic_written"]
    assert "SECRET" not in path.read_text()
    assert path.stat().st_size < 4096
    assert json.loads(path.read_text())["deletion_authority"] is False
    assert _base_class("research_reset_receipts/_preflight/latest.json") is None
    assert not (tmp_path / "ACTIVE_RESET.json").exists()


def test_enospc_preserves_previous_receipt(tmp_path, monkeypatch):
    subject.write_reset_preflight_diagnostic(tmp_path, attempt_id="old", stage="ADMISSION")
    path = tmp_path / "research_reset_receipts/_preflight/latest.json"
    old = path.read_bytes()
    def fail(*args):
        raise OSError(errno.ENOSPC, "secret")
    monkeypatch.setattr(subject.os, "replace", fail)
    result = subject.write_reset_preflight_diagnostic(tmp_path, attempt_id="new", stage="BOUNDARY")
    assert result["diagnostic_storage_code"] == "ENOSPC"
    assert path.read_bytes() == old
    assert len(list(path.parent.iterdir())) == 1


@pytest.mark.parametrize("attempt", ["../bad", "", "x" * 65])
def test_invalid_id(tmp_path, attempt):
    with pytest.raises(ValueError):
        subject.write_reset_preflight_diagnostic(tmp_path, attempt_id=attempt, stage="ADMISSION")


def test_unknown_exception_class_not_exported(tmp_path):
    class SecretName(Exception):
        pass
    result = subject.write_reset_preflight_diagnostic(tmp_path, attempt_id="a", stage="BOUNDARY", error=SecretName())
    assert result["diagnostic"]["error"] == "UNCLASSIFIED_FAILURE"


def test_refusal_is_sanitized(tmp_path):
    result = subject.write_reset_preflight_diagnostic(tmp_path, attempt_id="a", stage="ADMISSION",
        status="REFUSED", refusal_code="secret path")
    assert result["diagnostic"]["refusal_code"] == "UNCLASSIFIED_REFUSAL"


def test_destination_directory_refused(tmp_path):
    (tmp_path / "research_reset_receipts/_preflight/latest.json").mkdir(parents=True)
    assert not subject.write_reset_preflight_diagnostic(tmp_path, attempt_id="a", stage="ADMISSION")["diagnostic_written"]


def test_nested_symlink_refused(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        (tmp_path / "research_reset_receipts").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlink permission unavailable")
    assert not subject.write_reset_preflight_diagnostic(tmp_path, attempt_id="a", stage="ADMISSION")["diagnostic_written"]
    assert list(outside.iterdir()) == []
