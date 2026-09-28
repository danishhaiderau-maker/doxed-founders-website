"""Bounded original-component bindings; no SQLite opens or payload hashing.

Bindings are expected hashes, not proof of transferred bytes. Consumers must
verify the full payload hash and generation before promotion/acknowledgement.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import time

MAX_RECEIPT_BYTES = 1024 * 1024
SCHEMA = 'lifecycle_index_rotation_recovery_receipt_v1'
PREFIX = ('v3', 'lifecycle_bundle_index', 'recovery-quarantine')
COMPONENTS = frozenset({'lifecycle_index.sqlite3', 'lifecycle_index.sqlite3-wal',
                        'lifecycle_index.sqlite3-shm'})


def _regular(path):
    for part in (path, *path.parents):
        info = part.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('QUARANTINE_LINK_REFUSED')
    info = path.stat()
    if not stat.S_ISREG(info.st_mode):
        raise ValueError('QUARANTINE_REGULAR_FILE_REQUIRED')
    return info


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError('QUARANTINE_DUPLICATE_KEY')
        result[key] = value
    return result


def original_component_binding(runtime_root, relative_path):
    """Return None outside the exact original-component namespace; fail closed inside.

    Does not authorize deletion, imply recovery completion, or admit retired DBs.
    """
    if not isinstance(relative_path, str) or '\\' in relative_path:
        raise ValueError('QUARANTINE_RELATIVE_PATH_INVALID')
    parts = relative_path.split('/')
    if any(part in ('', '.', '..') for part in parts) or Path(relative_path).is_absolute():
        raise ValueError('QUARANTINE_RELATIVE_PATH_INVALID')
    if tuple(parts[:3]) != PREFIX or len(parts) != 5 or parts[-1] not in COMPONENTS:
        return None
    if not re.fullmatch('[0-9a-f]{16}', parts[3]):
        raise ValueError('QUARANTINE_ID_INVALID')
    root = Path(os.path.abspath(runtime_root))
    target = root.joinpath(*parts)
    before = _regular(target)
    receipt_path = target.parent / 'receipt.json'
    receipt_stat = _regular(receipt_path)
    if not 0 < receipt_stat.st_size <= MAX_RECEIPT_BYTES:
        raise ValueError('QUARANTINE_RECEIPT_SIZE_INVALID')
    with receipt_path.open('rb') as handle:
        if _identity(os.fstat(handle.fileno())) != _identity(receipt_stat):
            raise ValueError('QUARANTINE_RECEIPT_CHANGED')
        raw = handle.read(MAX_RECEIPT_BYTES + 1)
    if len(raw) != receipt_stat.st_size or _identity(_regular(receipt_path)) != _identity(receipt_stat):
        raise ValueError('QUARANTINE_RECEIPT_CHANGED')
    receipt = json.loads(raw.decode('utf-8'), object_pairs_hook=_pairs)
    if not isinstance(receipt, dict):
        raise ValueError('QUARANTINE_RECEIPT_INVALID')
    claimed = receipt.pop('receipt_sha256', None)
    canonical = json.dumps(receipt, separators=(',', ':'), sort_keys=True, allow_nan=False).encode()
    if claimed != hashlib.sha256(canonical).hexdigest():
        raise ValueError('QUARANTINE_RECEIPT_HASH_MISMATCH')
    recovery_id = receipt.get('recovery_id')
    if (receipt.get('schema') != SCHEMA or receipt.get('status') != 'QUARANTINED'
            or not isinstance(recovery_id, str) or not re.fullmatch('[0-9a-f]{64}', recovery_id)
            or recovery_id[:16] != parts[3]):
        raise ValueError('QUARANTINE_RECEIPT_IDENTITY_INVALID')
    rows = receipt.get('components')
    if not isinstance(rows, list) or not 1 <= len(rows) <= 3:
        raise ValueError('QUARANTINE_COMPONENTS_INVALID')
    names = set()
    for row in rows:
        if (not isinstance(row, dict) or row.get('name') not in COMPONENTS
                or row['name'] in names or type(row.get('size')) is not int or row['size'] < 0
                or not isinstance(row.get('sha256'), str) or not re.fullmatch('[0-9a-f]{64}', row['sha256'])):
            raise ValueError('QUARANTINE_COMPONENTS_INVALID')
        names.add(row['name'])
    if 'lifecycle_index.sqlite3' not in names:
        raise ValueError('QUARANTINE_DATABASE_MISSING')
    selected = next((row for row in rows if row['name'] == parts[-1]), None)
    if selected is None or before.st_size != selected['size']:
        raise ValueError('QUARANTINE_COMPONENT_MISMATCH')
    if _identity(_regular(target)) != _identity(before):
        raise ValueError('QUARANTINE_COMPONENT_CHANGED')
    return {'schema': 'quarantine_original_component_binding_v1',
            'path': relative_path, 'consistency_mode': 'strict_generation_v1',
            'size': before.st_size, 'mtime_ns': before.st_mtime_ns, 'inode': before.st_ino,
            'expected_sha256': selected['sha256'], 'receipt_sha256': claimed,
            'receipt_file_sha256': hashlib.sha256(raw).hexdigest(), 'recovery_id': recovery_id,
            'required_components': sorted(names), 'payload_hash_verified': False}


def verify_original_component_group(runtime_root, relative_path, *, maximum_bytes=4 * 1024**3,
                                    timeout_seconds=120):
    """Read-only complete-group byte proof; not cleanup or transfer ACK authority.

    Memory is bounded to 1 MiB; work is bounded by byte budget and deadline.
    Caller must still prevent writers between this receipt and any later use.
    """
    if (type(maximum_bytes) is not int or not 0 < maximum_bytes <= 64 * 1024**3
            or type(timeout_seconds) not in (int, float) or not 0 < timeout_seconds <= 600):
        raise ValueError('QUARANTINE_VERIFY_BUDGET_INVALID')
    deadline = time.monotonic() + timeout_seconds
    def check_time():
        if time.monotonic() >= deadline:
            raise TimeoutError('QUARANTINE_VERIFY_DEADLINE')
    initial = original_component_binding(runtime_root, relative_path)
    if initial is None:
        raise ValueError('QUARANTINE_ORIGINAL_COMPONENT_REQUIRED')
    parent = relative_path.rsplit('/', 1)[0]
    bindings = [original_component_binding(runtime_root, parent + '/' + name)
                for name in initial['required_components']]
    if any(item['receipt_file_sha256'] != initial['receipt_file_sha256'] for item in bindings):
        raise ValueError('QUARANTINE_RECEIPT_CHANGED')
    if sum(item['size'] for item in bindings) > maximum_bytes:
        raise ValueError('QUARANTINE_VERIFY_BYTE_BUDGET')
    root = Path(os.path.abspath(runtime_root))
    verified = []
    for binding in bindings:
        check_time()
        path = root.joinpath(*binding['path'].split('/'))
        before = _regular(path)
        if (before.st_size, before.st_mtime_ns, before.st_ino) != (
                binding['size'], binding['mtime_ns'], binding['inode']):
            raise ValueError('QUARANTINE_COMPONENT_CHANGED')
        digest = hashlib.sha256()
        remaining = before.st_size
        with path.open('rb') as handle:
            if _identity(os.fstat(handle.fileno())) != _identity(before):
                raise ValueError('QUARANTINE_COMPONENT_CHANGED')
            while remaining:
                check_time()
                block = handle.read(min(1024 * 1024, remaining))
                if not block:
                    raise ValueError('QUARANTINE_COMPONENT_CHANGED')
                digest.update(block)
                remaining -= len(block)
            if handle.read(1) or _identity(os.fstat(handle.fileno())) != _identity(before):
                raise ValueError('QUARANTINE_COMPONENT_CHANGED')
        if _identity(_regular(path)) != _identity(before):
            raise ValueError('QUARANTINE_COMPONENT_CHANGED')
        if digest.hexdigest() != binding['expected_sha256']:
            raise ValueError('QUARANTINE_PAYLOAD_HASH_MISMATCH')
        verified.append({**binding, 'payload_hash_verified': True})
    # A later component must not permit an earlier component/receipt to change.
    for binding in bindings:
        check_time()
        if original_component_binding(runtime_root, binding['path']) != binding:
            raise ValueError('QUARANTINE_GROUP_CHANGED')
    result = {'schema': 'quarantine_original_group_verification_v1',
              'recovery_id': initial['recovery_id'], 'receipt_file_sha256': initial['receipt_file_sha256'],
              'components': verified, 'group_complete': True, 'payload_hash_verified': True,
              'verified_bytes': sum(item['size'] for item in bindings), 'cleanup_authorized': False}
    result['verification_sha256'] = hashlib.sha256(json.dumps(
        result, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
    return result
