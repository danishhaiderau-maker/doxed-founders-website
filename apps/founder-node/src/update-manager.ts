import { app, dialog, shell, Tray } from 'electron';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawn } from 'node:child_process';
import { FOUNDER_NODE_APP_VERSION } from '@dcf/founder-vault';
import { semverGt } from './semver';

const GITHUB_RELEASES =
  'https://api.github.com/repos/danishhaiderau-maker/doxed-founders-website/releases?per_page=30';
const CHECK_INTERVAL_MS = 2 * 60 * 60 * 1000;
const STARTUP_CHECK_DELAYS_MS = [5_000, 120_000];

export type UpdateInfo = {
  version: string;
  tag: string;
  downloadUrl: string;
  releasePageUrl: string;
  assetName: string;
};

let trayRef: Tray | null = null;
let updateCheckTimer: ReturnType<typeof setInterval> | null = null;
let pendingUpdate: UpdateInfo | null = null;
let checkInFlight = false;
let menuRefresh: (() => void) | null = null;

export function bindUpdateTray(tray: Tray): void {
  trayRef = tray;
}

export function setUpdateMenuRefresh(fn: () => void): void {
  menuRefresh = fn;
}

function notifyMenuRefresh(): void {
  menuRefresh?.();
}

export async function fetchLatestRelease(): Promise<UpdateInfo | null> {
  const res = await fetch(GITHUB_RELEASES, {
    headers: { Accept: 'application/vnd.github+json', 'User-Agent': 'Founder-Node-Updater' },
  });
  if (!res.ok) throw new Error(`Release check failed (${res.status})`);

  const releases = (await res.json()) as Array<{
    tag_name?: string;
    html_url?: string;
    assets?: Array<{ name: string; browser_download_url: string }>;
  }>;

  const release = releases.find((r) => r.tag_name?.startsWith('founder-node-v'));
  if (!release?.tag_name) return null;

  const version = release.tag_name.replace(/^founder-node-v/i, '');
  const asset =
    release.assets?.find(
      (a) =>
        /^Founder-Node-\d+\.\d+\.\d+-win-x64\.exe$/i.test(a.name) &&
        !a.name.toLowerCase().includes('blockmap'),
    ) ?? release.assets?.find((a) => /\.exe$/i.test(a.name) && !a.name.toLowerCase().includes('blockmap'));

  if (!asset?.browser_download_url) return null;

  return {
    version,
    tag: release.tag_name,
    downloadUrl: asset.browser_download_url,
    releasePageUrl: release.html_url ?? asset.browser_download_url,
    assetName: asset.name,
  };
}

export async function checkForUpdates(options: { silent?: boolean } = {}): Promise<UpdateInfo | null> {
  if (!app.isPackaged || checkInFlight) return pendingUpdate;
  checkInFlight = true;
  try {
    const latest = await fetchLatestRelease();
    if (!latest) return null;

    if (!semverGt(latest.version, FOUNDER_NODE_APP_VERSION)) {
      pendingUpdate = null;
      notifyMenuRefresh();
      if (!options.silent) {
        await dialog.showMessageBox({
          type: 'info',
          title: 'Founder Node',
          message: `You're on the latest version (v${FOUNDER_NODE_APP_VERSION}).`,
        });
      }
      return null;
    }

    pendingUpdate = latest;
    notifyMenuRefresh();
    if (options.silent) {
      trayRef?.displayBalloon({
        title: 'Founder Node update available',
        content: `v${latest.version} is ready — open tray menu → Install update`,
      });
    } else {
      const { response } = await dialog.showMessageBox({
        type: 'info',
        title: 'Update available',
        message: `Founder Node v${latest.version} is available (you have v${FOUNDER_NODE_APP_VERSION}).`,
        detail: 'Install now? Your vault in ~/FounderVault stays untouched.',
        buttons: ['Install update', 'Later', 'View release'],
        defaultId: 0,
        cancelId: 1,
      });
      if (response === 0) await downloadAndInstallUpdate(latest);
      if (response === 2) await shell.openExternal(latest.releasePageUrl);
    }
    return latest;
  } catch (err) {
    if (!options.silent) {
      await dialog.showMessageBox({
        type: 'error',
        title: 'Update check failed',
        message: err instanceof Error ? err.message : String(err),
      });
    }
    console.warn('Update check failed:', err);
    return null;
  } finally {
    checkInFlight = false;
  }
}

export async function downloadAndInstallUpdate(info: UpdateInfo = pendingUpdate!): Promise<void> {
  if (!info) return;

  const dir = path.join(os.tmpdir(), 'FounderNodeUpdate');
  fs.mkdirSync(dir, { recursive: true });
  const dest = path.join(dir, info.assetName);

  const res = await fetch(info.downloadUrl);
  if (!res.ok) throw new Error(`Download failed (${res.status})`);
  const buf = Buffer.from(await res.arrayBuffer());
  fs.writeFileSync(dest, buf);

  const { response } = await dialog.showMessageBox({
    type: 'info',
    title: 'Installing update',
    message: `Founder Node v${info.version} downloaded.`,
    detail: 'The app will close and the installer will upgrade in place. Restart from the tray or Start Menu after install.',
    buttons: ['Install & restart', 'Cancel'],
    defaultId: 0,
  });
  if (response !== 0) return;

  const args = process.env.PORTABLE_EXECUTABLE_FILE ? [] : ['/S'];
  spawn(dest, args, { detached: true, stdio: 'ignore' }).unref();
  pendingUpdate = null;
  notifyMenuRefresh();
  app.quit();
}

export function startAutoUpdateChecks(): void {
  if (!app.isPackaged) return;

  for (const delay of STARTUP_CHECK_DELAYS_MS) {
    setTimeout(() => {
      checkForUpdates({ silent: true }).catch(console.warn);
    }, delay);
  }

  if (updateCheckTimer) return;
  updateCheckTimer = setInterval(() => {
    checkForUpdates({ silent: true }).catch(console.warn);
  }, CHECK_INTERVAL_MS);
}

/** Run a silent update check when sync is failing (may fix offline bugs in older builds). */
export function checkForUpdatesAfterSyncFailure(): void {
  if (!app.isPackaged || checkInFlight) return;
  checkForUpdates({ silent: true }).catch(console.warn);
}

export function getPendingUpdate(): UpdateInfo | null {
  return pendingUpdate;
}
