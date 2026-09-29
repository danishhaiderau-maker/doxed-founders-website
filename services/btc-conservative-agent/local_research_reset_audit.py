"""Read-only authority adapter for an explicitly authorized local epoch reset.

OS and recovery inspection failures raise; absence is never inferred from a
failed query. This is local paper research provenance, not exchange flatness.
"""
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time

from emergency_evidence_wal import EmergencyEvidenceWal
from research.canonical_data_store import current_analyzer_dataset_identity
from research.canonical_generation_retirement import RETIRED_MARKER, metadata_snapshot
from local_research_reset import FLY_COMPLETION_MAX_BYTES
from research.mirror_generation_lease import MirrorGenerationLease, LEASE_FILE_NAME
from research_exact_deletion import _checked_path, ResearchDeletionRejected
from research_reset_auxiliary_audit import audit_auxiliary_cleanup
from research_reset_inventory import PROOF_SCHEMA
from research_reset_recovery_audit import audit_research_reset_recovery


TASKS = ('DoxxedFlyMirrorOneshot', 'DoxxedFlyMirrorSync', 'DoxxedFlySyncDurable',
         'DoxxedResearchStabilitySupervisor')


def windows_owner_snapshot(root):
    """Bounded command runtime; publish counts/IDs only, never command lines."""
    if os.name != 'nt':
        raise ResearchDeletionRejected('LOCAL_RESET_WINDOWS_OWNER_PROBE_REQUIRED')
    shell = shutil.which('pwsh') or shutil.which('powershell')
    if not shell:
        raise ResearchDeletionRejected('LOCAL_RESET_POWERSHELL_UNAVAILABLE')
    command = r'''
$ErrorActionPreference = 'Stop'
$processes = @(Get-CimInstance Win32_Process -OperationTimeoutSec 8 | Where-Object {
  $_.ProcessId -ne $PID -and $_.Name -match '^(python(?:w|3(?:\.\d+)?)?|pwsh|powershell|node)(?:\.exe)?$' -and
  ($_.CommandLine -match 'analyzer_research_engine|research_dashboard|sync-fly-bot-data|btc_conservative_agent|lifecycle_pipeline|data_sync_bundle|home-research-supervisor')
})
$unknown = @(Get-CimInstance Win32_Process -OperationTimeoutSec 8 | Where-Object {
  $_.ProcessId -ne $PID -and $_.Name -match '^(python|pythonw|pwsh|powershell|node)\.exe$' -and
  [string]::IsNullOrWhiteSpace($_.CommandLine)
})
$names = @('DoxxedFlyMirrorOneshot','DoxxedFlyMirrorSync','DoxxedFlySyncDurable','DoxxedResearchStabilitySupervisor')
$tasks = @(Get-ScheduledTask | Where-Object {$_.TaskName -in $names} | ForEach-Object {
  @{name=$_.TaskName;state=[string]$_.State}
})
$listeners = @(Get-NetTCPConnection -State Listen | Where-Object {$_.LocalPort -in @(9001,7810,7002)} | ForEach-Object {
  @{port=$_.LocalPort;pid=$_.OwningProcess}
})
@{schema='local_reset_os_owner_snapshot_v1';process_ids=@($processes | ForEach-Object {$_.ProcessId});
  unknown_process_count=$unknown.Count;tasks=$tasks;listeners=$listeners} | ConvertTo-Json -Depth 5 -Compress
'''
    encoded = base64.b64encode(command.encode('utf-16le')).decode()
    result = subprocess.run([shell, '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded],
        capture_output=True, text=True, encoding='utf-8', timeout=25)
    if result.returncode or len(result.stdout.encode('utf-8')) > 65536:
        raise ResearchDeletionRejected('LOCAL_RESET_OWNER_PROBE_FAILED')
    return json.loads(result.stdout)


def make_local_reset_auditor(*, lease, fly_receipt_bytes, fly_receipt_sha256,
                            expected_new_epoch, expected_revision,
                            journal_path=None,
                            prior_audit_bytes=None, prior_audit_sha256=None,
                            owner_snapshot_provider=windows_owner_snapshot):
    """Return the callback accepted by local_research_reset; no file writes.

    An alternate provider is trusted executable code for a host integration,
    never operator JSON claiming owners stopped. Keep tasks disabled throughout
    the externally held lease. The snapshot is not an OS process-launch fence.
    """
    if (not callable(owner_snapshot_provider) or not isinstance(fly_receipt_bytes, bytes)
            or len(fly_receipt_bytes) > FLY_COMPLETION_MAX_BYTES
            or hashlib.sha256(fly_receipt_bytes).hexdigest() != fly_receipt_sha256):
        raise ResearchDeletionRejected('LOCAL_RESET_FLY_PIN_INVALID')
    fly = json.loads(fly_receipt_bytes)
    safety = fly.get('proof', {})
    if (fly.get('schema') != 'bot_destructive_research_reset_v1' or fly.get('stage') != 'COMPLETE'
            or fly.get('new_epoch_id') != expected_new_epoch or safety.get('new_epoch_id') != expected_new_epoch
            or fly.get('boundary_evidence', {}).get('deployed_revision') != expected_revision
            or not re.fullmatch(r'[0-9a-f]{40}', expected_revision)
            or fly.get('accounting_preserved') is not True
            or safety.get('paper_only') is not True or safety.get('live_disarmed') is not True
            or any(type(safety.get(k)) is not int or safety[k] != 0
                   for k in ('pending_paper_orders', 'open_paper_positions'))):
        raise ResearchDeletionRejected('LOCAL_RESET_FLY_SAFETY_UNPROVEN')

    pinned_identity = None
    pinned_metadata = None
    prior_pin = None
    if prior_audit_bytes is not None:
        if (not isinstance(prior_audit_bytes, bytes) or len(prior_audit_bytes) > 16 * 1024**2
                or hashlib.sha256(prior_audit_bytes).hexdigest() != prior_audit_sha256):
            raise ResearchDeletionRejected('LOCAL_RESET_PRIOR_AUDIT_PIN_INVALID')
        prior = json.loads(prior_audit_bytes)
        if (prior.get('schema') != 'local_reset_audit_evidence_v1'
                or prior.get('fly_completion_sha256') != fly_receipt_sha256
                or not isinstance(prior.get('retired_identity'), dict)
                or not isinstance(prior.get('manifest_entry_hash'), str)
                or not re.fullmatch(r'[0-9a-f]{64}', prior['manifest_entry_hash'])
                or not isinstance(prior.get('metadata_snapshot'), dict)):
            raise ResearchDeletionRejected('LOCAL_RESET_PRIOR_AUDIT_INVALID')
        old = prior['retired_identity']
        pinned_identity = {**old, 'dataset_epoch': old['epoch_id'],
                           'entry_hash': prior['manifest_entry_hash']}
        pinned_metadata = prior['metadata_snapshot']
        prior_pin = prior_audit_sha256
    def audit(root):
        nonlocal pinned_identity, pinned_metadata
        started = time.time()
        root = Path(root).absolute()
        _checked_path(root / LEASE_FILE_NAME, root)
        if (not isinstance(lease, MirrorGenerationLease) or not lease.held
                or lease.path != root / LEASE_FILE_NAME):
            raise ResearchDeletionRejected('MATCHING_MIRROR_LEASE_REQUIRED')
        marker_path = _checked_path(root / RETIRED_MARKER, root)
        if pinned_identity is not None and marker_path.exists():
            if journal_path is None:
                raise ResearchDeletionRejected('LOCAL_RESET_RESUME_JOURNAL_REQUIRED')
            joint_path = _checked_path(journal_path, root)
            # The joint journal includes the complete exact target hash map.
            # Match the CLI's bounded receipt allowance; retirement markers stay tiny.
            if marker_path.stat().st_size > 65536 or joint_path.stat().st_size > 16 * 1024**2:
                raise ResearchDeletionRejected('LOCAL_RESET_RESUME_METADATA_LIMIT')
            marker = json.loads(marker_path.read_text(encoding='utf-8'))
            joint = json.loads(joint_path.read_text(encoding='utf-8'))
            from local_research_reset import _complete_receipt
            metadata_receipt = _complete_receipt(joint_path.parent / 'metadata-deletion.json', root)
            expected_hashes = {str(root / name): digest for name, digest in pinned_metadata.items() if digest is not None}
            binding = joint.get('binding', {})
            if (marker.get('schema') != 'canonical_generation_retirement_v1'
                    or marker.get('retired_epoch_id') != pinned_identity['dataset_epoch']
                    or marker.get('new_epoch_id') != expected_new_epoch
                    or marker.get('metadata_sha256') != pinned_metadata
                    or marker.get('generation_current') is not False
                    or joint.get('schema') != 'local_research_reset_v1'
                    or binding.get('retired_manifest_entry_hash') != pinned_identity['entry_hash']
                    or binding.get('fly_receipt_sha256') != fly_receipt_sha256
                    or binding.get('root') != str(root)
                    or binding.get('new_epoch_id') != expected_new_epoch
                    or binding.get('source_revision') != expected_revision
                    or (prior_pin is not None and binding.get('initial_audit_sha256') != prior_pin)
                    or binding.get('retired_identity') != {key: pinned_identity['dataset_epoch'] if key == 'epoch_id' else pinned_identity[key]
                        for key in ('epoch_id', 'source_revision', 'deployed_revision', 'tile_config_signature')}
                    or metadata_receipt.get('expected_sha256_by_path') != expected_hashes
                    or {r['path']: r['sha256'] for r in metadata_receipt['inventory']} != expected_hashes
                    or joint.get('metadata_snapshot') != pinned_metadata
                    or any(v is not None for v in metadata_snapshot(root).values())):
                raise ResearchDeletionRejected('LOCAL_RESET_RESUME_IDENTITY_UNVERIFIED')
            identity = dict(pinned_identity)
        else:
            identity = current_analyzer_dataset_identity(root)
            identity = {key: identity[key] for key in ('dataset_epoch', 'source_revision',
                        'deployed_revision', 'tile_config_signature', 'entry_hash')}
            if pinned_identity is not None and any(identity.get(key) != pinned_identity.get(key) for key in identity):
                raise ResearchDeletionRejected('LOCAL_RESET_MANIFEST_CHANGED')
            pinned_identity = dict(identity)
            pinned_metadata = metadata_snapshot(root)
        retired = {key: identity['dataset_epoch'] if key == 'epoch_id' else identity[key]
                   for key in ('epoch_id', 'source_revision', 'deployed_revision', 'tile_config_signature')}
        owners = owner_snapshot_provider(root)
        if (not isinstance(owners, dict) or owners.get('schema') != 'local_reset_os_owner_snapshot_v1'
                or owners.get('process_ids') != [] or owners.get('listeners') != []
                or type(owners.get('unknown_process_count')) is not int or owners['unknown_process_count'] != 0
                or not isinstance(owners.get('tasks'), list)
                or len(owners['tasks']) != len(TASKS)
                or {r.get('name'): r.get('state') for r in owners['tasks']} != {name: 'Disabled' for name in TASKS}):
            raise ResearchDeletionRejected('LOCAL_RESET_OWNERS_NOT_QUIESCENT')
        recovery = audit_research_reset_recovery(root, expected_identity=retired)
        auxiliary = audit_auxiliary_cleanup(root)
        if (recovery.get('complete') is not True or recovery.get('safe_for_reset_recovery_scope') is not True
                or type(recovery.get('pending_or_unknown_count')) is not int or recovery['pending_or_unknown_count'] != 0
                or auxiliary.get('complete') is not True or auxiliary.get('safe') is not True
                or type(auxiliary.get('pending_or_unknown_count')) is not int or auxiliary['pending_or_unknown_count'] != 0):
            raise ResearchDeletionRejected('LOCAL_RESET_RECOVERY_NOT_CLEAR')
        wal_path = root / 'v3' / 'emergency_evidence_wal_v2'
        if os.path.lexists(wal_path):
            _checked_path(wal_path, root)
            wal = EmergencyEvidenceWal.inspect_existing(wal_path, identity=retired)
            if wal.get('records') != [] or wal.get('alarms', []) != []:
                raise ResearchDeletionRejected('LOCAL_RESET_WAL_NOT_EMPTY')
            wal_state = 'EMPTY'
        else:
            # Validate parent containment too; lexists distinguishes a dangling link.
            _checked_path(wal_path, root)
            wal, wal_state = {'status': 'NOT_PRESENT'}, 'NOT_PRESENT'
        if time.time() - started > 10:
            raise ResearchDeletionRejected('LOCAL_RESET_AUDIT_WINDOW_EXCEEDED')
        evidence = {'schema': 'local_reset_audit_evidence_v1', 'retired_identity': retired,
                    'metadata_snapshot': pinned_metadata,
                    'manifest_entry_hash': identity['entry_hash'], 'owners': owners,
                    'recovery': recovery, 'auxiliary': auxiliary, 'wal': wal,
                    'fly_completion_sha256': fly_receipt_sha256,
                    'exposure_basis': 'PINNED_FLY_COMPLETED_PAPER_RESET_NOT_LOCAL_HISTORY_OR_EXCHANGE'}
        raw = json.dumps(evidence, sort_keys=True, separators=(',', ':')).encode()
        proof = dict(schema=PROOF_SCHEMA, runtime_root=str(root), retired_epoch_id=retired['epoch_id'],
            new_epoch_id=expected_new_epoch, source_revision=retired['source_revision'],
            recovery_receipt_sha256=hashlib.sha256(raw).hexdigest(), writers_quiesced=True,
            paper_only=safety['paper_only'], live_disarmed=safety['live_disarmed'], epoch_retired=True,
            pending_paper_orders=safety['pending_paper_orders'], open_paper_positions=safety['open_paper_positions'],
            pending_wal_records=0, pending_recovery_records=recovery['pending_or_unknown_count'])
        return dict(checked_at=started, proof=proof, recovery_receipt_bytes=raw,
                    retired_identity=retired, retired_manifest_entry_hash=identity['entry_hash'],
                    recovery_states={'local_owners': 'RECONCILED', 'append_rotation': 'RECONCILED',
                                     'auxiliary': 'RECONCILED', 'emergency_wal': wal_state})
    return audit
