import pytest

import research_exact_deletion as deletion
import research_reset_execution as execution
from test_research_reset_inventory import proof


def test_directory_target_rejected_before_hash(tmp_path, monkeypatch):
    target = tmp_path / "not-a-file"
    target.mkdir()
    monkeypatch.setattr(deletion, "_fingerprint", lambda _: pytest.fail("must not hash"))
    args = dict(root=tmp_path, targets=[target], allowed_paths=[target],
                receipt_path=tmp_path / "receipt.json", quiescent=True,
                recovery_states={"writers": "RECONCILED"})
    for operation in (deletion.validate_exact_research_deletion, deletion.delete_exact_research_files):
        with pytest.raises(deletion.ResearchDeletionRejected, match="TARGET_NOT_REGULAR_FILE"):
            operation(**args)
    assert target.is_dir()
    assert not args["receipt_path"].exists()


@pytest.fixture
def deletion_request(tmp_path):
    target = tmp_path / "signal_replay.jsonl"
    target.write_bytes(b'{"evidence":true}\n')
    return dict(root=tmp_path, targets=[target], allowed_paths=[target],
                receipt_path=tmp_path / "receipts/new/deletion.json", quiescent=True,
                recovery_states={"writers": "RECONCILED"}), target


def test_prospective_parent_is_nonmutating_but_actual_leaf_requires_parent(deletion_request, monkeypatch):
    args, target = deletion_request
    monkeypatch.setattr(deletion, "_fingerprint", lambda _: pytest.fail("must not hash"))
    accepted = deletion.validate_exact_research_deletion(**args, prospective_receipt_parent=True)
    assert accepted["paths"] == [target]
    assert not args["receipt_path"].parent.exists()
    with pytest.raises(deletion.ResearchDeletionRejected, match="UNSAFE_RECEIPT_PATH"):
        deletion.delete_exact_research_files(**args)
    assert target.exists()


@pytest.mark.parametrize("defect,code", [
    ("protected", "PROTECTED_PATH"), ("max_files", "INVALID_FILE_LIMIT"),
    ("max_bytes", "INVALID_BYTE_LIMIT"), ("context", "INVALID_RECEIPT_CONTEXT"),
    ("receipt", "UNSAFE_RECEIPT_PATH"), ("journal", "UNSAFE_PROGRESS_PATH"),
    ("parent_file", "UNSAFE_RECEIPT_PATH"), ("expected", "INVALID_EXPECTED_HASH_BINDING")])
def test_shared_admission_rejects_identically_before_any_hash(deletion_request, monkeypatch, defect, code):
    args, target = deletion_request
    args["receipt_path"].parent.mkdir(parents=True)
    if defect == "protected": args["protected_paths"] = [target]
    if defect == "max_files": args["max_files"] = True
    if defect == "max_bytes": args["max_total_bytes"] = True
    if defect == "context": args["receipt_context"] = {"unknown": "not admitted"}
    if defect == "receipt": args["receipt_path"].write_bytes(b"exists")
    if defect == "journal": args["receipt_path"].with_name("deletion.json.progress.jsonl").write_bytes(b"exists")
    if defect == "parent_file":
        parent = args["root"] / "parent-file"
        parent.write_bytes(b"ordinary-file")
        args["receipt_path"] = parent / "deletion.json"
    if defect == "expected": args["expected_sha256_by_path"] = {str(target): "invalid"}
    monkeypatch.setattr(deletion, "_fingerprint", lambda _: pytest.fail("must not hash"))
    for operation in (
            lambda: deletion.validate_exact_research_deletion(**args, prospective_receipt_parent=True),
            lambda: deletion.delete_exact_research_files(**args)):
        with pytest.raises(deletion.ResearchDeletionRejected, match=code): operation()
    assert target.exists()


def test_executor_checks_protection_before_expensive_hash(deletion_request, monkeypatch):
    args, target = deletion_request
    monkeypatch.setattr(execution, "_fingerprint", lambda _: pytest.fail("must not hash"))
    with pytest.raises(deletion.ResearchDeletionRejected, match="PROTECTED_PATH"):
        execution.execute_research_reset(runtime_root=args["root"], proof=proof(args["root"]),
            quiescent=True, recovery_states=args["recovery_states"], receipt_path=args["receipt_path"],
            protected_paths=(p for p in [target]), validate_only=True)
    assert target.exists()


def test_executor_accepts_prospective_safe_path_and_preserves_final_strictness(deletion_request):
    args, target = deletion_request
    kwargs = dict(runtime_root=args["root"], proof=proof(args["root"]), quiescent=True,
                  recovery_states=args["recovery_states"], receipt_path=args["receipt_path"])
    assert execution.execute_research_reset(**kwargs, validate_only=True)["status"] == "VALIDATED"
    assert not args["receipt_path"].parent.exists()
    with pytest.raises(deletion.ResearchDeletionRejected, match="UNSAFE_RECEIPT_PATH"):
        execution.execute_research_reset(**kwargs)
    assert target.exists()
