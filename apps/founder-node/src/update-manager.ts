import { app, dialog, shell, Tray } from 'electron';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawn } from 'node:child_process';
import { FOUNDER_NODE_APP_VERSION } from '@dcf/founder-vault';
import { semverGt } from './semver';

const GITHUB_RELEASES =
  'https://api.github.com/repos/danishhaiderau-maker/doxed-founders-website/releases?per_page=30';
const CHECK_INTERVAL_MS = 60 * 60 * 1000;
const STARTUP_CHECK_DELAYS_MS = [5_000, 90_000];

export type UpdateAssetKind = 'win-installer' | 'mac-dmg' | 'linux-appimage';

export type UpdateInfo = {
  version: string;
  tag: string;
  downloadUrl: string;
  releasePageUrl: string;
  assetName: string;
  kind: UpdateAssetKind;
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

function pickAssetForThisPlatform(
  assets: Array<{ name: string; browser_download_url: string }>,
): UpdateInfo['kind'] | null {
  const platform = process.platform;
  if (platform === 'win32') {
    if (
      assets.some(
        (a) =>
          /^Founder-Node-\d+\.\d+\.\d+-win-x64\.exe$/i.test(a.name) &&
          !/blockmap/i.test(a.name),
      )
    ) {
      return 'win-installer';
    }
    if (assets.some((a) => /\.exe$/i.test(a.name) && !/blockmap/i.test(a.name))) {
      return 'win-installer';
    }
    return null;
  }
  if (platform === 'darwin') {
    if (assets.some((a) => /\.dmg$/i.test(a.name))) return 'mac-dmg';
    return null;
  }
  if (platform === 'linux') {
    if (assets.some((a) => /\.AppImage$/i.test(a.name))) return 'linux-appimage';
    if (assets.some((a) => /\.deb$/i.test(a.name))) return 'linux-appimage';
    return null;
  }
  return null;
}

function findAsset(
  assets: Array<{ name: string; browser_download_url: string }>,
  kind: UpdateAssetKind,
): { name: string; browser_download_url: string } | undefined {
  if (kind === 'win-installer') {
    return (
      assets.find(
        (a) =>
          /^Founder-Node-\d+\.\d+\.\d+-win-x64\.exe$/i.test(a.name) &&
          !/blockmap/i.test(a.name),
      ) ?? assets.find((a) => /\.exe$/i.test(a.name) && !/blockmap/i.test(a.name))
    );
  }
  if (kind === 'mac-dmg') {
    return assets.find((a) => /\.dmg$/i.test(a.name) && !/blockmap/i.test(a.name));
  }
  return (
    assets.find((a) => /\.AppImage$/i.test(a.name)) ??
    assets.find((a) => /\.deb$/i.test(a.name))
  );
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
  if (!release?.tag_name || !release.assets?.length) return null;

  const kind = pickAssetForThisPlatform(release.assets);
  if (!kind) return null;

  const asset = findAsset(release.assets, kind);
  if (!asset?.browser_download_url) return null;

  const version = release.tag_name.replace(/^founder-node-v/i, '');

  return {
    version,
    tag: release.tag_name,
    downloadUrl: asset.browser_download_url,
    releasePageUrl: release.html_url ?? asset.browser_download_url,
    assetName: asset.name,
    kind,
  };
}

export async function checkForUpdates(options: { silent?: boolean } = {}): Promise<UpdateInfo | null> {
  if (!app.isPackaged || checkInFlight) return pendingUpdate;
  checkInFlight = true;
  try {
    const latest = await fetchLatestRelease();
    if (!latest) {
      if (!options.silent) {
        await dialog.showMessageBox({
          type: 'info',
          title: 'Founder Node',
          message: `No ${process.platform} installer found on GitHub releases yet.`,
          detail: 'Open the releases page to download manually.',
          buttons: ['Open releases', 'OK'],
        }).then(({ response }) => {
          if (response === 0) void shell.openExternal('https://github.com/danishhaiderau-maker/doxed-founders-website/releases/latest');
        });
      }
      return null;
    }

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
        content: `v${latest.version} ready — tray menu → Install update`,
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

async function installWindowsUpdate(dest: string): Promise<void> {
  const args = process.env.PORTABLE_EXECUTABLE_FILE ? [] : ['/S'];
  spawn(dest, args, { detached: true, stdio: 'ignore' }).unref();
  pendingUpdate = null;
  notifyMenuRefresh();
  app.quit();
}

async function installMacUpdate(dest: string, info: UpdateInfo): Promise<void> {
  await shell.openPath(path.dirname(dest));
  await dialog.showMessageBox({
    type: 'info',
    title: 'Install update',
    message: `Founder Node v${info.version} downloaded.`,
    detail:
      'Open the DMG, drag Founder Node to Applications, then launch from Applications. Quit this old tray app when done.',
    buttons: ['Open download folder', 'OK'],
  });
  pendingUpdate = null;
  notifyMenuRefresh();
}

async function installLinuxUpdate(dest: string, info: UpdateInfo): Promise<void> {
  try {
    fs.chmodSync(dest, 0o755);
  } catch {
    /* best effort */
  }
  const installDir = path.join(os.homedir(), 'Applications');
  fs.mkdirSync(installDir, { recursive: true });
  const target = path.join(installDir, info.assetName);
  try {
    fs.copyFileSync(dest, target);
    fs.chmodSync(target, 0o755);
  } catch {
    /* user can run from tmp */
  }

  await dialog.showMessageBox({
    type: 'info',
    title: 'Install update',
    message: `Founder Node v${info.version} ready.`,
    detail: target
      ? `Saved to:\n${target}\n\nRun it from your file manager or terminal, then quit this tray app.`
      : `Downloaded to:\n${dest}\n\nMake executable: chmod +x "${dest}" then run it.`,
    buttons: ['Open folder', 'OK'],
  });
  await shell.openPath(path.dirname(fs.existsSync(target) ? target : dest));
  pendingUpdate = null;
  notifyMenuRefresh();
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
    detail:
      info.kind === 'win-installer'
        ? 'The app will close and the installer will upgrade in place. Restart from the tray or Start Menu after install.'
        : info.kind === 'mac-dmg'
          ? 'We will open the download folder — install from the DMG, then restart Founder Node.'
          : 'We will save the AppImage to ~/Applications — run it, then quit this tray app.',
    buttons: ['Install now', 'Cancel'],
    defaultId: 0,
  });
  if (response !== 0) return;

  if (info.kind === 'win-installer') {
    await installWindowsUpdate(dest);
    return;
  }
  if (info.kind === 'mac-dmg') {
    await installMacUpdate(dest, info);
    return;
  }
  await installLinuxUpdate(dest, info);
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

export function checkForUpdatesAfterSyncFailure(): void {
  if (!app.isPackaged || checkInFlight) return;
  checkForUpdates({ silent: true }).catch(console.warn);
}

export function getPendingUpdate(): UpdateInfo | null {
  return pendingUpdate;
}
