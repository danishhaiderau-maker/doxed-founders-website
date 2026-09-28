"""Hash-bound forensic preservation and singleton-startup journal repair.

Application requires the startup ownership boundary and cooperative writer
lock. Unknown incidents are untouched; mirror validation is never relaxed.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path


MAX_BYTES = 16 * 1024 * 1024
INCIDENT_SIZE = 237934
INCIDENT_SHA256 = "9e97ca68c5a26d9a901344c3478710157df3c493f9836849e4130709648c70df"


def repair_known_incident_at_startup(root: Path):
    """Call only after singleton ownership and before starting bot threads.

    Unknown files are untouched; normal mirror validation still decides whether
    they are usable. This is not a general corruption-cleanup policy.
    """
    from crash_journal_writer import crash_journal_lock
    root = Path(root).resolve(strict=True)
    target = root / "crash_dump.json"
    if not target.exists():
        return "ABSENT"
    if target.is_symlink() or target.resolve() != target:
        raise ValueError("CRASH_JOURNAL_LINKED_TARGET")
    with crash_journal_lock(target):
        if target.stat().st_size != INCIDENT_SIZE:
            return "NOT_EXACT_INCIDENT"
        raw = target.read_bytes()
        if digest(raw) != INCIDENT_SHA256:
            return "NOT_EXACT_INCIDENT"
        _, derived, receipt = plan_repair(raw, expected_size=INCIDENT_SIZE,
                                         expected_sha256=INCIDENT_SHA256)
        quarantine = root / "corrupt_evidence_quarantine"
        quarantine.mkdir(exist_ok=True)
        if quarantine.resolve() != quarantine:
            raise ValueError("CRASH_JOURNAL_LINKED_QUARANTINE")
        destination = quarantine / ("crash-journal-" + INCIDENT_SHA256)
        if destination.is_symlink() or destination.resolve() != destination:
            raise ValueError("CRASH_JOURNAL_LINKED_ARCHIVE")
        if destination.exists():
            for name in ("original.bin", "derived.jsonl", "manifest.json"):
                artifact = destination / name
                if artifact.is_symlink() or artifact.resolve() != artifact:
                    raise ValueError("CRASH_JOURNAL_LINKED_ARTIFACT")
            # Resume only exact prefixes produced by interrupted preservation.
            # Never truncate or overwrite conflicting forensic evidence.
            receipt["source_artifact"] = "original.bin"
            receipt["derived_artifact"] = "derived.jsonl"
            for name, content in (("original.bin", raw), ("derived.jsonl", derived),
                                  ("manifest.json", json.dumps(receipt, sort_keys=True).encode("utf-8") + b"\n")):
                _resume_preserved_file(destination / name, content)
            if (destination / "original.bin").read_bytes() != raw or (
                    destination / "derived.jsonl").read_bytes() != derived:
                raise ValueError("CRASH_JOURNAL_QUARANTINE_MISMATCH")
            saved = json.loads((destination / "manifest.json").read_text())
            if any(saved.get(k) != v for k, v in receipt.items()):
                raise ValueError("CRASH_JOURNAL_MANIFEST_MISMATCH")
            if os.name != "nt":
                for directory in (destination, quarantine):
                    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
                    try:
                        os.fsync(fd)
                    finally:
                        os.close(fd)
        else:
            preserve_plan(raw, destination, expected_size=INCIDENT_SIZE,
                          expected_sha256=INCIDENT_SHA256)
        import tempfile
        fd, name = tempfile.mkstemp(prefix=".crash-repair-", dir=root)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(derived)
                handle.flush()
                os.fsync(handle.fileno())
            if target.read_bytes() != raw:
                raise ValueError("CRASH_JOURNAL_SOURCE_CHANGED")
            os.replace(name, target)
            if target.read_bytes() != derived:
                raise ValueError("CRASH_JOURNAL_REPLACE_FAILED")
            if os.name != "nt":
                directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
        finally:
            Path(name).unlink(missing_ok=True)
        return "REPAIRED_EXACT_INCIDENT"


def _resume_preserved_file(target: Path, content: bytes):
    """Append a missing suffix only; caller holds the incident writer lock."""
    if target.is_symlink() or target.resolve() != target:
        raise ValueError("CRASH_JOURNAL_LINKED_ARTIFACT")
    if target.exists() and target.stat().st_size > len(content):
        raise ValueError("CRASH_JOURNAL_QUARANTINE_MISMATCH")
    with target.open("a+b") as handle:
        handle.seek(0)
        existing = handle.read()
        if not content.startswith(existing):
            raise ValueError("CRASH_JOURNAL_QUARANTINE_MISMATCH")
        handle.seek(0, os.SEEK_END)
        handle.write(content[len(existing):])
        handle.flush()
        os.fsync(handle.fileno())
    if target.read_bytes() != content:
        raise ValueError("CRASH_JOURNAL_PRESERVATION_FAILED")


def preserve_plan(raw: bytes, destination: Path, *, expected_size: int,
                  expected_sha256: str) -> Path:
    """Create a new forensic directory, never replace files or the journal.

    A manifest appears only after both byte streams have been flushed and
    read-back verified. Interrupted directories are retained, not overwritten.
    """
    original, derived, receipt = plan_repair(
        raw, expected_size=expected_size, expected_sha256=expected_sha256)
    destination = Path(destination)
    parent = destination.parent.resolve(strict=True)
    if destination.parent.absolute() != parent:
        raise ValueError("CRASH_JOURNAL_LINKED_PARENT")
    destination.mkdir(exist_ok=False)
    for name, content in (("original.bin", original), ("derived.jsonl", derived)):
        target = destination / name
        with target.open("xb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        if target.read_bytes() != content:
            raise ValueError("CRASH_JOURNAL_PRESERVATION_FAILED")
    receipt["source_artifact"] = "original.bin"
    receipt["derived_artifact"] = "derived.jsonl"
    with (destination / "manifest.json").open("xb") as handle:
        handle.write(json.dumps(receipt, sort_keys=True).encode("utf-8") + b"\n")
        handle.flush()
        os.fsync(handle.fileno())
    if os.name != "nt":
        for directory in (destination, parent):
            fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
    return destination / "manifest.json"


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _constant(value: str):
    raise ValueError("NONFINITE_JSON")


def plan_repair(raw: bytes, *, expected_size: int, expected_sha256: str):
    """Return original bytes, derived valid rows and explicit excluded records.

    Valid rows retain exact original bytes/order. Exclusions remain UNKNOWN;
    no attempt is made to reconstruct damaged JSON or infer its contents.
    """
    if len(raw) > MAX_BYTES:
        raise ValueError("CRASH_JOURNAL_TOO_LARGE")
    if len(raw) != expected_size or digest(raw) != expected_sha256:
        raise ValueError("CRASH_JOURNAL_SOURCE_CHANGED")
    if not raw or not raw.endswith(b"\n"):
        raise ValueError("CRASH_JOURNAL_TAIL_NOT_COMPLETE")
    kept, excluded = [], []
    offset = 0
    for number, part in enumerate(raw[:-1].split(b"\n"), 1):
        row = part + b"\n"
        try:
            value = json.loads(row.decode("utf-8"), parse_constant=_constant)
            if not isinstance(value, dict):
                raise ValueError("NON_OBJECT_JSON")
        except (UnicodeDecodeError, ValueError) as error:
            excluded.append({"line": number, "offset": offset,
                             "size": len(row), "sha256": digest(row),
                             "classification": "UNKNOWN",
                             "error_type": type(error).__name__})
        else:
            kept.append(row)
        offset += len(row)
    if not excluded:
        raise ValueError("CRASH_JOURNAL_NO_REPAIR_NEEDED")
    if not kept:
        raise ValueError("CRASH_JOURNAL_NO_VALID_RECORDS")
    derived = b"".join(kept)
    return raw, derived, {
        "schema": "crash_journal_repair_plan_v1",
        "source_size": len(raw), "source_sha256": digest(raw),
        "derived_size": len(derived), "derived_sha256": digest(derived),
        "valid_records": len(kept), "excluded_records": excluded,
        "classification": "DERIVED_VALID_RECORDS_NOT_ORIGINAL",
        "requires_writer_quiescence": True,
    }
