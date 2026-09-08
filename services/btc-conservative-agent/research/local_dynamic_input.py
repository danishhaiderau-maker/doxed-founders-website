"""Bounded local derived inputs. Not a Fly manifest, adapter, or holdout seal."""
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import re

from research.mirror_coherence import assert_mirror_coherent
from research.mirror_generation_lease import MirrorGenerationLease
from research_v3_sealed_holdout import _write_once

MAX_ROWS = 1000
MAX_BYTES = 2 * 1024 * 1024
MAX_ROW_BYTES = 64 * 1024
SCHEMA = 'local_dynamic_input_v1'


def _encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def _safe_path(path):
    path = Path(os.path.abspath(path))
    for part in (*reversed(path.parents), path):
        if part.exists() or part.is_symlink():
            info = part.lstat()
            if part.is_symlink() or int(getattr(info, 'st_file_attributes', 0)) & 0x400:
                raise ValueError('LOCAL_DERIVED_LINK_FORBIDDEN')
    return path


def _directory(repo_root, data_root):
    repo, mirror = _safe_path(repo_root), _safe_path(data_root)
    directory = _safe_path(repo / 'local-derived' / 'dynamic-inputs')
    if directory.is_relative_to(mirror) or mirror.is_relative_to(directory):
        raise ValueError('LOCAL_DERIVED_RAW_OVERLAP')
    return directory


def _text(value):
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= 256:
        raise ValueError('LOCAL_DERIVED_IDENTITY_INVALID')
    return value


def _source(token):
    value = asdict(token)
    value.pop('heartbeat_path')
    if not all(value.values()):
        raise ValueError('LOCAL_DERIVED_SOURCE_IDENTITY_INCOMPLETE')
    return value


def _rows(rows, source):
    result, seen, size = [], set(), 0
    for row in rows:
        if len(result) >= MAX_ROWS:
            raise ValueError('LOCAL_DERIVED_ROW_BUDGET')
        if not isinstance(row, dict):
            raise ValueError('LOCAL_DERIVED_ROW_INVALID')
        episode = _text(row.get('episode_id'))
        if episode in seen:
            raise ValueError('LOCAL_DERIVED_DUPLICATE_EPISODE')
        seen.add(episode)
        for field, key in (('dataset_epoch', 'epoch'), ('source_revision', 'revision'),
                           ('deployed_revision', 'deployed_revision')):
            if row.get(field) != source[key]:
                raise ValueError('LOCAL_DERIVED_MIXED_SOURCE_IDENTITY')
        raw = _encoded(row)
        size += len(raw)
        if len(raw) > MAX_ROW_BYTES or size > MAX_BYTES:
            raise ValueError('LOCAL_DERIVED_BYTE_BUDGET')
        result.append(json.loads(raw))
    return result


def _check(repo, mirror, revision, **kwargs):
    return assert_mirror_coherent(repo_root=repo, data_root=mirror,
        expected_revision=revision, require_canonical_manifest=True, **kwargs)


def write_local_dynamic_input(*, repo_root, data_root, source_revision,
                              analyzer_revision, transformation_signature, config_signature,
                              rows, now=None, mapping_payload=None):
    directory = _directory(repo_root, data_root)
    identities = {key: _text(value) for key, value in {
        'analyzer_revision': analyzer_revision, 'transformation_signature': transformation_signature,
        'config_signature': config_signature}.items()}
    lease = MirrorGenerationLease(data_root, owner='local-dynamic-input')
    lease.acquire(timeout_seconds=0)
    try:
        token = _check(repo_root, data_root, source_revision, now=now)
        source = _source(token)
        body = {'schema': SCHEMA, 'source_generation': source, **identities,
                'rows': _rows(rows, source), 'qualification_allowed': False,
                'provenance_kind': 'LOCAL_DERIVED_NOT_FLY_SOURCE_NOT_SEALED'}
        if mapping_payload is not None:
            from research.local_dynamic_mapping import validate_stored_mapping
            validate_stored_mapping(mapping_payload, body)
            body['mapping_payload'] = json.loads(_encoded(mapping_payload))
        raw = _encoded(body)
        if len(raw) > MAX_BYTES:
            raise ValueError('LOCAL_DERIVED_BYTE_BUDGET')
        digest = hashlib.sha256(raw).hexdigest()
        _check(repo_root, data_root, source_revision, previous=token, held_lease=lease, now=now)
        _safe_path(directory)
        _write_once(directory / (digest + '.json'), body)
        return {'input_sha256': digest, 'source_generation': source, 'row_count': len(body['rows'])}
    finally:
        lease.release()


def load_local_dynamic_input(*, repo_root, data_root, input_sha256, source_revision,
                             analyzer_revision, transformation_signature, config_signature, now=None):
    if not isinstance(input_sha256, str) or not re.fullmatch('[0-9a-f]{64}', input_sha256):
        raise ValueError('LOCAL_DERIVED_DIGEST_INVALID')
    directory = _directory(repo_root, data_root)
    lease = MirrorGenerationLease(data_root, owner='local-dynamic-input-reader')
    lease.acquire(timeout_seconds=0)
    try:
        token = _check(repo_root, data_root, source_revision, now=now)
        with _safe_path(directory / (input_sha256 + '.json')).open('rb') as stream:
            raw = stream.read(MAX_BYTES + 2)
        if len(raw) > MAX_BYTES + 1:
            raise ValueError('LOCAL_DERIVED_BYTE_BUDGET')
        body = json.loads(raw)
        if hashlib.sha256(_encoded(body)).hexdigest() != input_sha256:
            raise ValueError('LOCAL_DERIVED_CHECKSUM_MISMATCH')
        if (body.get('schema') != SCHEMA or body.get('source_generation') != _source(token)
                or body.get('qualification_allowed') is not False):
            raise ValueError('LOCAL_DERIVED_SOURCE_MISMATCH')
        for key, value in {'analyzer_revision': analyzer_revision,
                'transformation_signature': transformation_signature, 'config_signature': config_signature}.items():
            if body.get(key) != _text(value):
                raise ValueError('LOCAL_DERIVED_CONFIG_MISMATCH')
        if not isinstance(body.get('rows'), list):
            raise ValueError('LOCAL_DERIVED_ROW_INVALID')
        _rows(body['rows'], _source(token))
        if 'mapping_payload' in body:
            from research.local_dynamic_mapping import validate_stored_mapping
            validate_stored_mapping(body['mapping_payload'], body)
        _check(repo_root, data_root, source_revision, previous=token, held_lease=lease, now=now)
        return body
    finally:
        lease.release()
