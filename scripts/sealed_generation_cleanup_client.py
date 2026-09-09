"""Explicit local cleanup tooling. Default plan performs NO network requests.

Requires independently produced sealed mapping and triple-copy ACK. Local files
are trusted operator state, not proof against an owner rewriting that state.
An uncertain request is never retried automatically; server recovery is separate.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import urllib.request
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'services' / 'btc-conservative-agent'))
from raw_generation_cleanup import verify_generation
from raw_generation_cleanup_owner import RawGenerationCleanupOwner


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def read(path):
    with Path(path).open('rb') as handle:
        raw = handle.read(2 * 1024 * 1024 + 1)
    if len(raw) > 2 * 1024 * 1024:
        raise ValueError('PROOF_READ_LIMIT')
    return json.loads(raw)


def once(path, value):
    raw = encoded(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation is also the cross-process request admission fence.
    with path.open('xb') as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())


def canonical_origin(endpoint):
    lock = read(Path(__file__).resolve().parents[1] / 'config' / 'fly-canonical.lock.json')
    expected = str(lock.get('sourceUrl') or '').rstrip('/')
    if (lock.get('frozen') is not True or lock.get('desktopBotEnabled') is not False
            or expected != 'https://doxed-btc-bot.fly.dev'
            or endpoint.rstrip('/') != expected):
        raise ValueError('REFUSED_NON_CANONICAL_UPSTREAM')
    return expected


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError('ADMIN_REDIRECT_REFUSED')


def execute(*, source_root, manifest, ack, identity, generation_id, receipts,
            endpoint, action='plan', confirmation=None, post=None):
    if action not in {'plan', 'register', 'quarantine', 'purge'}:
        raise ValueError('ACTION_INVALID')
    parsed = urlsplit(endpoint)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {'', '/'}:
        raise ValueError('HTTPS_ORIGIN_REQUIRED')
    endpoint = canonical_origin(endpoint)
    if manifest.get('generation_id') != generation_id or manifest.get('identity') != identity:
        raise ValueError('EXPECTED_GENERATION_IDENTITY_MISMATCH')
    if manifest.get('caught_up_cycle_complete') is not True or len(manifest.get('members') or []) != 1:
        raise ValueError('SEALED_MAPPING_REQUIRED')
    owner = RawGenerationCleanupOwner(Path(source_root), identity=lambda: identity,
                                     leases=lambda _: {})
    source = owner._source(manifest)
    proof = verify_generation(source, manifest, ack, current_identity=identity, active_leases={})
    # Local preflight cannot establish absence of Fly leases. Server revalidates
    # authority and takes its cleanup gate at every explicit mutation boundary.
    job = {'generation_id': generation_id, 'identity': identity,
           'manifest_sha256': manifest['manifest_sha256'],
           'acknowledgement_sha256': ack['acknowledgement_sha256'],
           'endpoint': endpoint.rstrip('/'), 'proof_sha256': proof['proof_sha256']}
    if action == 'plan':
        return {'status': 'LOCAL_PLAN_ONLY_SERVER_LEASES_UNKNOWN', 'job': job,
                'planned_bytes': proof['source_bytes'], 'freed_bytes': 0,
                'source_cleanup_authorized': False}
    if confirmation != f'{action.upper()}:{generation_id}':
        raise ValueError('EXACT_ACTION_CONFIRMATION_REQUIRED')
    key = hashlib.sha256(encoded(job)).hexdigest()
    directory = Path(receipts) / key
    done = directory / f'{action}.result.json'
    intent = directory / f'{action}.intent.json'
    if done.exists():
        saved = read(done)
        if saved.get('job') != job or saved.get('action') != action:
            raise ValueError('LOCAL_RECEIPT_CONFLICT')
        return {**saved['response'], 'local_receipt_reused': True}
    if intent.exists():
        raise ValueError('PRIOR_REQUEST_UNCERTAIN_EXPLICIT_SERVER_RECOVERY_REQUIRED')
    previous = {'quarantine': 'register', 'purge': 'quarantine'}.get(action)
    if previous:
        prior = read(directory / f'{previous}.result.json')
        if prior.get('job') != job or prior.get('action') != previous:
            raise ValueError('PRIOR_PHASE_RECEIPT_MISMATCH')
    if post is None:
        raise ValueError('AUTHENTICATED_TRANSPORT_REQUIRED')
    body = {'manifest': manifest, 'acknowledgement': ack} if action == 'register' else {'generation_id': generation_id}
    if action == 'purge':
        body['confirmation'] = 'PURGE_SEALED_RAW_GENERATION:' + generation_id
    route = 'authority' if action == 'register' else action
    once(intent, {'job': job, 'action': action})
    response = post(job['endpoint'] + '/api/data-sync/raw-generation/' + route, body)
    expected = {'register': 'RAW_GENERATION_AUTHORITY_REGISTERED_SOURCE_RETAINED',
                'quarantine': 'QUARANTINED_SOURCE_RETAINED'}
    if (not isinstance(response, dict) or response.get('ok') is not True
            or response.get('generation_id') != generation_id
            or (action in expected and response.get('status') != expected[action])
            or (action == 'register' and response.get('proof_sha256') != proof['proof_sha256'])
            or (action == 'purge' and (response.get('state') != 'PURGED'
                or response.get('schema') != 'raw_generation_purge_receipt_v1'))):
        raise ValueError('SERVER_PHASE_NOT_VERIFIED_SOURCE_STATE_UNKNOWN')
    if action == 'purge':
        material = {k: v for k, v in response.items() if k not in {'ok', 'receipt_sha256', 'owner_space_observation'}}
        # Owner adds post-operation space diagnostics; receipt hash binds only
        # actual receipt fields. Require downstream review if shape differs.
        if hashlib.sha256(encoded(material)).hexdigest() != response.get('receipt_sha256'):
            raise ValueError('PURGE_RECEIPT_HASH_UNVERIFIED')
    once(done, {'job': job, 'action': action, 'response': response})
    return response


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for field in ('source-root', 'manifest', 'ack', 'identity', 'generation-id', 'receipts', 'endpoint'):
        parser.add_argument('--' + field, required=True)
    parser.add_argument('--action', choices=('plan', 'register', 'quarantine', 'purge'), default='plan')
    parser.add_argument('--confirmation')
    args = vars(parser.parse_args())
    for field in ('manifest', 'ack', 'identity'):
        args[field] = read(args[field])
    def post(url, body):
        token = os.environ.get('BOT_ADMIN_TOKEN')
        if not token:
            raise ValueError('ADMIN_TOKEN_MISSING')
        request = urllib.request.Request(url, data=encoded(body), method='POST',
            headers={'Content-Type': 'application/json', 'X-Bot-Admin-Token': token})
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=30) as response:
            raw = response.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024:
            raise ValueError('RESPONSE_READ_LIMIT')
        return json.loads(raw)
    print(json.dumps(execute(**args, post=post), sort_keys=True))


if __name__ == '__main__':
    main()
