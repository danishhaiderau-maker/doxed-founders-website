"""Explicit local reset entrypoint. Default is validation, not deletion."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re

from local_research_reset import reset_local_research
from local_research_reset_audit import make_local_reset_auditor
from research.mirror_generation_lease import MirrorGenerationLease
from research_exact_deletion import _checked_path, ResearchDeletionRejected


CANONICAL_ROOT = Path('C:/DoxxedCrypto/btc-v31-current/services/btc-conservative-agent/canonical-research-data')


def _read_regular(path, maximum):
    import stat
    path = Path(path)
    if not path.is_absolute():
        raise ValueError('LOCAL_RESET_ABSOLUTE_PATH_REQUIRED')
    for part in (path, *path.parents):
        info = part.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('LOCAL_RESET_LINK_REFUSED')
    before = path.stat()
    if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= maximum:
        raise ValueError('LOCAL_RESET_RECEIPT_SIZE_INVALID')
    with path.open('rb') as handle:
        raw = handle.read(maximum + 1)
    after = path.stat()
    sig = lambda s:(s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns)
    if len(raw) != before.st_size or sig(before) != sig(after):
        raise ValueError('LOCAL_RESET_RECEIPT_CHANGED')
    return raw


def run(*, root, fly_receipt, fly_sha256, revision, new_epoch, reset_id, execute=False):
    root = Path(root).absolute()
    if root != CANONICAL_ROOT.absolute() or not root.is_dir():
        raise ValueError('LOCAL_RESET_CANONICAL_ROOT_REQUIRED')
    if (not re.fullmatch(r'[0-9a-f]{64}', fly_sha256)
            or not re.fullmatch(r'[0-9a-f]{40}', revision)
            or not re.fullmatch(r'epoch-[0-9a-f]{24}', new_epoch)
            or not re.fullmatch(r'[0-9a-f]{24}', reset_id)):
        raise ValueError('LOCAL_RESET_ARGUMENT_IDENTITY_INVALID')
    raw = _read_regular(Path(fly_receipt), 16 * 1024**2)
    if hashlib.sha256(raw).hexdigest() != fly_sha256:
        raise ValueError('LOCAL_RESET_FLY_PIN_MISMATCH')
    directory = _checked_path(root/'research_reset_receipts'/reset_id, root)
    journal = _checked_path(directory/'operation.json', root)
    audit_path = _checked_path(directory/'initial-audit.json', root)
    with MirrorGenerationLease(root, owner='explicit-local-reset-cli').acquire(timeout_seconds=0) as lease:
        prior, prior_sha = None, None
        if journal.exists():
            joint = json.loads(_read_regular(journal, 16 * 1024**2))
            prior = _read_regular(audit_path, 16 * 1024**2)
            prior_sha = hashlib.sha256(prior).hexdigest()
            if joint.get('binding', {}).get('initial_audit_sha256') != prior_sha:
                raise ValueError('LOCAL_RESET_PRIOR_AUDIT_MISMATCH')
        auditor = make_local_reset_auditor(lease=lease, fly_receipt_bytes=raw,
            fly_receipt_sha256=fly_sha256, expected_new_epoch=new_epoch, expected_revision=revision,
            journal_path=journal, prior_audit_bytes=prior, prior_audit_sha256=prior_sha)
        def capture(root):
            result = auditor(root)
            if execute and prior is None:
                data = result['recovery_receipt_bytes']
                directory.mkdir(parents=True, exist_ok=True)
                if audit_path.exists():
                    if _read_regular(audit_path, 16 * 1024**2) != data:
                        raise ValueError('LOCAL_RESET_AUDIT_OVERWRITE_REFUSED')
                else:
                    with audit_path.open('xb') as handle:
                        handle.write(data)
                        handle.flush()
                        os.fsync(handle.fileno())
            return result
        return reset_local_research(root=root, lease=lease, fly_receipt_bytes=raw,
            fly_receipt_sha256=fly_sha256, expected_new_epoch=new_epoch, expected_revision=revision,
            audit_owners=capture, journal_path=journal, validate_only=not execute)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('root','fly-receipt','fly-sha256','revision','new-epoch','reset-id'):
        parser.add_argument('--'+name, required=True)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    try:
        result = run(**vars(args))
        print(json.dumps({'status':result.get('status') or result.get('stage'),
                          'deletion_requested':args.execute}, sort_keys=True))
    except (ValueError, OSError, RuntimeError) as exc:
        code = str(exc)
        if not re.fullmatch(r'[A-Z_]+', code):
            code = 'LOCAL_RESET_FAILED_REVIEW_RECEIPTS'
        print(json.dumps({'status':'FAILED','error':code}))
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
