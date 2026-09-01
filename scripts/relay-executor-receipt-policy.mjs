export function relayExecutorReceiptAccepted({ health, fresh, instancePaused, flatExposure }) {
  const common =
    health?.serviceRole === 'executor-worker' &&
    health?.healthy === true &&
    health?.executionEnabled === true &&
    health?.timeoutCount === 0 &&
    String(health?.ownerId ?? '').trim() !== '';
  if (!common) return false;
  if (fresh) return health.status === 'RUNNING';
  return health.status === 'IDLE' &&
    health.terminalState === 'QUIESCENT' &&
    health.paused === true &&
    health.flatExposure === true &&
    instancePaused === true &&
    flatExposure === true;
}
