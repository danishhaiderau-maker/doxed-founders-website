"""Read-only verifier for one canonical execution-funnel corruption repair.

The command intentionally emits only a sanitized JSON result.  It never
prints artifact paths, JSONL contents, exception text, or environment data.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path


SHA256 = re.compile(r"[0-9a-f]{64}")
REVISION = re.compile(r"[0-9a-f]{7,64}")
ACTIVE_NAME = "execution_funnel.jsonl"
EXPECTED_VALID_LINES = 1425


class VerificationError(RuntimeError):
    """A deliberately non-sensitive, stable verification failure."""


class SanitizedArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise VerificationError("ARGUMENTS_INVALID")


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise VerificationError(code)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_object(path: Path, code: str) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise VerificationError(code) from None
    _require(isinstance(value, dict), code)
    return value


def _safe_existing(root: Path, path: Path, code: str, *, directory: bool = False) -> Path:
    """Require a real, non-symlink object contained by the canonical root."""
    try:
        relative = path.relative_to(root)
    except ValueError:
        raise VerificationError(code) from None
    current = root
    for part in relative.parts:
        current = current / part
        _require(current.exists() and not current.is_symlink(), code)
    _require(path.is_dir() if directory else path.is_file(), code)
    try:
        path.resolve(strict=True).relative_to(root)
    except (OSError, ValueError):
        raise VerificationError(code) from None
    return path


def _last_manifest_row(root: Path) -> dict:
    path = _safe_existing(root, root / "canonical_dataset_manifest.jsonl", "MANIFEST_MISSING")
    last = None
    try:
        with path.open("r", encoding="utf-8-sig") as handle:
            for line in handle:
                if line.strip():
                    row = json.loads(line)
                    _require(isinstance(row, dict), "MANIFEST_INVALID")
                    last = row
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise VerificationError("MANIFEST_INVALID") from None
    _require(last is not None, "MANIFEST_INVALID")
    return last


def _raw_line_audit(path: Path) -> tuple[int, list[dict]]:
    try:
        lines = path.read_bytes().splitlines(keepends=True)
    except OSError:
        raise VerificationError("RAW_READ_FAILED") from None
    invalid = []
    valid_count = 0
    for number, raw_line in enumerate(lines, 1):
        try:
            decoded = raw_line.decode("utf-8")
            if decoded.strip():
                json.loads(decoded)
            valid_count += 1
        except (UnicodeDecodeError, json.JSONDecodeError):
            invalid.append(
                {
                    "line_number": number,
                    "raw_sha256": hashlib.sha256(raw_line).hexdigest(),
                    "size_bytes": len(raw_line),
                }
            )
    return valid_count, invalid


def _active_line_count(path: Path) -> int:
    try:
        lines = path.read_bytes().splitlines(keepends=True)
    except OSError:
        raise VerificationError("ACTIVE_READ_FAILED") from None
    for raw_line in lines:
        try:
            decoded = raw_line.decode("utf-8")
            _require(bool(decoded.strip()), "ACTIVE_INVALID_JSONL")
            json.loads(decoded)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise VerificationError("ACTIVE_INVALID_JSONL") from None
    return len(lines)


def verify(
    mirror_root: Path,
    *,
    expected_deployed_revision: str,
    expected_source_sha256: str,
    expected_rebuilt_sha256: str,
    expected_invalid_line: int,
    expected_invalid_line_sha256: str,
) -> dict:
    for value, code, pattern in (
        (expected_deployed_revision, "EXPECTED_REVISION_INVALID", REVISION),
        (expected_source_sha256, "EXPECTED_SOURCE_SHA256_INVALID", SHA256),
        (expected_rebuilt_sha256, "EXPECTED_REBUILT_SHA256_INVALID", SHA256),
        (expected_invalid_line_sha256, "EXPECTED_INVALID_LINE_SHA256_INVALID", SHA256),
    ):
        _require(isinstance(value, str) and pattern.fullmatch(value) is not None, code)
    _require(
        isinstance(expected_invalid_line, int)
        and not isinstance(expected_invalid_line, bool)
        and expected_invalid_line > 0,
        "EXPECTED_INVALID_LINE_INVALID",
    )

    try:
        supplied_root = Path(mirror_root)
        _require(supplied_root.exists() and not supplied_root.is_symlink(), "MIRROR_ROOT_INVALID")
        root = supplied_root.resolve(strict=True)
    except OSError:
        raise VerificationError("MIRROR_ROOT_INVALID") from None
    _require(root.is_dir(), "MIRROR_ROOT_INVALID")

    manifest_row = _last_manifest_row(root)
    _require(
        str(manifest_row.get("deployed_revision") or "").lower()
        == expected_deployed_revision,
        "DEPLOYED_REVISION_MISMATCH",
    )

    active = _safe_existing(root, root / ACTIVE_NAME, "ACTIVE_FILE_MISSING")
    quarantine = _safe_existing(
        root, root / "corrupt_evidence_quarantine", "QUARANTINE_ROOT_INVALID", directory=True
    )
    matches: list[tuple[Path, dict]] = []
    try:
        children = list(quarantine.iterdir())
    except OSError:
        raise VerificationError("QUARANTINE_ROOT_INVALID") from None
    for child in children:
        _require(not child.is_symlink(), "SYMLINK_REFUSED")
        if not child.is_dir():
            continue
        receipt_path = child / "repair_receipt.json"
        if not receipt_path.exists():
            continue
        receipt_path = _safe_existing(root, receipt_path, "REPAIR_RECEIPT_UNSAFE")
        receipt = _json_object(receipt_path, "REPAIR_RECEIPT_INVALID")
        if (
            receipt.get("schema") == "jsonl_corruption_repair_receipt_v1"
            and receipt.get("source_sha256") == expected_source_sha256
            and receipt.get("rebuilt_sha256") == expected_rebuilt_sha256
            and receipt.get("invalid_line_numbers") == [expected_invalid_line]
            and receipt.get("expected_invalid_line_sha256")
            == expected_invalid_line_sha256
        ):
            matches.append((child, receipt))
    _require(len(matches) == 1, "MATCHING_REPAIR_RECEIPT_COUNT_NOT_ONE")
    repair_dir, receipt = matches[0]
    _safe_existing(root, repair_dir, "REPAIR_DIRECTORY_UNSAFE", directory=True)

    _require(receipt.get("complete") is True, "REPAIR_RECEIPT_INCOMPLETE")
    _require(receipt.get("source_name") == ACTIVE_NAME, "REPAIR_SOURCE_NAME_MISMATCH")
    _require(receipt.get("valid_line_count") == EXPECTED_VALID_LINES, "VALID_COUNT_MISMATCH")
    _require(receipt.get("excluded_line_count") == 1, "EXCLUDED_COUNT_MISMATCH")
    _require(receipt.get("qualification") == "UNKNOWN", "QUALIFICATION_MISMATCH")
    _require(receipt.get("ranking_eligible") is False, "RANKING_ELIGIBILITY_MISMATCH")

    manifest_path = _safe_existing(root, repair_dir / "quarantine_manifest.json", "QUARANTINE_MANIFEST_MISSING")
    excluded_path = _safe_existing(root, repair_dir / "excluded_lines_unknown.json", "EXCLUDED_RECEIPT_MISSING")
    raw_path = _safe_existing(root, repair_dir / ACTIVE_NAME, "QUARANTINED_RAW_MISSING")
    validation_name = receipt.get("validation_receipt")
    _require(validation_name == ACTIVE_NAME + ".validation.json", "VALIDATION_RECEIPT_NAME_INVALID")
    validation_path = _safe_existing(root, root / validation_name, "VALIDATION_RECEIPT_MISSING")

    hashes = {
        "active_sha256": _sha256(active),
        "quarantined_raw_sha256": _sha256(raw_path),
        "quarantine_manifest_sha256": _sha256(manifest_path),
        "excluded_unknown_receipt_sha256": _sha256(excluded_path),
        "validation_receipt_sha256": _sha256(validation_path),
        "repair_receipt_sha256": _sha256(repair_dir / "repair_receipt.json"),
    }
    _require(hashes["active_sha256"] == expected_rebuilt_sha256, "ACTIVE_SHA256_MISMATCH")
    _require(hashes["quarantined_raw_sha256"] == expected_source_sha256, "RAW_SHA256_MISMATCH")
    _require(
        hashes["quarantine_manifest_sha256"] == receipt.get("quarantine_manifest_sha256"),
        "QUARANTINE_MANIFEST_SHA256_MISMATCH",
    )

    manifest = _json_object(manifest_path, "QUARANTINE_MANIFEST_INVALID")
    _require(manifest.get("schema") == "corrupt_jsonl_quarantine_v2", "QUARANTINE_MANIFEST_INVALID")
    _require(manifest.get("complete") is True, "QUARANTINE_MANIFEST_INVALID")
    _require(manifest.get("source_name") == ACTIVE_NAME, "QUARANTINE_MANIFEST_INVALID")
    _require(manifest.get("preserved_path") == ACTIVE_NAME, "QUARANTINE_PATH_INVALID")
    _require(manifest.get("source_sha256") == expected_source_sha256, "QUARANTINE_MANIFEST_INVALID")
    _require(manifest.get("source_size_bytes") == raw_path.stat().st_size, "SOURCE_SIZE_MISMATCH")
    _require(manifest.get("invalid_line_numbers") == [expected_invalid_line], "QUARANTINE_MANIFEST_INVALID")
    _require(manifest.get("expected_invalid_line_sha256") == expected_invalid_line_sha256, "QUARANTINE_MANIFEST_INVALID")
    _require(manifest.get("valid_line_count") == EXPECTED_VALID_LINES, "VALID_COUNT_MISMATCH")
    _require(manifest.get("excluded_line_count") == 1, "EXCLUDED_COUNT_MISMATCH")

    excluded = _json_object(excluded_path, "EXCLUDED_RECEIPT_INVALID")
    lines = excluded.get("lines")
    _require(excluded.get("schema") == "excluded_corrupt_jsonl_lines_v1", "EXCLUDED_RECEIPT_INVALID")
    _require(excluded.get("complete") is True, "EXCLUDED_RECEIPT_INVALID")
    _require(excluded.get("qualification") == "UNKNOWN", "EXCLUDED_RECEIPT_INVALID")
    _require(excluded.get("ranking_eligible") is False, "EXCLUDED_RECEIPT_INVALID")
    _require(isinstance(lines, list) and len(lines) == 1, "EXCLUDED_RECEIPT_INVALID")
    excluded_line = lines[0]
    _require(isinstance(excluded_line, dict), "EXCLUDED_RECEIPT_INVALID")
    _require(excluded_line.get("line_number") == expected_invalid_line, "EXCLUDED_RECEIPT_INVALID")
    _require(excluded_line.get("raw_sha256") == expected_invalid_line_sha256, "EXCLUDED_RECEIPT_INVALID")
    _require(excluded_line.get("classification") == "UNKNOWN_CORRUPT_EVIDENCE", "EXCLUDED_RECEIPT_INVALID")
    _require(excluded_line.get("ranking_eligible") is False, "EXCLUDED_RECEIPT_INVALID")

    active_count = _active_line_count(active)
    _require(active_count == EXPECTED_VALID_LINES, "ACTIVE_LINE_COUNT_MISMATCH")
    raw_valid_count, raw_invalid = _raw_line_audit(raw_path)
    _require(raw_valid_count == EXPECTED_VALID_LINES, "RAW_VALID_COUNT_MISMATCH")
    _require(len(raw_invalid) == 1, "RAW_INVALID_COUNT_MISMATCH")
    _require(raw_invalid[0]["line_number"] == expected_invalid_line, "RAW_INVALID_LINE_MISMATCH")
    _require(raw_invalid[0]["raw_sha256"] == expected_invalid_line_sha256, "RAW_INVALID_SHA256_MISMATCH")
    _require(excluded_line.get("size_bytes") == raw_invalid[0]["size_bytes"], "RAW_INVALID_SIZE_MISMATCH")

    validation = _json_object(validation_path, "VALIDATION_RECEIPT_INVALID")
    _require(validation.get("schema") == "jsonl_validation_receipt_v1", "VALIDATION_RECEIPT_INVALID")
    _require(validation.get("complete") is True, "VALIDATION_RECEIPT_INVALID")
    _require(validation.get("source_name") == ACTIVE_NAME, "VALIDATION_RECEIPT_INVALID")
    _require(validation.get("size_bytes") == active.stat().st_size, "VALIDATION_SIZE_MISMATCH")
    tail_bytes = validation.get("tail_bytes")
    _require(isinstance(tail_bytes, int) and 0 <= tail_bytes <= active.stat().st_size, "VALIDATION_RECEIPT_INVALID")
    with active.open("rb") as handle:
        handle.seek(active.stat().st_size - tail_bytes)
        tail_sha = hashlib.sha256(handle.read()).hexdigest()
    _require(validation.get("tail_sha256") == tail_sha, "VALIDATION_TAIL_SHA256_MISMATCH")
    _require(receipt.get("source_size_bytes") == raw_path.stat().st_size, "SOURCE_SIZE_MISMATCH")
    _require(receipt.get("rebuilt_size_bytes") == active.stat().st_size, "REBUILT_SIZE_MISMATCH")

    return {
        "schema": "funnel_repair_readback_verification_v1",
        "complete": True,
        "status": "VERIFIED",
        "deployed_revision": expected_deployed_revision,
        "source_sha256": expected_source_sha256,
        "rebuilt_sha256": expected_rebuilt_sha256,
        "invalid_line_number": expected_invalid_line,
        "invalid_line_sha256": expected_invalid_line_sha256,
        "active_valid_line_count": active_count,
        "active_invalid_line_count": 0,
        "quarantined_valid_line_count": raw_valid_count,
        "quarantined_invalid_line_count": 1,
        "qualification": "UNKNOWN",
        "ranking_eligible": False,
        "artifact_sha256": hashes,
        "read_only": True,
        "network_used": False,
    }


def main() -> int:
    parser = SanitizedArgumentParser(add_help=False)
    parser.add_argument("--mirror-root", required=True)
    parser.add_argument("--expected-deployed-revision", required=True)
    parser.add_argument("--expected-source-sha256", required=True)
    parser.add_argument("--expected-rebuilt-sha256", required=True)
    parser.add_argument("--expected-invalid-line", required=True)
    parser.add_argument("--expected-invalid-line-sha256", required=True)
    try:
        args = parser.parse_args()
        try:
            invalid_line = int(args.expected_invalid_line)
        except (TypeError, ValueError):
            raise VerificationError("EXPECTED_INVALID_LINE_INVALID") from None
        receipt = verify(
            Path(args.mirror_root),
            expected_deployed_revision=args.expected_deployed_revision,
            expected_source_sha256=args.expected_source_sha256,
            expected_rebuilt_sha256=args.expected_rebuilt_sha256,
            expected_invalid_line=invalid_line,
            expected_invalid_line_sha256=args.expected_invalid_line_sha256,
        )
        print(json.dumps(receipt, sort_keys=True, separators=(",", ":")))
        return 0
    except VerificationError as exc:
        print(json.dumps({"schema": "funnel_repair_readback_verification_v1", "complete": False, "status": "REFUSED", "reason": str(exc)}, sort_keys=True, separators=(",", ":")))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
