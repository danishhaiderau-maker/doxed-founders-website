"""Bounded same-generation attempts; caller retains the singleton owner lock.

Never called from GET handlers. Each attempt retains runtime's independent
slice/deadline/admission guards and resumes the existing durable checkpoint.
"""
from copy import deepcopy
import threading

from data_sync_bundle_runtime import run_managed_generation

RETRYABLE = frozenset({"BUNDLE_COORDINATOR_BUDGET", "BUNDLE_COORDINATOR_SLICE_LIMIT"})


def run_resumable_generation(metadata, source_root, output_root, *, pressure_probe,
                             generation_available, stop_event=None,
                             publish=lambda receipt: None, max_attempts=4,
                             max_slices=512, max_seconds=1800,
                             attempt_runner=None):
    """Retry only exhausted scheduling budgets, at most eight bounded attempts.

    No failure, authority rejection, no-progress or arbitrary DEFERRED retries.
    This function owns no additional process/thread and performs no source I/O.
    """
    for value, upper in ((max_attempts, 8), (max_slices, 512), (max_seconds, 1800)):
        if type(value) is not int or not 1 <= value <= upper:
            raise ValueError("INVALID_RESUMPTION_LIMIT")
    frozen = deepcopy(metadata)
    stop = stop_event if stop_event is not None else threading.Event()
    runner = attempt_runner or run_managed_generation
    for attempt in range(max_attempts):
        if stop.is_set():
            return {"status": "STOPPED"}
        result = runner(deepcopy(frozen), source_root, output_root,
                        pressure_probe=pressure_probe,
                        generation_available=generation_available,
                        stop_event=stop, publish=publish,
                        max_slices=max_slices, max_seconds=max_seconds)
        if (result.get("status") != "DEFERRED" or result.get("error") not in RETRYABLE
                or attempt + 1 == max_attempts):
            return result
        # Interruptible capped exponential backoff; no timer survives this owner.
        if stop.wait(min(30, 3 * (2 ** attempt))) or stop.is_set():
            return {"status": "STOPPED"}
