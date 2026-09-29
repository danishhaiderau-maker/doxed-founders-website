"""Use the runtime's existing reset gate; never create a substitute lock."""


def run_research_writer(*, gate, reset_active, write):
    """Skip busy/reset work; invoke the callback only under the shared gate.

    The caller supplies the authoritative active-reset check, including failed
    attempts still awaiting reconciliation. Exceptions remain failures, not a
    successful write. This is an in-process barrier, not a process-wide lease.
    """
    if not gate.acquire(blocking=False):
        return {'status': 'SKIPPED', 'reason_code': 'RESEARCH_WRITER_GATE_BUSY'}
    try:
        if reset_active() is not False:
            return {'status': 'SKIPPED', 'reason_code': 'RESEARCH_RESET_ACTIVE_OR_UNKNOWN'}
        return {'status': 'WRITTEN', 'result': write()}
    finally:
        gate.release()
