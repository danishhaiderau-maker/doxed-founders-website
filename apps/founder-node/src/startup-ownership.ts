export type StartupOwnership =
  | 'acquired'
  | 'electron-owner-active'
  | 'global-owner-active';

interface StartupOwnershipDeps {
  requestElectronLock: () => boolean;
  releaseElectronLock: () => void;
  acquireGlobalLock: () => boolean;
}

/**
 * Claim Electron's shared-profile lock before touching any cross-path relay.
 * Multiple IDE windows may activate the embedded launcher simultaneously; a
 * losing launch must exit without stopping the relay that already owns it.
 */
export function claimStartupOwnership(
  deps: StartupOwnershipDeps,
): StartupOwnership {
  if (!deps.requestElectronLock()) {
    return 'electron-owner-active';
  }
  if (deps.acquireGlobalLock()) {
    return 'acquired';
  }
  deps.releaseElectronLock();
  return 'global-owner-active';
}
