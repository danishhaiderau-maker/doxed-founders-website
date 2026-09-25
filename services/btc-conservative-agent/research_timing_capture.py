"""Signal-time timing scenarios. Declared assumptions never authorize orders."""
import hashlib
import json
import math
import os
from pathlib import Path
import stat

TREATMENT = 'FIXED_EXPIRY_CANCEL_BEFORE_REPLACE'


def load_runtime_timing_config(environ=None):
    """Read a small explicitly pinned nonsecret config; never supply defaults."""
    env = os.environ if environ is None else environ
    name, expected = env.get('BTC_RESEARCH_TIMING_CONFIG_FILE'), env.get('BTC_RESEARCH_TIMING_CONFIG_SHA256')
    if not name and not expected:
        return {}
    try:
        if not name or not expected:
            raise ValueError('TIMING_CONFIG_PIN_PAIR_REQUIRED')
        path = Path(name)
        if not path.is_absolute():
            raise ValueError('TIMING_CONFIG_ABSOLUTE_PATH_REQUIRED')
        for part in (path, *path.parents):
            info = part.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
                raise ValueError('TIMING_CONFIG_LINK_REFUSED')
        before = path.stat()
        if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= 65536:
            raise ValueError('TIMING_CONFIG_SIZE_INVALID')
        with path.open('rb') as handle:
            raw = handle.read(65537)
        after = path.stat()
        if len(raw) != before.st_size or (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
            raise ValueError('TIMING_CONFIG_CHANGED')
        def unique_pairs(items):
            result = {}
            for key, value in items:
                if key in result:
                    raise ValueError('TIMING_CONFIG_DUPLICATE_KEY')
                result[key] = value
            return result
        config = json.loads(raw, object_pairs_hook=unique_pairs)
        if not isinstance(config, dict):
            raise ValueError('TIMING_CONFIG_OBJECT_REQUIRED')
        canonical = json.dumps(config, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
        if hashlib.sha256(canonical).hexdigest() != expected:
            raise ValueError('TIMING_CONFIG_HASH_MISMATCH')
        return {'research_timing_config': config, 'research_timing_config_sha256': expected}
    except (OSError, ValueError, TypeError, OverflowError):
        return {'research_timing_config': None, 'research_timing_config_sha256': None,
                'research_timing_config_status': 'UNAVAILABLE'}


def materialize_timing_declarations(opportunity, snapshot):
    """Bind only explicitly supplied pre-signal config to both capture hashes.

    The caller must supply the activated configuration, not a retrospectively
    selected delay. Missing/invalid configuration produces no assumed variants.
    """
    config = opportunity.get('research_timing_config')
    if config is None:
        return {'status': 'UNAVAILABLE', 'reason': 'TIMING_CONFIG_MISSING', 'declarations': []}
    try:
        if not isinstance(config, dict) or set(config) != {
            'schema', 'epoch_id', 'source_revision', 'tile_config_signature',
            'activated_at_ts', 'delay_seconds', 'ordering_treatment', 'evidence_basis'}:
            raise ValueError('TIMING_CONFIG_SCHEMA_INVALID')
        if (config['schema'] != 'research_timing_config_v1'
                or config['ordering_treatment'] != TREATMENT
                or config['evidence_basis'] != 'DECLARED_SIMULATION'):
            raise ValueError('TIMING_CONFIG_SCHEMA_INVALID')
        for key in ('epoch_id', 'source_revision', 'tile_config_signature'):
            if not isinstance(config[key], str) or not config[key] or config[key] != opportunity.get(key):
                raise ValueError('TIMING_CONFIG_IDENTITY_MISMATCH')
        activated, signal = config['activated_at_ts'], opportunity.get('signal_ts')
        if any(type(v) not in (int, float) or not math.isfinite(v) or v <= 0 for v in (activated, signal)) or activated > signal:
            raise ValueError('TIMING_CONFIG_NOT_PRE_SIGNAL')
        delays = config['delay_seconds']
        if (not isinstance(delays, list) or not 1 <= len(delays) <= 16
                or any(type(v) is not int or not 0 <= v <= 300 for v in delays)
                or len(set(delays)) != len(delays)):
            raise ValueError('TIMING_CONFIG_DELAYS_INVALID')
        raw = json.dumps(config, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
        digest = hashlib.sha256(raw).hexdigest()
        if opportunity.get('research_timing_config_sha256') != digest:
            raise ValueError('TIMING_CONFIG_HASH_MISMATCH')
        captures = snapshot.get('directional_schedules', {}) if isinstance(snapshot, dict) else {}
        if not isinstance(captures, dict) or set(captures) != {'LONG', 'SHORT'}:
            raise ValueError('TIMING_CONFIG_BOTH_CAPTURES_REQUIRED')
        declarations = []
        for side in ('LONG', 'SHORT'):
            capture = captures[side]
            if not isinstance(capture, dict) or capture.get('direction') != side or not capture.get('capture_signature'):
                raise ValueError('TIMING_CONFIG_CAPTURE_INVALID')
            for delay in delays:
                declarations.append(dict(schema='declared_submission_timing_v1',
                    evidence_basis='DECLARED_SIMULATION', provenance='PINNED_RUNTIME_CONFIG:' + digest,
                    declared_at_ts=activated, source_capture_signature=capture['capture_signature'],
                    delay_sec=delay, ordering_treatment=TREATMENT))
        return {'status': 'DECLARED', 'config_sha256': digest, 'declarations': declarations}
    except (ValueError, TypeError, OverflowError) as exc:
        return {'status': 'UNAVAILABLE', 'reason': str(exc), 'declarations': []}
