import { app } from 'electron';
import os from 'node:os';
import path from 'node:path';
import { FOUNDER_VAULT_DIR_NAME } from '@dcf/founder-vault';

/** One Electron profile for portable + NSIS installs so single-instance lock works. */
export function sharedElectronUserDataDir(): string {
  return path.join(os.homedir(), FOUNDER_VAULT_DIR_NAME, 'electron-user-data');
}

export function configureSharedElectronUserData(): void {
  if (app.isReady()) return;
  app.setPath('userData', sharedElectronUserDataDir());
}
