"""Explicit one-attempt checkpoint recovery; never resets a circuit in a loop."""
import hashlib
import json
import os
from pathlib import Path
import re

from data_sync_bundle_runtime import _identity, _reject_links, run_managed_generation, run_slice


def claim_checkpoint_retry(metadata, source_root, output_root, expected_receipt_sha256):
    """Caller must hold the existing coordinator singleton across claim/start.

    Preserve the original diagnostic bytes once, before any new attempt can
    replace the mutable status. A consumed claim is never automatically retried.
    """
    from data_sync_bundle_worker import _validate_output_root, _generation
    if not isinstance(expected_receipt_sha256, str) or not re.fullmatch(r'[0-9a-f]{64}', expected_receipt_sha256):
        raise ValueError('BUNDLE_RETRY_PROOF_INVALID')
    generation = _generation(metadata)
    output = _validate_output_root(Path(source_root).resolve(strict=True), output_root)
    directory = output / ('g-' + generation['inventory_generation_id'][:16])
    def read(name, limit):
        path = directory / name
        _reject_links(path)
        with path.open('rb') as stream:
            raw = stream.read(limit + 1)
        if len(raw) > limit:
            raise ValueError('BUNDLE_RETRY_PROOF_INVALID')
        return raw, json.loads(raw)
    raw, receipt = read('bundle-coordinator-status.json', 65536)
    if (hashlib.sha256(raw).hexdigest() != expected_receipt_sha256
            or receipt.get('schema') != 'fly_transport_bundle_coordinator_status_v1'
            or receipt.get('identity') != _identity(metadata)
            or receipt.get('terminal') is not True or receipt.get('status') != 'FAILED'
            or receipt.get('error') != 'BUNDLE_CIRCUIT_OPEN'
            or receipt.get('last_error') != 'BUNDLE_SLICE_TIMEOUT'
            or receipt.get('timeout_phase') != 'CHECKPOINT'):
        raise ValueError('BUNDLE_RETRY_PROOF_INVALID')
    _, state = read('bundle-worker-state.json', 2 * 1024 * 1024)
    if state.get('generation') != generation or not isinstance(state.get('cursor'), dict):
        raise ValueError('BUNDLE_RETRY_CHECKPOINT_IDENTITY_INVALID')
    # The current durable cursor may be newer than the timed-out receipt.
    # The real worker validates and resumes it; do not roll it back here.
    claimed = directory / ('checkpoint-retry-' + expected_receipt_sha256 + '.json')
    _reject_links(claimed)
    try:
        with claimed.open('xb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError:
        raise ValueError('BUNDLE_RETRY_ALREADY_CLAIMED') from None
    return claimed


def run_checkpoint_recovery(metadata, source_root, output_root, **kwargs):
    def recovery_slice(*args, **slice_kwargs):
        return run_slice(*args, timeout=30, **slice_kwargs)
    # Exactly one managed attempt. Existing per-attempt circuits, storage,
    # pressure, retention and time/member budgets remain unchanged.
    return run_managed_generation(metadata, source_root, output_root,
                                  slice_runner=recovery_slice, **kwargs)


def register_checkpoint_retry_route(app, *, authenticated, start):
    from flask import jsonify, request
    def retry():
        if not authenticated():
            return jsonify(ok=False, error='UNAUTHORIZED'), 401
        if request.content_length is None or request.content_length > 2048:
            return jsonify(ok=False, error='BUNDLE_RETRY_REQUEST_INVALID'), 400
        body = request.get_json(silent=True)
        if (not isinstance(body, dict) or set(body) != {'generation_id', 'receipt_sha256'}
                or any(not isinstance(body[k], str) or not re.fullmatch(r'[0-9a-f]{64}', body[k]) for k in body)):
            return jsonify(ok=False, error='BUNDLE_RETRY_REQUEST_INVALID'), 400
        try:
            accepted = start(body['generation_id'], checkpoint_retry_sha256=body['receipt_sha256'])
        except (ValueError, OSError):
            return jsonify(ok=False, error='BUNDLE_RETRY_PROOF_REJECTED'), 409
        return jsonify(ok=bool(accepted), status='ACCEPTED' if accepted else 'NOT_ADMITTED'), 202 if accepted else 409
    app.add_url_rule('/api/data-sync/bundles/retry-checkpoint',
                     'data_sync_bundle_retry_checkpoint', retry, methods=['POST'])
