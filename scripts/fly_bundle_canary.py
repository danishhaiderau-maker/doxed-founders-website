"""One bounded derivative slice on the deployed image; never a coordinator.

Operational admission uses recent authenticated telemetry, not an atomic lease on
the bot's in-process coordinator. The existing worker OS lease serializes actual
package work. No ACK, cleanup, restart, trade mutation or metadata relabel occurs.
The deployed pressure callback is storage/overlap admission, not a CPU, memory or
I/O performance proof. Inspection counters below are a single cumulative sample;
they do not prove a bottleneck or a rate without a separately timed second sample.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import sys
import time
import urllib.request

VOLUME = Path('/app/data')
SOURCE = VOLUME / 'runtime'
OUTPUT = VOLUME / '.data-sync-snapshots/transport-bundles'
SNAPSHOT = VOLUME / 'sync_inventory_current.json'
MAX_JSON = 2 * 1024 * 1024
MAX_INDEX = 16 * 1024 * 1024


class Blocked(ValueError):
    pass


def require(ok, code):
    if not ok:
        raise Blocked(code)


def number(value):
    return type(value) in (int, float) and math.isfinite(value)


def bounded(path, maximum):
    path = Path(path)
    for parent in (*reversed(path.parents), path):
        info = parent.lstat()
        require(not parent.is_symlink() and not getattr(info, 'st_file_attributes', 0) & 0x400,
                'PATH_LINK_REJECTED')
    before = path.stat()
    require(before.st_size <= maximum and path.is_file(), 'OBJECT_SIZE_LIMIT')
    with path.open('rb') as stream:
        raw = stream.read(maximum + 1)
    after = path.stat()
    signature = lambda stat: (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)
    require(len(raw) == before.st_size and signature(before) == signature(after), 'OBJECT_CHANGED')
    return raw


def validate_status(status, revision):
    require(status.get('source_git_rev') == revision[:12], 'STATUS_REVISION_MISMATCH')
    require(status.get('force_paper_mode') is True and status.get('live_armed') is False
            and status.get('bitfinex_live_enabled') is False, 'PAPER_SAFETY_UNPROVEN')
    require(status.get('process_alive') is True, 'PROCESS_NOT_ALIVE')
    require(status.get('dashboard_owner') is True and type(status.get('dashboard_pid')) is int
            and status['dashboard_pid'] > 0, 'DASHBOARD_OWNER_UNPROVEN')
    pipeline = status.get('lifecycle_pipeline') or {}
    require(all(pipeline.get(k) is True for k in ('owner', 'running', 'source_revision_match')),
            'PIPELINE_OWNER_UNPROVEN')
    require(pipeline.get('active') is False and pipeline.get('last_outcome') == 'SUCCESS',
            'PIPELINE_OVERLAP_OR_NOT_SUCCESSFUL')
    age, next_run = pipeline.get('last_success_age_sec'), pipeline.get('next_run_in_sec')
    require(number(age) and 0 <= age <= 15 and number(next_run) and next_run > 15,
            'FRESH_OVERLAP_WINDOW_UNAVAILABLE')
    require(pipeline.get('pressure') is False and pipeline.get('emergency') is False
            and 'overlap_code' in pipeline and pipeline['overlap_code'] is None,
            'PRESSURE_OR_OVERLAP_UNPROVEN')
    boot = pipeline.get('receipt_bootstrap') or {}
    require(boot.get('complete') is True and boot.get('blocked') is False,
            'BOOTSTRAP_INCOMPLETE')


def validate_manifest(manifest, revision, expected=None):
    require(manifest.get('schema') == 'fly_runtime_incremental_sync_v1', 'MANIFEST_SCHEMA')
    require(manifest.get('inventory_status') == 'CURRENT'
            and manifest.get('inventory_authoritative') is True
            and manifest.get('inventory_ack_eligible') is True, 'INVENTORY_NOT_CURRENT')
    require(manifest.get('inventory_build_status') == 'IDLE', 'INVENTORY_OVERLAP')
    boot = manifest.get('receipt_bootstrap') or {}
    require(boot.get('complete') is True and boot.get('blocked') is False, 'BOOTSTRAP_INCOMPLETE')
    generation = manifest.get('inventory_generation_id')
    require(isinstance(generation, str) and re.fullmatch('[0-9a-f]{64}', generation)
            and manifest.get('inventory_sha256') == generation, 'GENERATION_IDENTITY')
    require(manifest.get('source_git_rev') == revision[:12], 'MANIFEST_REVISION_MISMATCH')
    for key in ('collection_epoch_id', 'tile_registry_signature'):
        require(isinstance(manifest.get(key), str) and bool(manifest[key]), 'MANIFEST_IDENTITY_MISSING')
    if expected:
        for key in ('inventory_generation_id', 'inventory_sha256', 'source_git_rev',
                    'collection_epoch_id', 'tile_registry_signature', 'file_count',
                    'total_bytes', 'manifest_page_count'):
            require(manifest.get(key) == expected.get(key), 'MANIFEST_IDENTITY_CHANGED')
    return generation


def bind_snapshot(raw, manifest, volume=VOLUME):
    snapshot = json.loads(raw)
    require(snapshot.get('schema') == 'fly_runtime_inventory_snapshot_v2', 'SNAPSHOT_SCHEMA')
    require(snapshot.get('source_git_rev') == manifest['source_git_rev'], 'SNAPSHOT_REVISION')
    generation = snapshot.get('generation') or {}
    digest = manifest['inventory_generation_id']
    require(generation.get('storage') == 'disk_pages_v2'
            and generation.get('generation_id') == digest, 'SNAPSHOT_GENERATION')
    expected_dir = volume / '.data-sync-snapshots/inventory-generations' / digest
    require(generation.get('generation_dir') == str(expected_dir)
            and generation.get('page_index_path') == str(expected_dir / 'page-index.jsonl'),
            'SNAPSHOT_PATH')
    for field, public in (('file_count', 'file_count'), ('total_bytes', 'total_bytes'),
                          ('page_count', 'manifest_page_count')):
        require(type(generation.get(field)) is int and generation[field] >= 0
                and generation[field] == manifest.get(public), 'SNAPSHOT_COUNTS')
    identity = generation.get('bundle_identity') or {}
    require(set(identity) == {'source_git_rev', 'collection_epoch_id', 'tile_registry_signature'}
            and all(identity[key] == manifest.get(key) for key in identity), 'SNAPSHOT_IDENTITY')
    index = bounded(Path(generation['page_index_path']), MAX_INDEX)
    require(hashlib.sha256(index).hexdigest() == generation.get('page_index_sha256'), 'INDEX_SHA_MISMATCH')
    identity_hash = hashlib.sha256(b'fly_runtime_inventory_generation_v2\n')
    total_files = total_bytes = 0
    lines = index.splitlines()
    require(len(lines) == generation['page_count'] and 0 < len(lines) <= 100000,
            'INDEX_DESCRIPTOR_COUNT')
    for position, line in enumerate(lines):
        require(len(line) <= 16384, 'INDEX_DESCRIPTOR_LIMIT')
        row = json.loads(line)
        require(row.get('page_index') == position and type(row.get('file_count')) is int
                and row['file_count'] >= 0 and type(row.get('total_bytes')) is int
                and row['total_bytes'] >= 0 and isinstance(row.get('page_sha256'), str)
                and re.fullmatch('[0-9a-f]{64}', row['page_sha256']), 'INDEX_DESCRIPTOR_INVALID')
        require(row.get('file_name') == f"p{position:08d}-{row['page_sha256'][:24]}.json",
                'INDEX_DESCRIPTOR_PATH')
        total_files += row['file_count']
        total_bytes += row['total_bytes']
        identity_hash.update(f"{position}:{row['file_count']}:{row['total_bytes']}:{row['page_sha256']}\n".encode('ascii'))
    require(identity_hash.hexdigest() == digest and total_files == generation['file_count']
            and total_bytes == generation['total_bytes'], 'INDEX_MANIFEST_BINDING')
    # Authority comes only from the authenticated current pinned manifest above.
    metadata = {**generation, **identity, 'inventory_generation_id': digest,
                'inventory_sha256': digest, 'ack_eligible': manifest['inventory_ack_eligible']}
    return metadata, hashlib.sha256(index).hexdigest()


def _run_canary(revision, generation_id, request_json, slice_runner, local_admission,
                *, volume=VOLUME, monotonic=time.monotonic, attempt):
    require(isinstance(revision, str) and re.fullmatch('[0-9a-f]{12}', revision), 'EXACT_REVISION_REQUIRED')
    require(isinstance(generation_id, str) and re.fullmatch('[0-9a-f]{64}', generation_id), 'GENERATION_REQUIRED')
    started = monotonic()
    validate_status(request_json('/api/status'), revision)
    # disk_pages_v2 page size is immutable; never request a smaller page.
    pinned_url = '/api/data-sync/manifest?paged=1&generation_id=' + generation_id
    manifest = request_json(pinned_url)
    digest = validate_manifest(manifest, revision)
    require(digest == generation_id, 'REQUESTED_GENERATION_MISMATCH')
    validate_manifest(request_json(pinned_url), revision, manifest)
    snapshot_path = volume / 'sync_inventory_current.json'
    raw = bounded(snapshot_path, MAX_JSON)
    metadata, index_sha = bind_snapshot(raw, manifest, volume)
    local_admission()
    validate_status(request_json('/api/status'), revision)
    require(monotonic() - started <= 20, 'ADMISSION_DEADLINE')
    attempt['slice_attempted'] = True
    receipt = slice_runner(metadata, volume / 'runtime',
                           volume / '.data-sync-snapshots/transport-bundles', timeout=12)
    if receipt.get('status') not in ('BUILDING', 'COMPLETE'):
        error = receipt.get('error')
        raise Blocked(error if isinstance(error, str) and re.fullmatch('[A-Z][A-Z0-9_]{1,95}', error)
                      else 'SLICE_FAILED')
    validate_manifest(request_json(pinned_url), revision, manifest)
    require(bounded(snapshot_path, MAX_JSON) == raw, 'SNAPSHOT_CHANGED_AFTER_SLICE')
    _, after_index = bind_snapshot(raw, manifest, volume)
    require(index_sha == after_index, 'INDEX_CHANGED_AFTER_SLICE')
    validate_status(request_json('/api/status'), revision)
    return {'schema': 'fly_bundle_canary_receipt_v1', 'status': 'SLICE_VERIFIED',
            'deployed_revision': revision, 'inventory_generation_id': digest,
            'snapshot_sha256': hashlib.sha256(raw).hexdigest(), 'page_index_sha256': index_sha,
            'slice': receipt, 'ack_performed': False, 'cleanup_performed': False,
            'slice_attempted': True, 'derivative_writes_possible': True,
            'managed_coordinator_started': False, 'admission_basis': 'RECENT_AUTHENTICATED_TELEMETRY'}


def run_canary(*args, **kwargs):
    attempt = {'slice_attempted': False}
    try:
        return _run_canary(*args, **kwargs, attempt=attempt)
    except Exception as exc:
        exc.slice_attempted = attempt['slice_attempted']
        raise


def blocked_receipt(exc):
    attempted = getattr(exc, 'slice_attempted', False) is True
    return {'schema': 'fly_bundle_canary_receipt_v1', 'status': 'BLOCKED',
            'error': str(exc) if isinstance(exc, Blocked) else type(exc).__name__,
            'slice_attempted': attempted, 'derivative_writes_possible': attempted,
            'ack_performed': False, 'cleanup_performed': False}


def virtual_read(path, maximum=8192):
    """Fixed proc/cgroup files report zero stat size; bound the actual read."""
    try:
        require(not path.is_symlink(), 'VIRTUAL_PATH_LINK')
        with path.open('rb') as stream:
            raw = stream.read(maximum + 1)
        require(len(raw) <= maximum, 'VIRTUAL_READ_LIMIT')
        return raw.decode('ascii', errors='strict'), None
    except (OSError, UnicodeError, Blocked) as exc:
        return None, str(exc) if isinstance(exc, Blocked) else type(exc).__name__


def integer_fields(text, allowed=None):
    result = {}
    for line in text.splitlines():
        fields = line.replace(':', ' ').split()
        if len(fields) in (2, 3) and (allowed is None or fields[0] in allowed):
            if fields[1].isdigit() and len(fields[1]) <= 24:
                result[fields[0]] = int(fields[1])
    return result


def pressure_fields(text):
    result = {}
    for line in text.splitlines():
        fields = line.split()
        if not fields or fields[0] not in ('some', 'full'):
            continue
        row = {}
        for item in fields[1:]:
            key, sep, value = item.partition('=')
            if sep and key in ('avg10', 'avg60', 'avg300', 'total'):
                try:
                    number_ = int(value) if key == 'total' else float(value)
                except ValueError:
                    continue
                if number(number_) and number_ >= 0:
                    row[key] = number_
        result[fields[0]] = row
    return result


def resource_snapshot(status, *, proc_root=Path('/proc'), cgroup_root=Path('/sys/fs/cgroup')):
    """No directory enumeration, raw argv, environment, logs or sleeps."""
    result = {'schema': 'bounded_runtime_resource_snapshot_v1',
              'basis': 'SINGLE_CUMULATIVE_SAMPLE_NOT_RATE_OR_CAUSE', 'cgroup': {},
              'inventory_workers': [], 'unavailable': {},
              'child_scope': 'VERIFIED_DASHBOARD_MAIN_THREAD_DIRECT_CHILDREN_ONLY'}
    for name in ('io.pressure', 'cpu.pressure', 'memory.pressure', 'io.stat',
                 'cpu.stat', 'memory.current', 'memory.max'):
        text, error = virtual_read(cgroup_root / name)
        if error:
            result['unavailable']['cgroup/' + name] = error
        elif name.endswith('.pressure'):
            result['cgroup'][name] = pressure_fields(text)
        elif name == 'cpu.stat':
            result['cgroup'][name] = integer_fields(text, ('usage_usec', 'user_usec', 'system_usec',
                'nr_periods', 'nr_throttled', 'throttled_usec', 'nr_bursts', 'burst_usec'))
        elif name.startswith('memory.'):
            value = text.strip()
            if value == 'max' or value.isdigit() and len(value) <= 24:
                result['cgroup'][name] = 'max' if value == 'max' else int(value)
            else:
                result['unavailable']['cgroup/' + name] = 'INVALID_NUMERIC_VALUE'
        else:
            devices = []
            for line in text.splitlines()[:64]:
                fields = line.split()
                if not fields or not re.fullmatch('[0-9]{1,10}:[0-9]{1,10}', fields[0]):
                    continue
                row = {'device': fields[0]}
                for item in fields[1:]:
                    key, sep, value = item.partition('=')
                    if key in ('rbytes', 'wbytes', 'rios', 'wios', 'dbytes', 'dios') and value.isdigit() and len(value) <= 24:
                        row[key] = int(value)
                devices.append(row)
            result['cgroup'][name] = devices
            if len(text.splitlines()) > 64:
                result['unavailable']['cgroup/io.stat'] = 'DEVICE_LIMIT_PARTIAL'
    pid = status.get('dashboard_pid')
    if status.get('dashboard_owner') is not True or status.get('process_alive') is not True \
            or type(pid) is not int or not 1 <= pid <= 2**31 - 1:
        result['unavailable']['dashboard_children'] = 'DASHBOARD_PID_UNVERIFIED'
        return result
    parent = proc_root / str(pid)
    parent_stat, error = virtual_read(parent / 'stat', 4096)
    children, child_error = virtual_read(parent / 'task' / str(pid) / 'children', 2048)
    if error or child_error:
        result['unavailable']['dashboard_children'] = error or child_error
        return result
    def start_identity(text):
        try:
            return int(text.split(' ', 1)[0]), int(text.rsplit(') ', 1)[1].split()[19])
        except (ValueError, IndexError):
            return None
    parent_identity = start_identity(parent_stat)
    if parent_identity is None or parent_identity[0] != pid:
        result['unavailable']['dashboard_children'] = 'PROC_IDENTITY_INVALID'
        return result
    ids = children.split()
    if len(ids) > 64 or any(not re.fullmatch('[0-9]{1,10}', item) or not 1 <= int(item) <= 2**31 - 1 for item in ids):
        result['unavailable']['dashboard_children'] = 'CHILD_PID_LIMIT_OR_INVALID'
        return result
    for child in dict.fromkeys(ids):
        root = proc_root / child
        comm, comm_error = virtual_read(root / 'comm', 128)
        argv, argv_error = virtual_read(root / 'cmdline', 8192)
        if comm_error or argv_error:
            result['unavailable']['proc/' + child] = comm_error or argv_error
            continue
        args = argv.split('\x00')
        script_index = 2 if len(args) > 1 and args[1] in ('-u', '-B') else 1
        if not comm.strip().startswith('python') or len(args) <= script_index \
                or args[script_index] != '/app/data_sync_inventory_worker.py':
            continue  # Never publish comm, argv, unknown scripts or arguments.
        row = {'pid': int(child), 'kind': 'DATA_SYNC_INVENTORY_WORKER'}
        child_stat, stat_error = virtual_read(root / 'stat', 4096)
        child_identity = start_identity(child_stat) if child_stat is not None else None
        if stat_error or child_identity is None or child_identity[0] != int(child):
            result['unavailable']['proc/' + child] = stat_error or 'PROC_IDENTITY_INVALID'
            continue
        for name in ('io', 'schedstat', 'status'):
            text, error = virtual_read(root / name, 4096)
            if error:
                result['unavailable']['proc/' + child + '/' + name] = error
            elif name == 'schedstat':
                values = text.split()
                if len(values) == 3 and all(value.isdigit() and len(value) <= 24 for value in values):
                    row[name] = dict(zip(('run_ns', 'runqueue_wait_ns', 'timeslices'), map(int, values)))
                else:
                    result['unavailable']['proc/' + child + '/' + name] = 'INVALID_SCHEDSTAT'
            else:
                allowed = ('rchar', 'wchar', 'syscr', 'syscw', 'read_bytes', 'write_bytes', 'cancelled_write_bytes') if name == 'io' else (
                    'Pid', 'PPid', 'VmRSS', 'VmHWM', 'VmSize', 'Threads', 'voluntary_ctxt_switches', 'nonvoluntary_ctxt_switches')
                row[name] = integer_fields(text, allowed)
        after, _ = virtual_read(root / 'stat', 4096)
        if row.get('status', {}).get('PPid') != pid or after is None or start_identity(after) != child_identity:
            result['unavailable']['proc/' + child] = 'CHILD_OWNERSHIP_CHANGED_OR_UNVERIFIED'
            continue
        row['start_ticks'] = child_identity[1]
        row['status_memory_unit'] = 'KiB'
        result['inventory_workers'].append(row)
    after_parent, _ = virtual_read(parent / 'stat', 4096)
    if after_parent is None or start_identity(after_parent) != parent_identity:
        result['inventory_workers'] = []
        result['unavailable']['dashboard_children'] = 'DASHBOARD_PID_IDENTITY_CHANGED'
    else:
        result['dashboard_pid'] = pid
        result['dashboard_start_ticks'] = parent_identity[1]
    return result


def inspect_only(revision, generation_id, request_json, *, volume=VOLUME, inventory_fingerprint=None,
                 resource_probe=resource_snapshot):
    status = request_json('/api/status')
    require(status.get('source_git_rev') == revision, 'STATUS_REVISION_MISMATCH')
    # Identity-only is the explicit no-refresh/physical-inventory path.
    manifest = request_json('/api/data-sync/manifest?identity_only=1')
    pipeline = status.get('lifecycle_pipeline') or {}
    fields = ('source_git_rev', 'inventory_status', 'inventory_sha256',
              'inventory_generation_id', 'inventory_build_status', 'inventory_error')
    counters = ('complete', 'phase', 'files_seen', 'dirs_seen', 'rows_written',
                'invocations', 'pages_written', 'pages_total', 'total_elapsed_seconds',
                'invocation_elapsed_seconds', 'spool_bytes_used', 'pending_directories',
                'cpu_seconds', 'invocation_files_seen', 'invocation_dirs_seen',
                'file_budget', 'elapsed_budget_seconds', 'current_directory_files_remaining',
                'peak_rss_bytes')
    progress = []
    work = volume / '.data-sync-snapshots'
    if inventory_fingerprint is not None:
        require(re.fullmatch('[0-9a-f]{64}', inventory_fingerprint), 'INVALID_INVENTORY_FINGERPRINT')
        name = f'inventory-worker-v2-{inventory_fingerprint[:32]}.progress.json'
        path = work / name
        if path.exists():
            raw = bounded(path, 65536)
            value = json.loads(raw)
            require(value.get('request_fingerprint') == inventory_fingerprint, 'PROGRESS_FINGERPRINT_MISMATCH')
            progress.append({'name': name, 'sha256': hashlib.sha256(raw).hexdigest(),
                             **{key: value.get(key) for key in counters}})
    return {'schema': 'fly_bundle_canary_inspection_v1', 'status': 'INSPECTED',
            'inspection_authority': 'IDENTITY_AND_PROGRESS_ONLY_NOT_CURRENT_INVENTORY_OR_CANARY_PROOF',
            'requested_generation_id': generation_id, 'slice_invoked': False,
            'manifest_identity_only': {key: manifest.get(key) for key in fields},
            'runtime': {key: status.get(key) for key in ('source_git_rev', 'process_alive',
                        'dashboard_owner', 'dashboard_pid', 'bot_instance_id',
                        'force_paper_mode', 'live_armed', 'bitfinex_live_enabled')},
            'pipeline': {key: pipeline.get(key) for key in ('owner', 'running', 'active',
                         'source_revision_match', 'last_outcome', 'pressure', 'emergency',
                         'overlap_code', 'last_success_age_sec', 'receipt_bootstrap')},
            'progress_receipts': progress,
            'progress_status': 'READ' if progress else 'EXACT_RECEIPT_UNAVAILABLE',
            'resources': resource_probe(status),
            'os_coordinator_ownership': 'UNAVAILABLE_IN_PROCESS_LOCK_NOT_EXPORTED'}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--expected-revision', required=True)
    parser.add_argument('--generation-id', required=True)
    parser.add_argument('--inspect-only', action='store_true')
    parser.add_argument('--inventory-fingerprint')
    args = parser.parse_args()
    require(re.fullmatch('[0-9a-f]{12}', args.expected_revision), 'EXACT_REVISION_REQUIRED')
    require(re.fullmatch('[0-9a-f]{64}', args.generation_id), 'GENERATION_REQUIRED')
    deployed = os.getenv('SOURCE_GIT_REV') or ''
    require(re.fullmatch('[0-9a-f]{40}', deployed) and deployed[:12] == args.expected_revision,
            'RUNTIME_ENV_REVISION_MISMATCH')
    token = os.getenv('BOT_ADMIN_TOKEN') or ''
    require(bool(token), 'ADMIN_AUTH_UNAVAILABLE')
    sys.path.insert(0, '/app')
    from data_sync_bundle_runtime import run_slice
    from collector_v22_schema import STORAGE_PRESSURE_THRESHOLD
    from research.mirror_generation_lease import mirror_generation_lease_held

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            raise Blocked('HTTP_REDIRECT_REJECTED')
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def request_json(path):
        request = urllib.request.Request('http://127.0.0.1:7002' + path,
            headers={'X-Bot-Admin-Token': token, 'Cache-Control': 'no-cache'})
        with opener.open(request, timeout=5) as response:
            raw = response.read(MAX_JSON + 1)
        require(len(raw) <= MAX_JSON, 'HTTP_BODY_LIMIT')
        return json.loads(raw)

    def local_admission():
        usage = shutil.disk_usage(VOLUME)
        require(usage.free >= 512 * 1024 * 1024 and usage.used / usage.total < STORAGE_PRESSURE_THRESHOLD,
                'LOCAL_STORAGE_PRESSURE')
        require(not mirror_generation_lease_held(VOLUME), 'ANALYZER_GENERATION_LEASE')

    if args.inspect_only:
        result = inspect_only(args.expected_revision, args.generation_id, request_json,
                              inventory_fingerprint=args.inventory_fingerprint)
    else:
        result = run_canary(args.expected_revision, args.generation_id, request_json, run_slice, local_admission)
    result['runtime_env_revision'] = deployed
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(json.dumps(blocked_receipt(exc)))
        raise SystemExit(1)
