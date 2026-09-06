"""Best-effort request-bound progress, never a reset authority receipt."""
import json
import os
from pathlib import Path
import re
import tempfile
import time

from research_exact_deletion import _checked_path

PHASES=frozenset({'EXECUTOR_FINGERPRINT','DELETER_FINGERPRINT','PRE_UNLINK_REVALIDATION'})


def make_reset_progress_callback(runtime_root, *, attempt_id, reset_id, scope_name=None):
    for value, length in ((attempt_id,32),(reset_id,24)):
        if not isinstance(value,str) or not re.fullmatch('[0-9a-f]{'+str(length)+'}',value):
            raise ValueError('RESET_PROGRESS_ID_INVALID')
    if scope_name is not None and (not isinstance(scope_name,str) or not re.fullmatch('[A-Za-z0-9_-]{1,64}',scope_name)):
        raise ValueError('RESET_PROGRESS_SCOPE_INVALID')
    root=Path(runtime_root).absolute()
    folder=_checked_path(root/'research_reset_receipts'/'_progress',root)
    destination=_checked_path(folder/(attempt_id+'-'+reset_id+'-'+(scope_name or 'runtime')+'.json'),root)
    previous_phase=None
    last_write=None
    def callback(event):
        nonlocal previous_phase,last_write
        if not isinstance(event,dict) or event.get('phase') not in PHASES:
            return False
        values=[event.get(k) for k in ('completed_targets','total_targets','fingerprinted_bytes')]
        if any(type(v) is not int or v<0 for v in values):
            return False
        completed,total,byte_count=values
        if completed>total or total>200000 or byte_count>64*1024**3:
            return False
        now=time.monotonic()
        phase=event['phase']
        if phase==previous_phase and completed!=total and last_write is not None and now-last_write<5:
            return False
        body={'schema':'research_reset_latest_progress_v1','attempt_id':attempt_id,
              'reset_id':reset_id,'scope_name':scope_name,'phase':phase,
              'completed_targets':completed,'total_targets':total,'fingerprinted_bytes':byte_count,
              'observed_at_ts':time.time(),'authority':'ADVISORY_ONLY'}
        raw=json.dumps(body,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
        if len(raw)>4096:
            return False
        # Failed storage attempts must also be throttled under ENOSPC.
        previous_phase,last_write=phase,now
        candidate=None
        try:
            _checked_path(folder,root).mkdir(parents=True,exist_ok=True)
            _checked_path(destination,root)
            fd,name=tempfile.mkstemp(prefix='.progress-',dir=folder)
            candidate=Path(name)
            with os.fdopen(fd,'wb') as stream:
                stream.write(raw); stream.flush(); os.fsync(stream.fileno())
            _checked_path(destination,root)
            os.replace(candidate,destination)
            if os.name != 'nt':
                directory_fd=os.open(folder,os.O_RDONLY)
                try: os.fsync(directory_fd)
                finally: os.close(directory_fd)
            return True
        except (OSError,ValueError):
            return False
        finally:
            if candidate is not None:
                try: candidate.unlink(missing_ok=True)
                except OSError: pass
    return callback
