"""Shared, stdlib-only contract for sealed research-data segments.

A segment is a deterministic ``tar.gz`` whose members are the exact bytes of
ordered manifest units. The manifest is canonical JSON and is hash-chained to
the previous manifest, so gaps, reordering and substitution are detectable by
the laptop puller without trusting the object store.

Both the Fly shipper and the laptop puller import this module; keep it free of
runtime (bot.py) imports.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import re
import tarfile

MANIFEST_SCHEMA = "research_segment_manifest_v1"
ACK_SCHEMA = "research_segment_laptop_ack_v1"
SHIPPER_VERSION = "research_segment_shipper_v1"
GENESIS_PREV_SHA256 = "0" * 64
SEQ_WIDTH = 12
GZIP_LEVEL = 6

KIND_APPEND = "APPEND"
KIND_SEAL = "SEAL"
KIND_SNAPSHOT = "SNAPSHOT"
KIND_REWRITE = "REWRITE"
KIND_TOMBSTONE = "TOMBSTONE"
# Epoch genesis only: an append stream starts at ``base_offset``; the bytes
# before it are recorded by size and sha256 but never shipped. The payload is
# the stream's header line (CSV) or empty.
KIND_BASELINE = "BASELINE"
PAYLOAD_KINDS = frozenset({KIND_APPEND, KIND_SEAL, KIND_SNAPSHOT, KIND_REWRITE, KIND_BASELINE})
ALL_KINDS = PAYLOAD_KINDS | {KIND_TOMBSTONE}

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SAFE_PREFIX_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class SegmentFormatError(ValueError):
    """A segment, manifest or ACK violates the sealed-segment contract."""


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def canonical_json(payload) -> bytes:
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ).encode("ascii")


def validate_prefix(prefix: str) -> str:
    if not isinstance(prefix, str) or not _SAFE_PREFIX_RE.match(prefix):
        raise SegmentFormatError(f"unsafe key prefix: {prefix!r}")
    return prefix


def seq_token(seq: int) -> str:
    if not isinstance(seq, int) or isinstance(seq, bool) or seq < 1:
        raise SegmentFormatError(f"invalid segment seq: {seq!r}")
    return f"{seq:0{SEQ_WIDTH}d}"


def segment_key(prefix: str, seq: int) -> str:
    return f"{validate_prefix(prefix)}/seg/{seq_token(seq)}.tar.gz"


def manifest_key(prefix: str, seq: int) -> str:
    return f"{validate_prefix(prefix)}/man/{seq_token(seq)}.json"


def ack_prefix(prefix: str) -> str:
    return f"{validate_prefix(prefix)}/acks/laptop/"


def ack_key(prefix: str, seq: int) -> str:
    return f"{ack_prefix(prefix)}{seq_token(seq)}.json"


def member_name(index: int) -> str:
    return f"m/{int(index):06d}.bin"


def validate_relpath(path: str) -> str:
    """Reject absolute, parent-escaping, drive-qualified or odd relative paths."""
    if not isinstance(path, str) or not path or len(path) > 1024:
        raise SegmentFormatError(f"invalid member path: {path!r}")
    if "\\" in path or "\x00" in path or path.startswith("/") or ":" in path:
        raise SegmentFormatError(f"unsafe member path: {path!r}")
    parts = path.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise SegmentFormatError(f"unsafe member path: {path!r}")
    return path


def build_segment(payloads: list[bytes]) -> bytes:
    """Build a byte-identical ``tar.gz`` for the same ordered payloads.

    Every metadata field that could vary between attempts or hosts is pinned:
    member names, mtime, uid/gid, owner names, mode, tar format, gzip header
    mtime/filename and compression level.
    """
    tar_buffer = io.BytesIO()
    with tarfile.open(fileobj=tar_buffer, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        for index, payload in enumerate(payloads):
            info = tarfile.TarInfo(member_name(index))
            info.size = len(payload)
            info.mtime = 0
            info.mode = 0o644
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            info.type = tarfile.REGTYPE
            archive.addfile(info, io.BytesIO(payload))
    compressed = io.BytesIO()
    with gzip.GzipFile(
        filename="", mode="wb", fileobj=compressed, compresslevel=GZIP_LEVEL, mtime=0,
    ) as handle:
        handle.write(tar_buffer.getvalue())
    return compressed.getvalue()


def build_segment_from_file(path, size: int, chunk_bytes: int = 1024 * 1024) -> bytes:
    """Stream one file into a segment byte-identical to ``build_segment([raw])``.

    Only the compressed output is held in memory, so a large SQLite snapshot
    never needs its raw bytes, the tar buffer and the gzip output at once.
    """
    compressed = io.BytesIO()
    with gzip.GzipFile(
        filename="", mode="wb", fileobj=compressed, compresslevel=GZIP_LEVEL, mtime=0,
    ) as handle:
        with tarfile.open(fileobj=handle, mode="w", format=tarfile.USTAR_FORMAT,
                          bufsize=chunk_bytes) as archive:
            info = tarfile.TarInfo(member_name(0))
            info.size = int(size)
            info.mtime = 0
            info.mode = 0o644
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            info.type = tarfile.REGTYPE
            with open(path, "rb") as source:
                archive.addfile(info, source)
    return compressed.getvalue()


def read_segment(raw: bytes, expected_members: int) -> list[bytes]:
    """Extract ordered member payloads without touching the filesystem."""
    try:
        tar_bytes = gzip.decompress(raw)
        with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:") as archive:
            members = archive.getmembers()
            if len(members) != expected_members:
                raise SegmentFormatError(
                    f"segment has {len(members)} members, manifest declares {expected_members}"
                )
            payloads = []
            for index, member in enumerate(members):
                if member.name != member_name(index) or not member.isreg():
                    raise SegmentFormatError(f"unexpected segment member {member.name!r}")
                handle = archive.extractfile(member)
                payloads.append(handle.read() if handle is not None else b"")
            return payloads
    except (OSError, EOFError, tarfile.TarError) as exc:
        raise SegmentFormatError(f"segment is not a readable tar.gz: {exc}") from exc


def build_manifest(
    *, prefix: str, seq: int, prev_manifest_sha256: str, segment_raw: bytes,
    members: list[dict], source_git_rev: str, collection_epoch_id: str,
    window_start: float, window_end: float,
) -> dict:
    manifest = {
        "schema": MANIFEST_SCHEMA,
        "shipper_version": SHIPPER_VERSION,
        "seq": seq,
        "prev_manifest_sha256": prev_manifest_sha256,
        "segment_key": segment_key(prefix, seq),
        "segment_sha256": sha256_bytes(segment_raw),
        "segment_size": len(segment_raw),
        "member_count": len(members),
        "members": members,
        # Identity is audit/cohort metadata only; it is never a transfer gate.
        "source_git_rev": str(source_git_rev or "unknown"),
        "collection_epoch_id": str(collection_epoch_id or ""),
        "window_start": round(float(window_start), 3),
        "window_end": round(float(window_end), 3),
    }
    validate_manifest(manifest, prefix=prefix, expected_seq=seq)
    return manifest


def _require_sha(value, field: str) -> None:
    if not isinstance(value, str) or not _SHA256_RE.match(value):
        raise SegmentFormatError(f"{field} is not a sha256 hex digest")


def _require_int(value, field: str, minimum: int = 0) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise SegmentFormatError(f"{field} must be an integer >= {minimum}")


def validate_member(member: dict, index: int) -> None:
    if not isinstance(member, dict):
        raise SegmentFormatError("member must be an object")
    if member.get("index") != index:
        raise SegmentFormatError(f"member index {member.get('index')!r} != {index}")
    kind = member.get("kind")
    if kind not in ALL_KINDS:
        raise SegmentFormatError(f"unknown member kind {kind!r}")
    validate_relpath(member.get("path"))
    _require_int(member.get("size"), "member.size")
    _require_sha(member.get("sha256"), "member.sha256")
    if kind == KIND_TOMBSTONE and member["size"] != 0:
        raise SegmentFormatError("TOMBSTONE carries no payload")
    if kind in (KIND_APPEND, KIND_SEAL):
        _require_int(member.get("base_offset"), "member.base_offset")
        _require_int(member.get("end_offset"), "member.end_offset")
        if member["end_offset"] - member["base_offset"] != member["size"]:
            raise SegmentFormatError("member range does not match payload size")
    if kind == KIND_SEAL:
        validate_relpath(member.get("source_path"))
        _require_sha(member.get("final_sha256"), "member.final_sha256")
        if member.get("final_size") != member.get("end_offset"):
            raise SegmentFormatError("SEAL final_size must equal end_offset")
    if kind == KIND_REWRITE:
        _require_int(member.get("generation"), "member.generation", 1)
    if kind == KIND_BASELINE:
        _require_int(member.get("base_offset"), "member.base_offset")
        _require_sha(member.get("source_sha256"), "member.source_sha256")
        if member["size"] > member["base_offset"]:
            raise SegmentFormatError("BASELINE preamble cannot exceed its base_offset")


def validate_manifest(manifest: dict, *, prefix: str, expected_seq: int) -> None:
    if not isinstance(manifest, dict) or manifest.get("schema") != MANIFEST_SCHEMA:
        raise SegmentFormatError("manifest schema mismatch")
    if manifest.get("seq") != expected_seq:
        raise SegmentFormatError(
            f"manifest seq {manifest.get('seq')!r} != expected {expected_seq} (gap or reorder)"
        )
    if manifest.get("segment_key") != segment_key(prefix, expected_seq):
        raise SegmentFormatError("manifest segment_key mismatch")
    _require_sha(manifest.get("prev_manifest_sha256"), "prev_manifest_sha256")
    _require_sha(manifest.get("segment_sha256"), "segment_sha256")
    _require_int(manifest.get("segment_size"), "segment_size", 1)
    members = manifest.get("members")
    if not isinstance(members, list) or manifest.get("member_count") != len(members):
        raise SegmentFormatError("manifest member list mismatch")
    for index, member in enumerate(members):
        validate_member(member, index)


def parse_manifest(raw: bytes, *, prefix: str, expected_seq: int) -> dict:
    try:
        manifest = json.loads(raw.decode("ascii"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SegmentFormatError(f"manifest is not canonical JSON: {exc}") from exc
    if canonical_json(manifest) != raw:
        raise SegmentFormatError("manifest bytes are not canonical")
    validate_manifest(manifest, prefix=prefix, expected_seq=expected_seq)
    return manifest


def verify_chain_link(manifest: dict, previous_manifest_sha256: str) -> None:
    if manifest["prev_manifest_sha256"] != previous_manifest_sha256:
        raise SegmentFormatError(
            f"chain break at seq {manifest['seq']}: prev_manifest_sha256 does not match"
        )


def verify_segment(manifest: dict, raw: bytes) -> list[bytes]:
    if len(raw) != manifest["segment_size"] or sha256_bytes(raw) != manifest["segment_sha256"]:
        raise SegmentFormatError(f"segment {manifest['seq']} size/sha256 mismatch")
    payloads = read_segment(raw, manifest["member_count"])
    for member, payload in zip(manifest["members"], payloads):
        if len(payload) != member["size"] or sha256_bytes(payload) != member["sha256"]:
            raise SegmentFormatError(
                f"segment {manifest['seq']} member {member['index']} sha256 mismatch"
            )
    return payloads


def build_ack(*, through_seq: int, manifest_sha256: str, applied_at: str, verifier_version: str) -> bytes:
    seq_token(through_seq)
    _require_sha(manifest_sha256, "manifest_sha256")
    return canonical_json({
        "schema": ACK_SCHEMA,
        "through_seq": through_seq,
        "manifest_sha256": manifest_sha256,
        "applied_at": applied_at,
        "verifier_version": verifier_version,
    })


def parse_ack(raw: bytes) -> dict:
    try:
        ack = json.loads(raw.decode("ascii"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SegmentFormatError(f"ack is not JSON: {exc}") from exc
    if not isinstance(ack, dict) or ack.get("schema") != ACK_SCHEMA:
        raise SegmentFormatError("ack schema mismatch")
    seq_token(ack.get("through_seq"))
    _require_sha(ack.get("manifest_sha256"), "ack.manifest_sha256")
    return ack
