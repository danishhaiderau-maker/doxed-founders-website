"""Explicit local reset composition; no process stopping or authority discovery.

The operator must provide a trusted fresh owner/recovery auditor. A supplied
JSON boolean is not such an auditor. This module does not authenticate Fly.
The caller obtains and pins the actual completed Fly operation separately.
"""
import hashlib
import json
import re
import time
import os
import tempfile
from pathlib import Path

from research.canonical_generation_retirement import metadata_snapshot, retire_canonical_generation, RETIRED_MARKER
from research.mirror_generation_lease import MirrorGenerationLease, LEASE_FILE_NAME
from research_exact_deletion import _checked_path, ResearchDeletionRejected, reconcile_research_deletion
from research_reset_execution import execute_research_reset
from research_reset_inventory import _proof_valid


def _reject(code):
    raise ResearchDeletionRejected(code)


def _read(path):
    if path.stat().st_size > 16 * 1024**2:
        _reject('LOCAL_RESET_RECEIPT_LIMIT')
    return json.loads(path.read_text(encoding='utf-8'))


def _complete_receipt(path, root):
    receipt = _read(_checked_path(path, root))
    if (receipt.get('schema') != 'research_exact_deletion_v1' or receipt.get('status') != 'COMPLETE'
            or receipt.get('root') != str(root) or receipt.get('receipt_path') != str(path)):
        _reject('LOCAL_RESET_COMPLETION_UNVERIFIED')
    reconciliation = reconcile_research_deletion(path)
    rows = reconciliation.get('rows', [])
    if (len(rows) != len(receipt.get('inventory', []))
            or any(row.get('status') != 'UNLINKED_CONFIRMED' for row in rows)):
        _reject('LOCAL_RESET_COMPLETION_UNVERIFIED')
    return receipt


def _verify_raw_binding(receipt, journal):
    expected = journal.get('raw_expected_sha256_by_path')
    plan = journal.get('raw_target_plan_sha256')
    if (not isinstance(expected, dict) or not isinstance(plan, str)
            or not re.fullmatch(r'[0-9a-f]{64}', plan)
            or receipt.get('context', {}).get('proof_sha256') != journal.get('raw_proof_sha256')
            or hashlib.sha256(json.dumps(expected, sort_keys=True, separators=(',', ':')).encode()).hexdigest() != plan
            or receipt.get('expected_sha256_by_path') != expected
            or len(receipt.get('inventory', [])) != len(expected)
            or {row['path']: row['sha256'] for row in receipt['inventory']} != expected):
        _reject('LOCAL_RESET_RAW_TARGET_BINDING_MISMATCH')


FLY_COMPLETION_MAX_BYTES = 64 * 1024**2


def reset_local_research(*, root, lease, fly_receipt_bytes, fly_receipt_sha256,
                         expected_new_epoch, expected_revision, audit_owners,
                         journal_path, validate_only=True):
    """Hold the same real mirror lease throughout validation and both deletions.

    audit_owners(root) must freshly inspect actual owners and recovery state and
    return checked_at, proof, recovery_states and recovery_receipt_bytes. The
    proof's digest must bind those bytes. Caller is responsible for the trusted
    auditor's implementation. Partial exact deletions require their independent
    reconciliation; never silently re-plan around files already removed.
    """
    root = Path(root).absolute()
    if (not isinstance(lease, MirrorGenerationLease) or not lease.held
            or lease.path != root / LEASE_FILE_NAME):
        _reject('MATCHING_MIRROR_LEASE_REQUIRED')
    if type(validate_only) is not bool or not callable(audit_owners):
        _reject('LOCAL_RESET_AUDITOR_REQUIRED')
    journal_path = _checked_path(journal_path, root)
    if journal_path.parent.parent != root / 'research_reset_receipts':
        _reject('LOCAL_RESET_JOURNAL_SCOPE_INVALID')
    if (not isinstance(fly_receipt_bytes, bytes) or len(fly_receipt_bytes) > FLY_COMPLETION_MAX_BYTES
            or hashlib.sha256(fly_receipt_bytes).hexdigest() != fly_receipt_sha256):
        _reject('LOCAL_RESET_FLY_RECEIPT_HASH_MISMATCH')
    fly = json.loads(fly_receipt_bytes)
    if (fly.get('schema') != 'bot_destructive_research_reset_v1' or fly.get('stage') != 'COMPLETE'
            or fly.get('new_epoch_id') != expected_new_epoch
            or fly.get('boundary_evidence', {}).get('deployed_revision') != expected_revision
            or not re.fullmatch(r'[0-9a-f]{40}', expected_revision)
            or fly.get('proof', {}).get('new_epoch_id') != expected_new_epoch
            or fly.get('accounting_preserved') is not True):
        _reject('LOCAL_RESET_FLY_COMPLETION_INVALID')
    audit = audit_owners(root)
    if not isinstance(audit, dict):
        _reject('LOCAL_RESET_AUDIT_INVALID')
    checked = audit.get('checked_at')
    if type(checked) not in (float, int) or not 0 <= time.time() - checked <= 10:
        _reject('LOCAL_RESET_AUDIT_STALE')
    proof = audit.get('proof')
    recovery = audit.get('recovery_receipt_bytes')
    retired_identity = audit.get('retired_identity')
    retired_manifest_entry_hash = audit.get('retired_manifest_entry_hash')
    if (not isinstance(retired_identity, dict)
            or set(retired_identity) != {'epoch_id', 'source_revision', 'deployed_revision', 'tile_config_signature'}
            or not isinstance(proof, dict)
            or retired_identity.get('epoch_id') != proof.get('retired_epoch_id')
            or not isinstance(retired_manifest_entry_hash, str)
            or not re.fullmatch(r'[0-9a-f]{64}', retired_manifest_entry_hash)
            or any(not isinstance(retired_identity.get(key), str)
                   or not re.fullmatch(pattern, retired_identity[key])
                   for key, pattern in [('source_revision', r'(?:[0-9a-f]{12}|[0-9a-f]{40})'),
                                        ('deployed_revision', r'(?:[0-9a-f]{12}|[0-9a-f]{40})'),
                                        ('tile_config_signature', r'[0-9a-f]{64}')])):
        _reject('LOCAL_RESET_RETIRED_IDENTITY_INVALID')
    if (not _proof_valid(root, proof) or not isinstance(recovery, bytes)
            or hashlib.sha256(recovery).hexdigest() != proof['recovery_receipt_sha256']
            or proof['new_epoch_id'] != expected_new_epoch
            or proof['source_revision'] != retired_identity['source_revision']):
        _reject('LOCAL_RESET_AUDIT_BINDING_INVALID')
    states = audit.get('recovery_states')
    binding = {'root': str(root), 'fly_receipt_sha256': fly_receipt_sha256,
               'new_epoch_id': expected_new_epoch, 'source_revision': expected_revision,
               'retired_epoch_id': proof['retired_epoch_id'], 'retired_identity': retired_identity,
               'retired_manifest_entry_hash': retired_manifest_entry_hash}
    journal = _read(journal_path) if journal_path.exists() else None
    initial_audit_sha256 = (journal.get('binding', {}).get('initial_audit_sha256')
                            if journal else proof['recovery_receipt_sha256'])
    if (not isinstance(initial_audit_sha256, str)
            or not re.fullmatch(r'[0-9a-f]{64}', initial_audit_sha256)):
        _reject('LOCAL_RESET_INITIAL_AUDIT_BINDING_MISSING')
    binding['initial_audit_sha256'] = initial_audit_sha256
    if journal and (journal.get('schema') != 'local_research_reset_v1' or journal.get('binding') != binding):
        _reject('LOCAL_RESET_JOURNAL_BINDING_CHANGED')
    raw_path = journal_path.parent / 'raw-deletion.json'
    metadata_path = journal_path.parent / 'metadata-deletion.json'
    retired_on_disk = metadata_path.exists()
    if journal and (journal.get('stage') in {'METADATA_RETIRED', 'COMPLETE'} or retired_on_disk):
        marker = _read(_checked_path(root / RETIRED_MARKER, root))
        metadata_receipt = _complete_receipt(metadata_path, root)
        expected_hashes = {str(root / name): digest for name, digest in journal['metadata_snapshot'].items()
                           if digest is not None}
        if (marker.get('metadata_sha256') != journal.get('metadata_snapshot')
                or marker.get('retired_epoch_id') != proof['retired_epoch_id']
                or marker.get('new_epoch_id') != expected_new_epoch
                or marker.get('generation_current') is not False
                or metadata_receipt.get('expected_sha256_by_path') != expected_hashes
                or {row['path']: row['sha256'] for row in metadata_receipt['inventory']} != expected_hashes
                or any(value is not None for value in metadata_snapshot(root).values())):
            _reject('LOCAL_RESET_METADATA_RETIREMENT_UNVERIFIED')
        if journal['stage'] == 'VALIDATED':
            journal['stage'] = 'METADATA_RETIRED'
    if journal and journal.get('stage') == 'COMPLETE':
        raw_receipt = _complete_receipt(raw_path, root)
        _verify_raw_binding(raw_receipt, journal)
        return journal
    recovered_raw = False
    if raw_path.exists():
        if not journal or journal.get('stage') != 'METADATA_RETIRED':
            _reject('LOCAL_RESET_RAW_RECEIPT_REQUIRES_RECONCILIATION')
        raw_receipt = _complete_receipt(raw_path, root)
        _verify_raw_binding(raw_receipt, journal)
        recovered_raw = True
    preflight = None if recovered_raw else execute_research_reset(
        runtime_root=root, proof=proof, quiescent=True,
        recovery_states=states, receipt_path=raw_path, validate_only=True)
    snapshot = journal['metadata_snapshot'] if journal else metadata_snapshot(root)
    if validate_only:
        return {'status': 'VALIDATED', 'binding': binding, 'preflight': preflight,
                'deletion_performed': False, 'activation_requires_trusted_auditor': True}
    journal_path.parent.mkdir(parents=True, exist_ok=True)
    journal = journal or {'schema': 'local_research_reset_v1', 'binding': binding,
                          'metadata_snapshot': snapshot, 'stage': 'VALIDATED'}
    def save():
        fd, candidate = tempfile.mkstemp(prefix='.local-reset-', dir=journal_path.parent)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as handle:
                json.dump(journal, handle, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(candidate, journal_path)
        finally:
            if os.path.exists(candidate):
                os.unlink(candidate)
    save()
    if recovered_raw:
        journal.update(stage='COMPLETE', raw_receipt=str(raw_path))
        save()
        return journal
    if journal['stage'] == 'VALIDATED':
        retire_canonical_generation(root=root, expected_snapshot=snapshot,
            retired_epoch_id=proof['retired_epoch_id'], new_epoch_id=expected_new_epoch,
            receipt_path=journal_path.parent / 'metadata-deletion.json', quiescent=True,
            recovery_states=states, lease=lease)
        journal['stage'] = 'METADATA_RETIRED'
        save()
    elif journal['stage'] != 'METADATA_RETIRED':
        _reject('LOCAL_RESET_JOURNAL_STAGE_INVALID')
    journal['raw_proof_sha256'] = preflight['proof_sha256']
    # Full inventory plan includes retained journal metadata, which this very
    # save changes. Pin the exact validated deletion target map independently.
    journal['raw_preflight_plan_sha256'] = preflight['plan_sha256']
    journal['raw_expected_sha256_by_path'] = preflight['expected_sha256_by_path']
    journal['raw_target_plan_sha256'] = hashlib.sha256(json.dumps(
        preflight['expected_sha256_by_path'], sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    save()
    result = execute_research_reset(runtime_root=root, proof=proof, quiescent=True,
        recovery_states=states, receipt_path=raw_path,
        expected_target_sha256_by_path=journal['raw_expected_sha256_by_path'])
    if result.get('status') != 'COMPLETE':
        _reject('LOCAL_RESET_RAW_DELETION_INCOMPLETE')
    _verify_raw_binding(result['deletion_receipt'], journal)
    journal.update(stage='COMPLETE', raw_receipt=str(raw_path))
    save()
    return journal
