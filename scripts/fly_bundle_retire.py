"""Inspect-default, exact stale-source transport derivative retirement.

Caller dispatches reviewed source without changing/restarting the collector.
No old generation is retired solely because it is old or returns generic 404.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import urllib.error
import urllib.request

VOLUME = Path('/app/data')


def require(condition, code):
    if not condition:
        raise ValueError(code)


def run(expected_revision, old_generation, state_sha, current_generation, request,
        *, inspect_only=True, volume=VOLUME):
    from data_sync_bundle_retirement import retire_derivative_generation, _stable_read, _seal
    from data_sync_bundle_storage import MAX_STATE_BYTES, _generation_usage, _stat
    require(re.fullmatch('[0-9a-f]{40}', expected_revision or ''), 'FULL_INCUMBENT_SHA_REQUIRED')
    require(all(re.fullmatch('[0-9a-f]{64}', v or '') for v in (old_generation, state_sha, current_generation)), 'EXACT_GENERATION_AND_STATE_SHA_REQUIRED')
    require(old_generation != current_generation, 'CURRENT_GENERATION_PROTECTED')
    require(os.getenv('SOURCE_GIT_REV') == expected_revision, 'ENV_REVISION_MISMATCH')
    output = volume / '.data-sync-snapshots/transport-bundles'
    state_path = output / ('g-' + old_generation[:16]) / 'bundle-worker-state.json'
    receipt_path = volume / ('transport-retirement-' + old_generation + '-' + state_sha[:16] + '.json')
    for parent in (*reversed(output.parents), output):
        _stat(parent, directory=True)
    if state_path.parent.exists() or state_path.parent.is_symlink():
        _stat(state_path.parent, directory=True)
    if state_path.exists():
        raw = _stable_read(state_path, MAX_STATE_BYTES)
        require(hashlib.sha256(raw).hexdigest() == state_sha, 'STATE_SHA_MISMATCH')
        identity = json.loads(raw).get('generation') or {}
        _generation_usage(state_path.parent, current_generation)
    else:
        saved = json.loads(_stable_read(receipt_path, 4 * 1024 * 1024))
        require(saved.get('receipt_sha256') == _seal(saved)['receipt_sha256']
            and saved.get('generation_id') == old_generation and saved.get('state_sha256') == state_sha,
            'RESUME_RECEIPT_MISMATCH')
        identity = saved.get('generation_identity') or {}
    old_revision = identity.get('source_git_rev')
    require(identity.get('inventory_generation_id') == old_generation
        and identity.get('inventory_sha256') == old_generation
        and isinstance(old_revision, str) and re.fullmatch('[0-9a-f]{12}', old_revision)
        and old_revision != expected_revision[:12], 'STALE_SOURCE_UNPROVEN')
    pinned = '/api/data-sync/manifest?paged=1&generation_id=' + current_generation
    baseline = None
    def protection():
        nonlocal baseline
        require(os.getenv('SOURCE_GIT_REV') == expected_revision, 'ENV_REVISION_CHANGED')
        code, status = request('/api/status')
        require(code == 200 and status.get('source_git_rev') == expected_revision[:12]
            and status.get('force_paper_mode') is True and status.get('live_armed') is False
            and status.get('bitfinex_live_enabled') is False and status.get('process_alive') is True
            and status.get('dashboard_owner') is True, 'INCUMBENT_PAPER_OWNER_UNPROVEN')
        code, manifest = request(pinned)
        require(code == 200 and manifest.get('inventory_status') == 'CURRENT'
            and manifest.get('inventory_authoritative') is True and manifest.get('inventory_ack_eligible') is True
            and manifest.get('inventory_generation_id') == current_generation
            and manifest.get('inventory_sha256') == current_generation
            and manifest.get('source_git_rev') == expected_revision[:12], 'CURRENT_MANIFEST_UNPROVEN')
        bound = {k: manifest.get(k) for k in ('source_git_rev', 'collection_epoch_id', 'tile_registry_signature', 'inventory_generation_id')}
        require(all(isinstance(v, str) and v for v in bound.values()), 'CURRENT_IDENTITY_MISSING')
        require(baseline is None or bound == baseline, 'CURRENT_IDENTITY_CHANGED')
        baseline = bound
        code, rejected = request('/api/data-sync/bundles?generation_id=' + old_generation)
        require(code == 409 and rejected.get('error') == 'GENERATION_NOT_RETAINED_OR_ACK_ELIGIBLE',
            'OLD_GENERATION_AUTHORITY_NOT_REVOKED')
        return {current_generation}
    protection()
    result = {'schema': 'fly_bundle_retirement_operation_v1', 'status': 'INSPECTED',
        'runtime_revision': expected_revision, 'old_generation': old_generation,
        'old_source_revision': old_revision, 'current_generation': current_generation,
        'state_sha256': state_sha, 'retirement_performed': False, 'raw_source_deleted': False}
    if not inspect_only:
        receipt = retire_derivative_generation(volume / 'runtime', output, old_generation,
            current_generation=current_generation, expected_state_sha256=state_sha,
            protected_generations=protection, receipt_path=receipt_path)
        result.update(status=receipt['status'], retirement_performed=True,
            receipt_path=str(receipt_path), receipt_sha256=receipt['receipt_sha256'])
    return result


def main():
    parser = argparse.ArgumentParser()
    for name in ('expected-revision', 'old-generation', 'state-sha', 'current-generation'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    token = os.getenv('BOT_ADMIN_TOKEN')
    require(bool(token), 'ADMIN_TOKEN_UNAVAILABLE')
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs): raise ValueError('REDIRECT_REFUSED')
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    def request(path):
        req = urllib.request.Request('http://127.0.0.1:7002' + path,
            headers={'X-Bot-Admin-Token': token, 'Cache-Control': 'no-cache'})
        try: response = opener.open(req, timeout=5)
        except urllib.error.HTTPError as exc: response = exc
        with response:
            raw = response.read(2 * 1024 * 1024 + 1)
            require(len(raw) <= 2 * 1024 * 1024, 'HTTP_BODY_LIMIT')
            return response.code, json.loads(raw)
    print(json.dumps(run(args.expected_revision, args.old_generation, args.state_sha,
        args.current_generation, request, inspect_only=not args.execute), sort_keys=True))


if __name__ == '__main__':
    main()
