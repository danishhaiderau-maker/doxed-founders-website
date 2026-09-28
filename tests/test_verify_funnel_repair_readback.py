from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


SCRIPT = Path(__file__).parents[1] / "scripts" / "verify_funnel_repair_readback.py"
SPEC = importlib.util.spec_from_file_location("verify_funnel_repair_readback", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")


def fixture(root: Path) -> dict:
    revision = "a5f83c0226c0"
    valid = [json.dumps({"row": number}).encode() + b"\n" for number in range(1, 1426)]
    corrupt = b'{"secret-looking-but-not-output": invalid-json}\n'
    raw_lines = valid[:1405] + [corrupt] + valid[1405:]
    raw = b"".join(raw_lines)
    rebuilt = b"".join(valid)
    source_sha = sha(raw)
    rebuilt_sha = sha(rebuilt)
    invalid_sha = sha(corrupt)
    (root / "execution_funnel.jsonl").write_bytes(rebuilt)
    (root / "canonical_dataset_manifest.jsonl").write_text(
        json.dumps({"deployed_revision": revision}) + "\n", encoding="utf-8"
    )
    repair = root / "corrupt_evidence_quarantine" / "one_repair"
    repair.mkdir(parents=True)
    (repair / "execution_funnel.jsonl").write_bytes(raw)
    manifest = {
        "schema": "corrupt_jsonl_quarantine_v2", "complete": True,
        "source_name": "execution_funnel.jsonl", "source_sha256": source_sha,
        "source_size_bytes": len(raw), "preserved_path": "execution_funnel.jsonl",
        "invalid_line_numbers": [1406], "expected_invalid_line_sha256": invalid_sha,
        "valid_line_count": 1425, "excluded_line_count": 1,
    }
    write_json(repair / "quarantine_manifest.json", manifest)
    excluded = {
        "schema": "excluded_corrupt_jsonl_lines_v1", "complete": True,
        "qualification": "UNKNOWN", "ranking_eligible": False,
        "lines": [{"line_number": 1406, "raw_sha256": invalid_sha,
                   "size_bytes": len(corrupt), "error_class": "JSONDecodeError",
                   "classification": "UNKNOWN_CORRUPT_EVIDENCE", "ranking_eligible": False}],
    }
    write_json(repair / "excluded_lines_unknown.json", excluded)
    active = root / "execution_funnel.jsonl"
    tail = rebuilt[-min(len(rebuilt), 1024 * 1024):]
    validation = {
        "schema": "jsonl_validation_receipt_v1", "complete": True,
        "source_name": "execution_funnel.jsonl", "size_bytes": len(rebuilt),
        "tail_bytes": len(tail), "tail_sha256": sha(tail),
    }
    write_json(root / "execution_funnel.jsonl.validation.json", validation)
    receipt = {
        "schema": "jsonl_corruption_repair_receipt_v1", "complete": True,
        "source_name": "execution_funnel.jsonl", "source_sha256": source_sha,
        "rebuilt_sha256": rebuilt_sha, "source_size_bytes": len(raw),
        "rebuilt_size_bytes": len(rebuilt), "valid_line_count": 1425,
        "excluded_line_count": 1, "invalid_line_numbers": [1406],
        "expected_invalid_line_sha256": invalid_sha, "qualification": "UNKNOWN",
        "ranking_eligible": False, "validation_receipt": "execution_funnel.jsonl.validation.json",
        "quarantine_manifest_sha256": MODULE._sha256(repair / "quarantine_manifest.json"),
    }
    write_json(repair / "repair_receipt.json", receipt)
    return {"root": root, "revision": revision, "source": source_sha,
            "rebuilt": rebuilt_sha, "invalid": invalid_sha, "repair": repair}


def run(values: dict) -> dict:
    return MODULE.verify(
        values["root"], expected_deployed_revision=values["revision"],
        expected_source_sha256=values["source"], expected_rebuilt_sha256=values["rebuilt"],
        expected_invalid_line=1406, expected_invalid_line_sha256=values["invalid"],
    )


def test_exact_readback_verifies_every_artifact_and_counts(tmp_path: Path):
    values = fixture(tmp_path)
    before = {
        str(path.relative_to(tmp_path)): (path.stat().st_size, sha(path.read_bytes()))
        for path in tmp_path.rglob("*") if path.is_file()
    }
    result = run(values)
    after = {
        str(path.relative_to(tmp_path)): (path.stat().st_size, sha(path.read_bytes()))
        for path in tmp_path.rglob("*") if path.is_file()
    }
    assert result["status"] == "VERIFIED"
    assert result["active_valid_line_count"] == 1425
    assert result["active_invalid_line_count"] == 0
    assert result["quarantined_invalid_line_count"] == 1
    assert result["source_sha256"] == values["source"]
    assert set(result["artifact_sha256"]) == {
        "active_sha256", "quarantined_raw_sha256", "quarantine_manifest_sha256",
        "excluded_unknown_receipt_sha256", "validation_receipt_sha256", "repair_receipt_sha256",
    }
    assert "root" not in json.dumps(result).lower()
    assert after == before


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        (lambda v: (v["root"] / "execution_funnel.jsonl").write_bytes(b"{}\n"), "ACTIVE_SHA256_MISMATCH"),
        (lambda v: v.update(revision="b5f83c0226c0"), "DEPLOYED_REVISION_MISMATCH"),
        (lambda v: (v["repair"] / "excluded_lines_unknown.json").write_text("{}"), "EXCLUDED_RECEIPT_INVALID"),
        (lambda v: (v["repair"] / "execution_funnel.jsonl").write_bytes(b"{}\n"), "RAW_SHA256_MISMATCH"),
    ],
)
def test_tampering_or_revision_drift_fails_closed(tmp_path: Path, mutation, reason: str):
    values = fixture(tmp_path)
    mutation(values)
    with pytest.raises(MODULE.VerificationError, match=reason):
        run(values)


def test_ambiguous_matching_receipts_are_refused(tmp_path: Path):
    values = fixture(tmp_path)
    duplicate = values["root"] / "corrupt_evidence_quarantine" / "duplicate"
    duplicate.mkdir()
    (duplicate / "repair_receipt.json").write_bytes(
        (values["repair"] / "repair_receipt.json").read_bytes()
    )
    with pytest.raises(MODULE.VerificationError, match="MATCHING_REPAIR_RECEIPT_COUNT_NOT_ONE"):
        run(values)


def test_symlink_is_refused_deterministically(tmp_path: Path, monkeypatch):
    values = fixture(tmp_path)
    raw = values["repair"] / "execution_funnel.jsonl"
    original = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda path: path == raw or original(path))
    with pytest.raises(MODULE.VerificationError, match="QUARANTINED_RAW_MISSING"):
        run(values)


def test_direct_path_escape_is_refused(tmp_path: Path):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    with pytest.raises(MODULE.VerificationError, match="PATH_ESCAPE"):
        MODULE._safe_existing(root.resolve(), outside.resolve(), "PATH_ESCAPE")


def test_cli_failure_is_sanitized_json_only(tmp_path: Path, capsys, monkeypatch):
    values = fixture(tmp_path)
    monkeypatch.setattr("sys.argv", [str(SCRIPT), "--mirror-root", str(tmp_path),
        "--expected-deployed-revision", values["revision"],
        "--expected-source-sha256", "0" * 64,
        "--expected-rebuilt-sha256", values["rebuilt"], "--expected-invalid-line", "1406",
        "--expected-invalid-line-sha256", values["invalid"]])
    assert MODULE.main() == 1
    output = capsys.readouterr().out
    parsed = json.loads(output)
    assert parsed == {"schema": "funnel_repair_readback_verification_v1", "complete": False,
                      "status": "REFUSED", "reason": "MATCHING_REPAIR_RECEIPT_COUNT_NOT_ONE"}
    assert str(tmp_path) not in output
    assert "secret-looking" not in output


def test_cli_argument_failure_is_sanitized_json_only(capsys, monkeypatch):
    monkeypatch.setattr("sys.argv", [str(SCRIPT), "--mirror-root", "sensitive-path"])
    assert MODULE.main() == 1
    output = capsys.readouterr().out
    assert json.loads(output)["reason"] == "ARGUMENTS_INVALID"
    assert "sensitive-path" not in output
