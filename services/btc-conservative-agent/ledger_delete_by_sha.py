"""Delete V3 ledger rows by exact row sha256 (RK PASS 24 Oct, R17 10,771 unplaced analyzer rows).

Root cause: #864's delete-by-id only covers trades_3factor.csv and #865 only *excludes* unplaced
ledger rows; no sanctioned mechanism existed to remove rows from the V3 JSONL ledgers.  This module is
the only writer for that operation; bot.py wraps it in admin-token routes.

Contract
* Input is the RK list as JSONL text (``list_jsonl``); ``list_sha256`` = sha256 of those exact bytes,
  so the RK file sha (e.g. d0dcf920...f473) is the list sha.  Each line carries ``stream``
  (``v3/ledgers/<name>.jsonl``) and ``row_sha256`` = sha256 of the ledger line without its line
  terminator (same definition as analyzer_epoch_guard).  Optional ``multiplicity`` (int >= 1) is the
  ONLY way to delete identical duplicate lines.  Every other field is ignored: deletion is by sha only.
* Only the four streams decision / lifecycle / market_segment / order_intent; paths come from
  ``V3EvidenceStore.ledger_path`` -- never from the request.
* All involved ledgers are locked (store ``_exclusive``, sorted order) for validation AND write.
* Refusals (nothing written) -- see ``REFUSAL_CODES``.
* dry_run defaults to True: full validation, no writes, no snapshot.
* Real run per file: rollback snapshot (ledger + every receipt/index file touched), then
  tmp + fsync + os.replace + dir fsync of the ledger; after == before - n exactly and the hash of
  the remaining rows is unchanged, else the snapshot is restored and the call fails.
* Index / cursor maintenance (legacy generation-0 receipts, the production layout):
  - receipts of deleted rows are removed; receipts of surviving rows are rebased to their new
    byte offsets (all other receipt fields preserved byte-for-byte in meaning);
  - complete.json and bootstrap.json are re-bound to the new file signature and tail anchor;
  - lifecycle membership is invalidated (new generation_id => every episode marker stops
    matching => optional lifecycle writes fail closed until re-published);
  - the in-process durable-id cache for the ledger is dropped.
* No resurrection: deleted shas are recorded in ``v3/receipts/deleted_row_tombstones_v1``
  (hashes only, no row data) and ``V3EvidenceStore.append`` refuses a row whose line hashes to one.
* Snapshots are temporary: ``delete_rollback_snapshot`` removes one and reads back that it is
  gone.  They are never archived.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import secrets
import shutil
import time
import uuid
from pathlib import Path
from typing import Any

ALLOWED_LEDGERS = ("decision", "lifecycle", "market_segment", "order_intent")
SNAPSHOT_DIRNAME = ".ledger_delete_rollback"
TOMBSTONE_DIRNAME = "deleted_row_tombstones_v1"
TOMBSTONE_PIN_KEY = "deleted_row_tombstones_integrity_sha256"
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
MANIFEST_NAME = "manifest.json"
APPLIED_MARKER = "applied.json"
RESTORED_MARKER = "restored.json"
_SNAPSHOT_RE = re.compile(r"^[0-9]{13}-[0-9a-f]{12}$")
_STREAM_RE = re.compile(r"^v3/ledgers/([a-z_]+)\.jsonl$")

REFUSAL_CODES = (
    "EMPTY_LIST", "LIST_SHA_MISMATCH", "LIST_PARSE_ERROR", "ENTRY_INVALID", "STREAM_NOT_ALLOWED",
    "LIST_DUPLICATE_ENTRY", "TRANSACTIONAL_AUTHORITY_ACTIVE", "LEDGER_GENERATION_NOT_SUPPORTED",
    "APPEND_HEAD_PENDING", "LEDGER_MISSING", "PARTIAL_TRAILING_LINE", "INDEX_NOT_COMPLETE",
    "ROW_MISSING", "DUPLICATE_LINES_WITHOUT_MULTIPLICITY", "MULTIPLICITY_MISMATCH",
    "ROW_UNPARSEABLE", "RECORD_ID_SHARED_WITH_SURVIVOR", "RECEIPT_MISSING", "RECEIPT_INVALID",
    "INVARIANT_FAILED_RESTORED", "SNAPSHOT_PATH_NOT_ALLOWED", "SNAPSHOT_MISSING",
    "MULTI_STREAM_LIST_REFUSED", "DELETED_ROW_TOMBSTONES_MISSING", "DELETED_ROW_TOMBSTONES_CORRUPT",
    "SNAPSHOT_INCOMPLETE_DELETE", "DELETED_ROW_TOMBSTONES_PIN_MISSING",
    "DELETED_ROW_TOMBSTONES_INDEX_MISSING",
)


class DeleteRefused(ValueError):
    def __init__(self, code: str, detail: Any = None):
        super().__init__(code)
        self.code = code
        self.detail = detail


def list_sha256(list_jsonl: str) -> str:
    return hashlib.sha256(list_jsonl.encode("utf-8")).hexdigest()


def _line_sha(line: bytes) -> str:
    return hashlib.sha256(line.rstrip(b"\r\n")).hexdigest()


def _rows_hash(lines: list[bytes]) -> str:
    h = hashlib.sha256()
    for line in lines:
        h.update(line.rstrip(b"\r\n"))
        h.update(b"\n")
    return h.hexdigest()


def _fsync_dir(path: Path) -> None:
    if os.name == "nt":
        return
    fd = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_atomic(path: Path, data: bytes) -> None:
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(6)}.tmp")
    try:
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()
    _fsync_dir(path.parent)


def _copy_fsync(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    with open(src, "rb") as a, open(dst, "wb") as b:
        shutil.copyfileobj(a, b)
        b.flush()
        os.fsync(b.fileno())


def parse_list(list_jsonl: Any, sha: Any) -> dict[str, dict[str, int]]:
    """Return {ledger: {row_sha256: multiplicity}}; refuse on any malformed or duplicate entry."""
    if not isinstance(list_jsonl, str) or not list_jsonl.strip():
        raise DeleteRefused("EMPTY_LIST")
    if not isinstance(sha, str) or sha != list_sha256(list_jsonl):
        raise DeleteRefused("LIST_SHA_MISMATCH")
    out: dict[str, dict[str, int]] = {}
    for n, raw in enumerate(list_jsonl.splitlines(), 1):
        if not raw.strip():
            continue
        try:
            entry = json.loads(raw)
        except json.JSONDecodeError:
            raise DeleteRefused("LIST_PARSE_ERROR", {"line": n})
        if not isinstance(entry, dict):
            raise DeleteRefused("LIST_PARSE_ERROR", {"line": n})
        row_sha = entry.get("row_sha256")
        mult = entry.get("multiplicity", 1)
        if (not isinstance(row_sha, str) or not _SHA_RE.match(row_sha)
                or isinstance(mult, bool) or not isinstance(mult, int) or mult < 1):
            raise DeleteRefused("ENTRY_INVALID", {"line": n})
        m = _STREAM_RE.match(str(entry.get("stream") or ""))
        if not m or m.group(1) not in ALLOWED_LEDGERS:
            raise DeleteRefused("STREAM_NOT_ALLOWED", {"line": n, "stream": str(entry.get("stream"))[:80]})
        bucket = out.setdefault(m.group(1), {})
        if row_sha in bucket:
            raise DeleteRefused("LIST_DUPLICATE_ENTRY", {"line": n, "row_sha256": row_sha})
        bucket[row_sha] = mult
    if not out:
        raise DeleteRefused("EMPTY_LIST")
    if len(out) > 1:
        # HM R2 blocker: multi-ledger applies are not atomic; one stream per call, enforced.
        raise DeleteRefused("MULTI_STREAM_LIST_REFUSED", {"streams": sorted(out)})
    return out


def _receipt_ok(store, ledger: str, receipt: dict | None, offset: int, line: bytes, record_id: str) -> bool:
    return bool(
        isinstance(receipt, dict)
        and receipt.get("schema") == "emergency_record_idempotency_v1"
        and receipt.get("state") == "COMMITTED"
        and receipt.get("ledger") == ledger and receipt.get("record_id") == record_id
        and int(receipt.get("offset", -1)) == offset and int(receipt.get("length", -1)) == len(line)
        and receipt.get("row_sha256") == hashlib.sha256(line).hexdigest()
    )


def _plan_ledger(store, ledger: str, wanted: dict[str, int]) -> dict[str, Any]:
    from research_v3_store import _path_signature
    from transactional_receipt_store import read_legacy_receipt

    gen = store._active_ledger_generation(ledger)
    if int(gen.get("generation", -1)) != 0:
        raise DeleteRefused("LEDGER_GENERATION_NOT_SUPPORTED", {"ledger": ledger})
    if store._append_head_path(ledger).exists():
        raise DeleteRefused("APPEND_HEAD_PENDING", {"ledger": ledger})
    path = store.ledger_path(ledger)
    if not path.is_file():
        raise DeleteRefused("LEDGER_MISSING", {"ledger": ledger})
    load_tombstones(store.root, ledger)  # fail closed on a missing/corrupt tombstone file
    signature = _path_signature(path)
    if not store._complete_generation(ledger, signature):
        raise DeleteRefused("INDEX_NOT_COMPLETE", {"ledger": ledger})
    data = path.read_bytes()
    if data and not data.endswith(b"\n"):
        raise DeleteRefused("PARTIAL_TRAILING_LINE", {"ledger": ledger})
    lines = data.splitlines(keepends=True)
    found: dict[str, int] = {}
    for line in lines:
        s = _line_sha(line)
        if s in wanted:
            found[s] = found.get(s, 0) + 1
    missing = sorted(s for s in wanted if s not in found)
    if missing:
        raise DeleteRefused("ROW_MISSING", {"ledger": ledger, "count": len(missing), "first": missing[:5]})
    for s, k in found.items():
        if k != wanted[s]:
            code = "DUPLICATE_LINES_WITHOUT_MULTIPLICITY" if wanted[s] == 1 else "MULTIPLICITY_MISMATCH"
            raise DeleteRefused(code, {"ledger": ledger, "row_sha256": s, "found": k, "listed": wanted[s]})
    keep: list[tuple[int, bytes, str]] = []   # (old_offset, line, record_id)
    drop: list[tuple[int, bytes, str]] = []
    offset = 0
    for line in lines:
        try:
            rid = str(json.loads(line).get("record_id") or "")
        except (json.JSONDecodeError, AttributeError, UnicodeDecodeError):
            rid = ""
        (drop if _line_sha(line) in wanted else keep).append((offset, line, rid))
        offset += len(line)
    if any(not rid for _o, _l, rid in drop):
        raise DeleteRefused("ROW_UNPARSEABLE", {"ledger": ledger})
    shared = {rid for _o, _l, rid in drop} & {rid for _o, _l, rid in keep}
    if shared:
        raise DeleteRefused("RECORD_ID_SHARED_WITH_SURVIVOR", {"ledger": ledger, "count": len(shared)})
    receipts: dict[str, dict] = {}
    for o, line, rid in drop + keep:
        if not rid:
            continue  # survivor without record_id carries no receipt (legacy row)
        r = read_legacy_receipt(store.root, ledger, rid)
        if r is None:
            raise DeleteRefused("RECEIPT_MISSING", {"ledger": ledger, "record_id": rid[:80]})
        if not _receipt_ok(store, ledger, r, o, line, rid):
            raise DeleteRefused("RECEIPT_INVALID", {"ledger": ledger, "record_id": rid[:80]})
        receipts[rid] = r
    kept_lines = [l for _o, l, _r in keep]
    return {
        "ledger": ledger, "path": path, "signature": signature, "data": data,
        "keep": keep, "drop": drop, "receipts": receipts,
        "before_count": len(lines), "removed_count": len(drop), "after_count": len(keep),
        "remaining_rows_sha256": _rows_hash(kept_lines),
    }


def _tombstone_path(store, ledger: str) -> Path:
    return store.receipt_dir / TOMBSTONE_DIRNAME / f"{ledger}.json"


def tombstone_integrity(shas) -> str:
    return hashlib.sha256("\n".join(sorted(shas)).encode("ascii")).hexdigest()


def tombstone_state(root: str | Path, ledger: str) -> tuple[bool, frozenset[str], str]:
    """(ok, shas, reason).  Fail closed when deletes are recorded but the tombstone file is missing/corrupt,
    or when the tombstone file exists but complete.json / its pin is gone.

    Limitations (documented for HM R2b):
      * Legacy (generation-0, receipt-file) backend only.  delete_ledger_rows_by_sha refuses
        TRANSACTIONAL_AUTHORITY_ACTIVE, and the transactional backend's set_ledger_complete does NOT carry
        the integrity pin.  If a stream with tombstones is later migrated to the transactional backend,
        its complete.json stops being maintained and appends to that stream fail closed
        (PIN_MISSING / INDEX_MISSING) until the pin is ported to the backend.  Do not enable transactional
        authority on a stream with tombstones without that port.
      * If BOTH the tombstone file and complete.json are removed, absence is indistinguishable from
        "no deletes ever"; complete.json loss also forces a full re-index, which is alerted separately.

    The expected integrity hash is pinned in the ledger's complete.json (rebound by every delete), and the
    tombstone file carries its own integrity hash; both must agree with the file's sha set.
    """
    root = Path(root).resolve()
    p = root / "v3" / "receipts" / TOMBSTONE_DIRNAME / f"{ledger}.json"
    comp_p = root / "v3" / "receipts" / "emergency_record_idempotency_v1" / ledger / "complete.json"
    expected = None
    comp_ok = False
    try:
        expected = json.loads(comp_p.read_text("utf-8")).get(TOMBSTONE_PIN_KEY)
        comp_ok = True
    except (FileNotFoundError, ValueError, AttributeError, UnicodeDecodeError):
        expected = None
    try:
        row = json.loads(p.read_text("utf-8"))
    except FileNotFoundError:
        if expected:
            return False, frozenset(), "DELETED_ROW_TOMBSTONES_MISSING"
        return True, frozenset(), ""
    except (ValueError, UnicodeDecodeError, OSError):
        return False, frozenset(), "DELETED_ROW_TOMBSTONES_CORRUPT"
    shas = row.get("row_sha256") if isinstance(row, dict) else None
    if (not isinstance(shas, list) or not all(isinstance(x, str) and _SHA_RE.match(x) for x in shas)
            or row.get("integrity_sha256") != tombstone_integrity(shas)
            or (expected is not None and expected != row.get("integrity_sha256"))):
        return False, frozenset(), "DELETED_ROW_TOMBSTONES_CORRUPT"
    if shas and expected is None:
        # HM R2b: deletes are recorded but complete.json (or its pin) was lost; the tombstone file can no
        # longer be cross-checked, so fail closed instead of trusting it.
        return False, frozenset(), ("DELETED_ROW_TOMBSTONES_PIN_MISSING" if comp_ok
                                    else "DELETED_ROW_TOMBSTONES_INDEX_MISSING")
    return True, frozenset(shas), ""


def load_tombstones(root: str | Path, ledger: str) -> frozenset[str]:
    ok, shas, reason = tombstone_state(root, ledger)
    if not ok:
        raise DeleteRefused(reason, {"ledger": ledger})
    return shas


def _apply_ledger(store, plan: dict[str, Any], snap: Path) -> dict[str, Any]:
    from research_v3_store import _id_cache, _path_signature
    from transactional_receipt_store import legacy_receipt_path

    ledger, path = plan["ledger"], plan["path"]
    rdir = store.receipt_dir / "emergency_record_idempotency_v1" / ledger
    membership = store._lifecycle_membership_dir / "current.json"
    touched = [path, store._completeness_path(ledger), store._bootstrap_path(ledger), _tombstone_path(store, ledger)]
    if ledger == "lifecycle":
        touched.append(membership)
    touched += [legacy_receipt_path(store.root, ledger, rid) for rid in plan["receipts"]]
    restore: list[tuple[Path, Path | None]] = []
    for i, p in enumerate(touched):
        if p.exists():
            dst = snap / ledger / f"{i:06d}.bin"
            _copy_fsync(p, dst)
            restore.append((p, dst))
        else:
            restore.append((p, None))
    # Manifest is published atomically only after every snapshot copy is durable; no ledger
    # file is touched before it exists, so a process death before this point changed nothing.
    _write_atomic(snap / ledger / MANIFEST_NAME, json.dumps(
        [[str(p.relative_to(store.root)), None if d is None else d.name] for p, d in restore]).encode())
    _fsync_dir(snap)

    def _restore() -> None:
        _restore_from_manifest(store, snap / ledger)

    try:
        new_data = b"".join(l for _o, l, _r in plan["keep"])
        _write_atomic(path, new_data)
        _id_cache.pop(str(path.resolve()), None)
        # receipts: drop deleted, rebase survivors
        for _o, _l, rid in plan["drop"]:
            legacy_receipt_path(store.root, ledger, rid).unlink()
        offset = 0
        for old, line, rid in plan["keep"]:
            if rid and old != offset:
                r = dict(plan["receipts"][rid])
                r["offset"] = offset
                store._atomic_json_receipt(legacy_receipt_path(store.root, ledger, rid), r, fsync_dir=False)
            offset += len(line)
        _fsync_dir(rdir)
        sig = _path_signature(path)
        anchor = None
        if plan["keep"]:
            last = plan["keep"][-1][1]
            anchor = {"offset": offset - len(last), "length": len(last),
                      "sha256": hashlib.sha256(last).hexdigest()}
        sigp = None if sig is None else store._signature_payload(sig)
        tomb = set(load_tombstones(store.root, ledger)) | {_line_sha(l) for _o, l, _r in plan["drop"]}
        integrity = tombstone_integrity(tomb)
        store._atomic_json_receipt(_tombstone_path(store, ledger), {
            "schema": "v3_deleted_row_tombstones_v1", "ledger": ledger, "row_sha256": sorted(tomb),
            "integrity_sha256": integrity})
        comp = json.loads(store._completeness_path(ledger).read_text("utf-8"))
        comp.update({"ledger_signature": sigp, "tail_anchor": anchor, TOMBSTONE_PIN_KEY: integrity})
        store._atomic_json_receipt(store._completeness_path(ledger), comp)
        bpath = store._bootstrap_path(ledger)
        if bpath.exists():
            boot = json.loads(bpath.read_text("utf-8"))
            boot.update({"source": sigp, "cursor": sig[2] if sig else 0, "cursor_anchor": anchor})
            store._atomic_json_receipt(bpath, boot)
        if ledger == "lifecycle" and membership.exists():
            cur = json.loads(membership.read_text("utf-8"))
            cur.update({"generation_id": str(uuid.uuid4()), "lifecycle_ledger": sigp, "tail_anchor": anchor})
            store._atomic_json_receipt(membership, cur)
        # read back
        after = path.read_bytes().splitlines(keepends=True)
        ok = (len(after) == plan["before_count"] - plan["removed_count"]
              and _rows_hash(after) == plan["remaining_rows_sha256"]
              and store._complete_generation(ledger, _path_signature(path)))
    except Exception:
        _restore()
        raise
    if not ok:
        _restore()
        raise DeleteRefused("INVARIANT_FAILED_RESTORED", {"ledger": ledger})
    _write_atomic(snap / ledger / APPLIED_MARKER, b'{"applied": true}')
    return {"after_count_read_back": len(after), "remaining_rows_sha256_read_back": _rows_hash(after)}


def delete_ledger_rows_by_sha(store, list_jsonl: Any, sha: Any, *, dry_run: bool = True,
                              now=time.time) -> dict[str, Any]:
    wanted = parse_list(list_jsonl, sha)
    if store._transactional_receipts() is not None:
        raise DeleteRefused("TRANSACTIONAL_AUTHORITY_ACTIVE")
    ledgers = sorted(wanted)
    with contextlib.ExitStack() as stack:
        for ledger in ledgers:
            stack.enter_context(store._exclusive(store.ledger_path(ledger)))
        plans = [_plan_ledger(store, ledger, wanted[ledger]) for ledger in ledgers]
        per_file = {p["ledger"]: {k: p[k] for k in ("before_count", "removed_count", "after_count",
                                                    "remaining_rows_sha256")} for p in plans}
        result: dict[str, Any] = {
            "dry_run": bool(dry_run), "list_sha256": sha, "files": per_file,
            "removed_total": sum(p["removed_count"] for p in plans), "snapshot_path": None,
        }
        if dry_run:
            return result
        snap = store.root / "v3" / SNAPSHOT_DIRNAME / f"{int(now() * 1000):013d}-{secrets.token_hex(6)}"
        snap.mkdir(parents=True)
        result["snapshot_path"] = str(snap)
        for plan in plans:
            per_file[plan["ledger"]].update(_apply_ledger(store, plan, snap))
        return result


def _restore_from_manifest(store, ledger_snap: Path) -> list[str]:
    """Put every file listed in the manifest back byte-identically (or remove it if it did not exist)."""
    from research_v3_store import _id_cache

    root = Path(store.root).resolve()
    manifest = json.loads((ledger_snap / MANIFEST_NAME).read_text("utf-8"))
    restored = []
    for rel, blob in manifest:
        target = (root / rel).resolve()
        if root / "v3" not in target.parents:
            raise DeleteRefused("SNAPSHOT_PATH_NOT_ALLOWED", {"path": rel[:120]})
        if blob is None:
            if target.exists():
                target.unlink()
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            _write_atomic(target, (ledger_snap / blob).read_bytes())
        restored.append(rel)
    _id_cache.pop(str(store.ledger_path(ledger_snap.name).resolve()), None)
    _write_atomic(ledger_snap / RESTORED_MARKER, b'{"restored": true}')
    return restored


def _incomplete(ledger_snap: Path) -> bool:
    return ((ledger_snap / MANIFEST_NAME).exists() and not (ledger_snap / APPLIED_MARKER).exists()
            and not (ledger_snap / RESTORED_MARKER).exists())


def restore_incomplete_deletes(store) -> list[dict[str, Any]]:
    """Boot-time recovery (HM R2): a process death mid-_apply_ledger leaves a snapshot with a manifest but
    neither applied.json nor restored.json.  Restore those ledgers from the snapshot under the ledger lock."""
    base = store.root / "v3" / SNAPSHOT_DIRNAME
    out: list[dict[str, Any]] = []
    if not base.is_dir():
        return out
    for snap in sorted(base.iterdir()):
        if not _SNAPSHOT_RE.match(snap.name) or not snap.is_dir():
            continue
        for ledger_snap in sorted(snap.iterdir()):
            if ledger_snap.name not in ALLOWED_LEDGERS or not _incomplete(ledger_snap):
                continue
            with store._exclusive(store.ledger_path(ledger_snap.name)):
                if not _incomplete(ledger_snap):
                    continue
                files = _restore_from_manifest(store, ledger_snap)
            out.append({"snapshot_path": str(snap), "ledger": ledger_snap.name, "restored_files": len(files)})
    return out


def delete_rollback_snapshot(store, snapshot_path: Any) -> dict[str, Any]:
    """Remove one rollback snapshot (only inside the snapshot dir) and read back that it is gone."""
    base = (store.root / "v3" / SNAPSHOT_DIRNAME).resolve()
    p = Path(os.path.realpath(str(snapshot_path or "")))
    if p.parent != base or not _SNAPSHOT_RE.match(p.name):
        raise DeleteRefused("SNAPSHOT_PATH_NOT_ALLOWED")
    if not p.is_dir():
        raise DeleteRefused("SNAPSHOT_MISSING")
    if any(_incomplete(d) for d in p.iterdir() if d.is_dir()):
        raise DeleteRefused("SNAPSHOT_INCOMPLETE_DELETE")
    shutil.rmtree(p)
    _fsync_dir(base)
    exists = p.exists()
    return {"snapshot_path": str(p), "deleted": not exists, "exists_after": exists}
