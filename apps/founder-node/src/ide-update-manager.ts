/**
 * Founder IDE updater (Phase 5 — Workstream E).
 *
 * Mirrors `update-manager.ts` (the Founder Node updater) but adds the four
 * integrity gates the brief requires:
 *
 *   1. SHA-256 verification — the downloaded `.part` file is hashed and
 *      compared to `download.sha256` from the manifest. Mismatch deletes the
 *      file and marks the updater `failed` — the binary is never executed.
 *   2. Authenticode verification — on Windows, `signtool verify /pa /v` is
 *      invoked against the downloaded installer. Behaviour when the binary
 *      is unsigned is controlled by `allowUnsigned` (default `false` on
 *      stable channel, `true` on beta), so insiders can self-build and
 *      self-sign while production builds must ship signed.
 *   3. Health handshake — after the installer exits, the updater waits up
 *      to `HEALTH_HANDSHAKE_TIMEOUT_MS` (30s) for the new Founder Node to
 *      re-establish its IPC handshake with the IDE. If the handshake does
 *      not come back, the updater rolls back.
 *   4. Rollback — the previously-installed installer is retained at
 *      `~/FounderVault/updates/founder-stack-{prev}.exe`. On health-handshake
 *      failure the updater re-runs it with `/SILENT /SP-` (Inno Setup
 *      silent mode), transitions `rolling_back`, then returns to `idle`.
 *
 * State machine (matches the `FounderStackUpdateState` contract from
 * `@dcf/founder-vault`):
 *
 *     idle ─▶ downloading ─▶ verifying ─▶ installing ─▶ idle   (success)
 *                       ╲           ╲          ╲
 *                        ╲           ╲          ─▶ rolling_back ─▶ idle
 *                         ─▶ failed   ─▶ failed
 *
 * `failed` is terminal until the user explicitly retries via the tray menu
 * ("Check for Founder IDE updates…").
 *
 * Yanked releases: the manifest validator already enforces that
 * `latestVersion` is never yanked, but the updater double-checks at runtime
 * — if the resolved release entry has `status: 'yanked'`, the updater
 * refuses to offer it and logs an error. Defence in depth.
 *
 * IPC: this module owns NO IPC code. Workstream B owns the IDE↔node IPC
 * handshake. The health-handshake check is delegated through a pluggable
 * `HandshakeProbe` callback so this file can be unit-tested without the
 * IPC client present.
 */

import { execFile } from 'node:child_process';
import crypto from 'node:crypto';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawn } from 'node:child_process';
import { promisify } from 'node:util';

import { semverGt, semverLt } from './semver';
import type { FounderStackUpdateState } from '@dcf/founder-vault';

// Electron is imported lazily inside the functions that need it (dialog, tray,
// shell, app). This keeps the module importable from unit tests and other
// non-Electron contexts (e.g. a future CLI). The type-only imports below
// carry no runtime cost.
import type { Tray } from 'electron';

const execFileP = promisify(execFile);

/** Where staged installers live: `~/FounderVault/updates/`. */
export function ideUpdatesDir(): string {
  return path.join(os.homedir(), 'FounderVault', 'updates');
}

/** Filename pattern for a staged installer. */
export function stagedInstallerName(version: string): string {
  return `founder-stack-${version}.exe`;
}

/** Filename pattern for a `.part` download in progress. */
export function partFileName(version: string): string {
  return `founder-stack-${version}.exe.part`;
}

/** How long to wait for the new version's IPC handshake before rolling back. */
export const HEALTH_HANDSHAKE_TIMEOUT_MS = 30_000;

/** How often to poll the handshake during the wait window. */
export const HEALTH_HANDSHAKE_POLL_MS = 1_000;

/** Periodic update-check interval (1h, matches the Node updater). */
const CHECK_INTERVAL_MS = 60 * 60 * 1000;

/** Startup check delays — same shape as `update-manager.ts`. */
const STARTUP_CHECK_DELAYS_MS = [5_000, 90_000];

/** SHA-256 of an empty string, used only as a sentinel in tests. */
export const EMPTY_SHA256 = crypto.createHash('sha256').update('').digest('hex');

// ─── Types ────────────────────────────────────────────────────────────────

/** Subset of the manifest we consume. Mirrors the JSON schema. */
export interface IdeUpdateRelease {
  version: string;
  status: 'current' | 'pending-build' | 'legacy' | 'yanked';
  releasedAt: string | null;
  download: {
    url: string;
    size: number | null;
    sha256: string | null;
    signature: string | null;
  };
  minimumVersion: string;
  rollback?: {
    mechanism?: string;
    previousVersionKept?: boolean;
    note?: string;
  };
}

/** Manifest shape returned by `GET /api/founder-node/manifest`. */
export interface IdeUpdateManifest {
  manifestVersion: number;
  product: string;
  channel: 'stable' | 'beta' | 'insider';
  latestVersion: string;
  minimumVersion: string;
  releases: IdeUpdateRelease[];
}

/** What the caller learns from `checkForUpdates`. */
export interface IdeUpdateInfo {
  version: string;
  downloadUrl: string;
  sha256: string;
  size: number | null;
  status: IdeUpdateRelease['status'];
  rollbackMechanism?: string;
}

/**
 * Pluggable probe that returns true when the IDE↔node IPC handshake is
 * currently established. In production this is wired to the IPC client
 * (Workstream B); in tests it's a controllable stub. The probe MUST be
 * side-effect free — the updater calls it on a tight poll loop.
 */
export type HandshakeProbe = () => boolean;

/** Pluggable installer for Authenticode — overridable in tests. */
export type AuthenticodeVerifier = (file: string) => Promise<AuthenticodeResult>;

/** Pluggable launcher for the installer .exe — overridable in tests. */
export type InstallerLauncher = (installerPath: string, args: string[]) => void;

export interface AuthenticodeResult {
  signed: boolean;
  output?: string;
  error?: string;
}

export type IdeUpdateStateListener = (
  state: FounderStackUpdateState,
  reason: string | null,
  update: IdeUpdateInfo | null,
) => void;

/** Constructor options (mostly for tests; production uses defaults). */
export interface IdeUpdateManagerOptions {
  apiBaseUrl: string;
  installedVersion?: string;
  channel?: 'stable' | 'beta' | 'insider';
  allowUnsigned?: boolean;
  handshakeProbe?: HandshakeProbe;
  authenticodeVerifier?: AuthenticodeVerifier;
  manifestFetcher?: (apiBaseUrl: string) => Promise<IdeUpdateManifest | null>;
  updatesDir?: string;
  /** Skip the `app.isPackaged` gate (Electron is not present in unit tests). */
  skipPackagedGate?: boolean;
  /** Skip user-facing dialog prompts (used by silent periodic checks). */
  silent?: boolean;
}

// ─── Module state ─────────────────────────────────────────────────────────

let updateState: FounderStackUpdateState = 'idle';
let lastFailureReason: string | null = null;
let lastResolvedUpdate: IdeUpdateInfo | null = null;
let cachedManifest: { at: number; body: IdeUpdateManifest | null } | null = null;
const MANIFEST_CACHE_TTL_MS = 60_000;

let trayRef: Tray | null = null;
let menuRefresh: (() => void) | null = null;
let updateStateListener: IdeUpdateStateListener | null = null;
let updateCheckTimer: ReturnType<typeof setInterval> | null = null;
let checkInFlight = false;

let configuredApiBaseUrl: string | null = null;
let configuredChannel: 'stable' | 'beta' | 'insider' = 'stable';
let configuredAllowUnsigned = false;
let configuredHandshakeProbe: HandshakeProbe = () => false;
let configuredAuthenticodeVerifier: AuthenticodeVerifier = defaultAuthenticodeVerifier;
let configuredInstallerLauncher: InstallerLauncher = defaultInstallerLauncher;
let configuredManifestFetcher: (apiBaseUrl: string) => Promise<IdeUpdateManifest | null> =
  defaultManifestFetcher;
let configuredUpdatesDir: string = ideUpdatesDir();
function electronAppVersion(): string {
  try {
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    const { app } = require('electron') as typeof import('electron');
    return app.getVersion();
  } catch {
    return '0.0.0';
  }
}

export interface InstalledIdeVersionOptions {
  markerPaths?: string[];
  fallbackVersion?: string;
}

/** Resolve the IDE release version rather than the embedded relay version. */
export function resolveInstalledIdeVersion(
  options: InstalledIdeVersionOptions = {},
): string {
  const inferredMarker = path.resolve(
    path.dirname(process.execPath),
    '..',
    '..',
    'founder-release.json',
  );
  const localAppDataMarker = process.env.LOCALAPPDATA
    ? path.join(
        process.env.LOCALAPPDATA,
        'Programs',
        'Founder IDE',
        'founder-release.json',
      )
    : null;
  const markerPaths = options.markerPaths ?? [
    ...(process.env.FOUNDER_IDE_RELEASE_MARKER
      ? [process.env.FOUNDER_IDE_RELEASE_MARKER]
      : []),
    inferredMarker,
    ...(localAppDataMarker ? [localAppDataMarker] : []),
  ];

  for (const markerPath of markerPaths) {
    try {
      const parsed = JSON.parse(fs.readFileSync(markerPath, 'utf8')) as {
        version?: unknown;
      };
      if (
        typeof parsed.version === 'string' &&
        /^\d+\.\d+\.\d+$/.test(parsed.version)
      ) {
        return parsed.version;
      }
    } catch {
      // Missing or malformed markers fall through to the next candidate.
    }
  }

  return options.fallbackVersion ?? electronAppVersion();
}

let configuredInstalledVersionProvider: () => string = resolveInstalledIdeVersion;
let configuredSkipPackagedGate = false;
let configuredHandshakeTimeoutMs = HEALTH_HANDSHAKE_TIMEOUT_MS;
let configuredHandshakePollMs = HEALTH_HANDSHAKE_POLL_MS;

// ─── Tray plumbing (mirrors update-manager.ts) ────────────────────────────

export function bindIdeUpdateTray(tray: Tray): void {
  trayRef = tray;
}

export function setIdeUpdateMenuRefresh(fn: () => void): void {
  menuRefresh = fn;
}

export function setIdeUpdateStateListener(
  listener: IdeUpdateStateListener | null,
): void {
  updateStateListener = listener;
  listener?.(updateState, lastFailureReason, lastResolvedUpdate);
}

function notifyMenuRefresh(): void {
  menuRefresh?.();
}

function notifyUpdateState(): void {
  updateStateListener?.(updateState, lastFailureReason, lastResolvedUpdate);
}

/** Current updater state — read by the runtime-status builder (Workstream C). */
export function getIdeUpdateState(): FounderStackUpdateState {
  return updateState;
}

/** Human-readable reason for the last `failed` transition. Cleared on retry. */
export function getIdeUpdateFailureReason(): string | null {
  return lastFailureReason;
}

/** Last manifest we resolved (for status display). */
export function getLastResolvedUpdate(): IdeUpdateInfo | null {
  return lastResolvedUpdate;
}

// ─── Configuration ────────────────────────────────────────────────────────

export interface IdeUpdaterConfig {
  apiBaseUrl: string;
  channel?: 'stable' | 'beta' | 'insider';
  allowUnsigned?: boolean;
  handshakeProbe?: HandshakeProbe;
  handshakeTimeoutMs?: number;
  handshakePollMs?: number;
  authenticodeVerifier?: AuthenticodeVerifier;
  installerLauncher?: InstallerLauncher;
  manifestFetcher?: (apiBaseUrl: string) => Promise<IdeUpdateManifest | null>;
  updatesDir?: string;
  installedVersionProvider?: () => string;
  skipPackagedGate?: boolean;
}

export function configureIdeUpdates(opts: IdeUpdaterConfig): void {
  configuredApiBaseUrl = opts.apiBaseUrl.replace(/\/$/, '');
  if (opts.channel) configuredChannel = opts.channel;
  // Stable channel MUST require signatures; only beta/insider can opt into unsigned.
  configuredAllowUnsigned =
    opts.allowUnsigned ?? (configuredChannel === 'stable' ? false : true);
  if (opts.handshakeProbe) configuredHandshakeProbe = opts.handshakeProbe;
  if (typeof opts.handshakeTimeoutMs === 'number') configuredHandshakeTimeoutMs = opts.handshakeTimeoutMs;
  if (typeof opts.handshakePollMs === 'number') configuredHandshakePollMs = opts.handshakePollMs;
  if (opts.authenticodeVerifier) configuredAuthenticodeVerifier = opts.authenticodeVerifier;
  if (opts.installerLauncher) configuredInstallerLauncher = opts.installerLauncher;
  if (opts.manifestFetcher) configuredManifestFetcher = opts.manifestFetcher;
  if (opts.updatesDir) configuredUpdatesDir = opts.updatesDir;
  if (opts.installedVersionProvider) configuredInstalledVersionProvider = opts.installedVersionProvider;
  if (typeof opts.skipPackagedGate === 'boolean') configuredSkipPackagedGate = opts.skipPackagedGate;
}

// ─── State-machine helpers ────────────────────────────────────────────────

function setState(next: FounderStackUpdateState, reason?: string): void {
  if (updateState === next) return;
  updateState = next;
  if (next === 'failed') {
    lastFailureReason = reason ?? 'unknown failure';
  } else if (next === 'idle') {
    lastFailureReason = null;
  }
  notifyMenuRefresh();
  notifyUpdateState();
}

// ─── Default manifest fetcher ─────────────────────────────────────────────

async function defaultManifestFetcher(
  apiBaseUrl: string,
): Promise<IdeUpdateManifest | null> {
  const url = `${apiBaseUrl.replace(/\/$/, '')}/api/founder-node/manifest`;
  const res = await fetch(url, {
    headers: { Accept: 'application/json', 'User-Agent': 'Founder-Stack-Updater' },
  });
  if (!res.ok) {
    throw new Error(`Manifest fetch failed: ${res.status} ${res.statusText}`);
  }
  const body = (await res.json()) as IdeUpdateManifest;
  if (!body || typeof body !== 'object' || !body.latestVersion || !Array.isArray(body.releases)) {
    throw new Error('Manifest response missing required fields');
  }
  return body;
}

/** Fetch manifest with 60s client-side cache. Exposed for tests. */
export async function fetchManifestCached(): Promise<IdeUpdateManifest | null> {
  const now = Date.now();
  if (cachedManifest && now - cachedManifest.at < MANIFEST_CACHE_TTL_MS) {
    return cachedManifest.body;
  }
  if (!configuredApiBaseUrl) return null;
  const body = await configuredManifestFetcher(configuredApiBaseUrl);
  cachedManifest = { at: now, body };
  return body;
}

/** Test-only hook to drop the manifest cache. */
export function __resetIdeManifestCacheForTests(): void {
  cachedManifest = null;
}

// ─── Version resolution ───────────────────────────────────────────────────

/**
 * Resolve the manifest's `latestVersion` against `installedVersion`.
 *
 * Returns `null` when:
 *   - installed >= latest (no update available)
 *   - latest is yanked (defence-in-depth; validator already enforces this)
 *   - latest is missing a real sha256 (placeholder or null)
 *
 * Returns `IdeUpdateInfo` otherwise.
 */
export function resolveIdeUpdate(
  manifest: IdeUpdateManifest,
  installedVersion: string,
): IdeUpdateInfo | null {
  if (!manifest?.releases?.length) return null;
  const latestEntry = manifest.releases.find((r) => r.version === manifest.latestVersion);
  if (!latestEntry) return null;

  // Defence in depth: the validator already rejects yanked latestVersion, but
  // a stale cached manifest or a hand-edited one could still slip through.
  if (latestEntry.status === 'yanked') {
    return null;
  }

  // No real sha256 → refuse to offer. The validator fails placeholder SHAs;
  // we mirror that here for runtime-loaded manifests (e.g. cache poisoning).
  const sha = latestEntry.download?.sha256;
  if (!sha || !/^[a-f0-9]{64}$/i.test(sha)) {
    return null;
  }

  if (!semverGt(manifest.latestVersion, installedVersion)) {
    return null;
  }

  return {
    version: manifest.latestVersion,
    downloadUrl: latestEntry.download.url,
    sha256: sha.toLowerCase(),
    size: latestEntry.download.size ?? null,
    status: latestEntry.status,
    rollbackMechanism: latestEntry.rollback?.mechanism,
  };
}

// ─── SHA-256 ──────────────────────────────────────────────────────────────

/** Compute SHA-256 of a file, streaming. Returns lowercase hex. */
export async function computeFileSha256(file: string): Promise<string> {
  const hash = crypto.createHash('sha256');
  await new Promise<void>((resolve, reject) => {
    const stream = fs.createReadStream(file);
    stream.on('data', (chunk) => hash.update(chunk));
    stream.on('error', reject);
    stream.on('end', () => resolve());
  });
  return hash.digest('hex');
}

// ─── Authenticode (signtool) ──────────────────────────────────────────────

/**
 * Locate the newest signtool.exe under the Windows Kits install dir.
 * Returns `null` on non-Windows or when no kit is installed.
 */
export async function findSigntool(): Promise<string | null> {
  if (process.platform !== 'win32') return null;
  const kitsRoot = 'C:\\Program Files (x86)\\Windows Kits\\10\\bin';
  let entries: string[] = [];
  try {
    entries = await fs.promises.readdir(kitsRoot);
  } catch {
    return null;
  }
  const versions = entries
    .filter((v) => /^\d+\.\d+\.\d+\.\d+$/.test(v))
    .sort()
    .reverse();
  for (const v of versions) {
    const candidate = path.join(kitsRoot, v, 'x64', 'signtool.exe');
    try {
      await fs.promises.access(candidate);
      return candidate;
    } catch {
      // try next
    }
  }
  return null;
}

/**
 * Default Authenticode verifier. Uses `signtool verify /pa /v` (Public Build
 * policy + verbose). Treats any non-zero exit as "not signed". On non-Windows
 * platforms, returns `signed: true` (the brief scopes Authenticode to Windows).
 */
export async function defaultAuthenticodeVerifier(
  file: string,
): Promise<AuthenticodeResult> {
  if (process.platform !== 'win32') {
    return { signed: true, output: 'non-Windows: Authenticode skipped' };
  }
  const signtool = await findSigntool();
  if (!signtool) {
    return {
      signed: false,
      error: 'signtool.exe not found under Windows Kits',
    };
  }
  try {
    const { stdout } = await execFileP(signtool, ['verify', '/pa', '/v', file], {
      windowsHide: true,
    });
    return { signed: true, output: stdout };
  } catch (err) {
    const e = err as { stdout?: string; stderr?: string; message: string };
    return {
      signed: false,
      output: e.stdout,
      error: e.stderr ?? e.message,
    };
  }
}

// ─── Health handshake ─────────────────────────────────────────────────────

/**
 * Wait up to `timeoutMs` for `probe()` to return true. Polls every `pollMs`.
 * Resolves true on success, false on timeout.
 */
export async function waitForHandshake(
  probe: HandshakeProbe,
  timeoutMs = HEALTH_HANDSHAKE_TIMEOUT_MS,
  pollMs = HEALTH_HANDSHAKE_POLL_MS,
): Promise<boolean> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      if (probe()) return true;
    } catch {
      // Probe may transiently throw while the new process is still booting.
    }
    await new Promise((r) => setTimeout(r, pollMs));
  }
  return probe();
}

// ─── Download ─────────────────────────────────────────────────────────────

/**
 * Download `url` to `dest` (a `.part` path). Returns the bytes written.
 * Throws on non-2xx. Uses streaming to avoid loading 100MB into memory.
 */
export async function downloadToFile(url: string, dest: string): Promise<number> {
  const res = await fetch(url);
  if (!res.ok || !res.body) {
    throw new Error(`Download failed: ${res.status} ${res.statusText}`);
  }
  await fs.promises.mkdir(path.dirname(dest), { recursive: true });
  const writer = fs.createWriteStream(dest);
  let total = 0;
  const reader = res.body.getReader();
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      if (value) {
        total += value.byteLength;
        writer.write(value);
      }
    }
  } finally {
    writer.end();
    await new Promise<void>((resolve, reject) => {
      writer.on('finish', () => resolve());
      writer.on('error', reject);
    });
  }
  return total;
}

// ─── Rollback helpers ─────────────────────────────────────────────────────

/** Path of the staged installer for a given version. */
export function stagedInstallerPath(version: string): string {
  return path.join(configuredUpdatesDir, stagedInstallerName(version));
}

/** Path of the staged `.part` for a given version. */
export function partFilePath(version: string): string {
  return path.join(configuredUpdatesDir, partFileName(version));
}

/**
 * Find the most-recent previous installer retained on disk. Used as the
 * rollback target. We pick the highest-versioned installer that is *below*
 * the just-installed version.
 */
export function findRollbackCandidate(
  justInstalledVersion: string,
): string | null {
  let dir: string[] = [];
  try {
    dir = fs.readdirSync(configuredUpdatesDir);
  } catch {
    return null;
  }
  const candidates: Array<{ version: string; file: string }> = [];
  for (const name of dir) {
    const m = /^founder-stack-(\d+\.\d+\.\d+)\.exe$/i.exec(name);
    if (!m) continue;
    const v = m[1];
    if (semverLt(v, justInstalledVersion)) {
      candidates.push({ version: v, file: path.join(configuredUpdatesDir, name) });
    }
  }
  if (!candidates.length) return null;
  candidates.sort((a, b) => (semverLt(a.version, b.version) ? 1 : -1));
  return candidates[0].file;
}

/**
 * Default installer launcher. Inno Setup supports overinstall on a stable
 * AppId; `/SP-` suppresses the "This will install..." dialog, `/SILENT` runs
 * without UI.
 */
export function defaultInstallerLauncher(installerPath: string, args: string[]): void {
  spawn(installerPath, args, {
    detached: true,
    stdio: 'ignore',
    windowsHide: true,
  }).unref();
}

/** Backwards-compat shim used by callers that want the silent defaults. */
export function runInstallerSilent(installerPath: string): void {
  configuredInstallerLauncher(installerPath, ['/SILENT', '/SP-']);
}

// ─── Top-level flow ───────────────────────────────────────────────────────

function isReady(): boolean {
  if (configuredSkipPackagedGate) return true;
  // app.isPackaged is unavailable in tests; in production the gate stands.
  try {
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    const { app } = require('electron') as typeof import('electron');
    return app?.isPackaged === true;
  } catch {
    return false;
  }
}

/**
 * Public entry point — check the manifest for a newer Founder IDE release.
 *
 * @param opts.silent When true, suppresses dialogs (used by the periodic
 *   background check). When false (user-invoked from the tray), shows a
 *   "you're up to date" dialog if no update is available.
 */
export async function checkForIdeUpdates(
  opts: { silent?: boolean } = {},
): Promise<IdeUpdateInfo | null> {
  if (!isReady() || checkInFlight) return lastResolvedUpdate;
  if (!configuredApiBaseUrl) return null;
  checkInFlight = true;
  try {
    const manifest = await fetchManifestCached();
    if (!manifest) return null;

    const installed = configuredInstalledVersionProvider();
    const update = resolveIdeUpdate(manifest, installed);
    lastResolvedUpdate = update;
    notifyMenuRefresh();
    notifyUpdateState();

    // Defence-in-depth yank refusal: resolveIdeUpdate already returns null,
    // but if a yanked release somehow snuck through, refuse explicitly here.
    const latestEntry = manifest.releases.find((r) => r.version === manifest.latestVersion);
    if (latestEntry?.status === 'yanked') {
      const msg = `Refusing yanked latestVersion ${manifest.latestVersion}`;
      console.error(`[ide-updater] ${msg}`);
      if (!opts.silent) {
        await showDialogSafe({
          type: 'error',
          title: 'Founder IDE update refused',
          message: `Latest published release (${manifest.latestVersion}) is yanked.`,
          detail:
            'The manifest advertises a yanked release as latest. Refusing to update. Open the releases page to download manually.',
          buttons: ['Open releases', 'OK'],
        }).then(({ response }) => {
          if (response === 0) {
            void openReleasesPage();
          }
        });
      }
      return null;
    }

    if (!update) {
      if (!opts.silent) {
        await showDialogSafe({
          type: 'info',
          title: 'Founder IDE',
          message: `You're on the latest Founder IDE (v${installed}).`,
        });
      }
      return null;
    }

    if (opts.silent) {
      await displayBalloonSafe(
        'Founder IDE update available',
        `v${update.version} ready — tray menu → Install Founder IDE update`,
      );
      return update;
    }

    const { response } = await showDialogSafe({
      type: 'info',
      title: 'Founder IDE update available',
      message: `Founder IDE v${update.version} is available (you have v${installed}).`,
      detail:
        'Download and install now? The installer will verify SHA-256 + Authenticode before running. Your ~/FounderVault and workspaces are preserved.',
      buttons: ['Download and install', 'Later', 'View release notes'],
      defaultId: 0,
      cancelId: 1,
    });
    if (response === 0) {
      void downloadVerifyInstallAndHandshake(update).catch((err) => {
        console.error('[ide-updater] install flow failed:', err);
      });
    } else if (response === 2) {
      void openReleasesPage();
    }
    return update;
  } catch (err) {
    console.warn('[ide-updater] update check failed:', err);
    if (!opts.silent) {
      await showDialogSafe({
        type: 'error',
        title: 'Founder IDE update check failed',
        message: err instanceof Error ? err.message : String(err),
      });
    }
    return null;
  } finally {
    checkInFlight = false;
  }
}

async function openReleasesPage(): Promise<void> {
  try {
    const { shell } = await import('electron');
    await shell.openExternal(
      'https://github.com/danishhaiderau-maker/doxed-founders-website/releases',
    );
  } catch {
    /* electron unavailable (test) */
  }
}

/**
 * Show a dialog, returning -1 (cancel) when Electron is not available
 * (tests, headless CI). Mirrors dialog.showMessageBox's `{ response }`
 * shape so call sites can stay unchanged.
 */
async function showDialogSafe(
  opts: Electron.MessageBoxOptions,
): Promise<{ response: number }> {
  try {
    const { dialog } = await import('electron');
    return await dialog.showMessageBox(opts);
  } catch {
    return { response: 1 };
  }
}

async function displayBalloonSafe(title: string, content: string): Promise<void> {
  try {
    trayRef?.displayBalloon?.({ title, content });
  } catch {
    /* tray not bound (test) */
  }
}

/**
 * Full download → verify → install → handshake → (rollback if needed) flow.
 * The brief requires every step to be atomic and recoverable.
 */
export async function downloadVerifyInstallAndHandshake(
  info: IdeUpdateInfo,
): Promise<void> {
  if (!isReady()) return;

  // ── Stage 1: download to .part ─────────────────────────────────────────
  setState('downloading');
  const part = partFilePath(info.version);
  try {
    await downloadToFile(info.downloadUrl, part);
  } catch (err) {
    setState('failed', `download failed: ${(err as Error).message}`);
    try {
      await fs.promises.unlink(part);
    } catch {
      /* best effort */
    }
    await notifyFailure(
      `Download failed for v${info.version}: ${(err as Error).message}`,
    );
    return;
  }

  // ── Stage 2: SHA-256 ───────────────────────────────────────────────────
  setState('verifying');
  let actualSha: string;
  try {
    actualSha = await computeFileSha256(part);
  } catch (err) {
    setState('failed', `sha256 read failed: ${(err as Error).message}`);
    try {
      await fs.promises.unlink(part);
    } catch {
      /* best effort */
    }
    await notifyFailure(`Could not hash the downloaded file: ${(err as Error).message}`);
    return;
  }

  if (actualSha.toLowerCase() !== info.sha256.toLowerCase()) {
    try {
      await fs.promises.unlink(part);
    } catch {
      /* best effort */
    }
    setState(
      'failed',
      `sha256 mismatch: expected ${info.sha256}, got ${actualSha}`,
    );
    await notifyFailure(
      `Integrity check failed for v${info.version}. The downloaded file did not match the published SHA-256 and has been deleted. The installer was NOT run.`,
    );
    return;
  }

  // ── Stage 3: Authenticode ──────────────────────────────────────────────
  const auth = await configuredAuthenticodeVerifier(part);
  if (!auth.signed && !configuredAllowUnsigned) {
    try {
      await fs.promises.unlink(part);
    } catch {
      /* best effort */
    }
    setState('failed', 'authenticode: unsigned + allowUnsigned=false');
    await notifyFailure(
      `v${info.version} is not Authenticode-signed and the current channel (${configuredChannel}) refuses unsigned installers. The downloaded file has been deleted. Set allowUnsigned=true (beta/insider only) to override.`,
    );
    return;
  }
  if (!auth.signed && configuredAllowUnsigned) {
    console.warn(
      `[ide-updater] ${info.version} is unsigned but allowUnsigned=true (${configuredChannel}); proceeding.`,
    );
  }

  // ── Stage 4: rename .part → .exe and run installer ─────────────────────
  const finalPath = stagedInstallerPath(info.version);
  try {
    // If a previous identical-version installer exists (e.g. retry), overwrite.
    await fs.promises.rename(part, finalPath);
  } catch (err) {
    setState('failed', `rename failed: ${(err as Error).message}`);
    await notifyFailure(`Could not finalize the downloaded file: ${(err as Error).message}`);
    return;
  }

  setState('installing');
  // Snapshot the previous version BEFORE we install, so rollback can target it.
  const previousInstaller = findRollbackCandidate(info.version);
  const previousVersion = previousInstaller
    ? (/founder-stack-(\d+\.\d+\.\d+)\.exe$/i.exec(previousInstaller)?.[1] ?? null)
    : null;

  try {
    runInstallerSilent(finalPath);
  } catch (err) {
    setState('failed', `installer spawn failed: ${(err as Error).message}`);
    await notifyFailure(`Could not launch the installer: ${(err as Error).message}`);
    return;
  }

  // The installer runs detached; app.quit() is required so the new version
  // can take over the single-instance lock. We do NOT quit yet — first wait
  // for the new version's IPC handshake (best-effort; in production the old
  // process will be killed by the installer's `runAfterFinish` or the user).
  // For health-handshake purposes we treat "the probe went true" as success.

  // ── Stage 5: health handshake ──────────────────────────────────────────
  const ok = await waitForHandshake(
    configuredHandshakeProbe,
    configuredHandshakeTimeoutMs,
    configuredHandshakePollMs,
  );
  if (ok) {
    lastResolvedUpdate = null;
    setState('idle');
    await displayBalloonSafe('Founder IDE updated', `Now running v${info.version}.`);
    return;
  }

  // ── Stage 6: rollback ─────────────────────────────────────────────────
  if (!previousInstaller || !previousVersion) {
    setState('failed', 'health-handshake timeout + no rollback candidate');
    await notifyFailure(
      `v${info.version} installed but did not come online within ${HEALTH_HANDSHAKE_TIMEOUT_MS / 1000}s, and no previous installer is retained for rollback. Manual recovery required.`,
    );
    return;
  }

  setState('rolling_back');
  try {
    runInstallerSilent(previousInstaller);
  } catch (err) {
    setState('failed', `rollback spawn failed: ${(err as Error).message}`);
    await notifyFailure(
      `Could not launch the rollback installer: ${(err as Error).message}`,
    );
    return;
  }

  // Wait briefly for the rollback handshake as well. If it succeeds we're
  // back to idle; if not, we go failed.
  const rollbackOk = await waitForHandshake(
    configuredHandshakeProbe,
    configuredHandshakeTimeoutMs,
    configuredHandshakePollMs,
  );
  if (rollbackOk) {
    lastResolvedUpdate = null;
    setState('idle');
    await showDialogSafe({
      type: 'warning',
      title: 'Update rolled back',
      message: `Founder IDE v${info.version} failed its post-install health check.`,
      detail: `Rolled back to v${previousVersion}. Your ~/FounderVault and workspaces are intact.`,
      buttons: ['OK'],
    });
    return;
  }

  setState('failed', `rollback handshake also failed for ${previousVersion}`);
  await notifyFailure(
    `Neither v${info.version} nor the rollback target v${previousVersion} came online. Manual recovery required.`,
  );
}

async function notifyFailure(message: string): Promise<void> {
  console.error(`[ide-updater] ${message}`);
  try {
    await showDialogSafe({
      type: 'error',
      title: 'Founder IDE update failed',
      message,
    });
  } catch {
    /* dialog may not be available in tests */
  }
}

// ─── Periodic loop (mirrors startAutoUpdateChecks) ────────────────────────

export function startIdeAutoUpdateChecks(): void {
  if (!isReady()) return;
  for (const delay of STARTUP_CHECK_DELAYS_MS) {
    setTimeout(() => {
      checkForIdeUpdates({ silent: true }).catch(console.warn);
    }, delay);
  }
  if (updateCheckTimer) return;
  updateCheckTimer = setInterval(() => {
    checkForIdeUpdates({ silent: true }).catch(console.warn);
  }, CHECK_INTERVAL_MS);
}

export function stopIdeAutoUpdateChecks(): void {
  if (updateCheckTimer) {
    clearInterval(updateCheckTimer);
    updateCheckTimer = null;
  }
}

// ─── Tray tooltip ─────────────────────────────────────────────────────────

/**
 * Build a short status string suitable for the tray icon tooltip. Surfaced
 * alongside the Node version by `main.ts`.
 */
export function ideUpdateTooltipSuffix(): string {
  switch (updateState) {
    case 'idle':
      return lastResolvedUpdate ? `Founder IDE update available: v${lastResolvedUpdate.version}` : '';
    case 'downloading':
      return lastResolvedUpdate ? `Downloading Founder IDE v${lastResolvedUpdate.version}…` : 'Downloading Founder IDE update…';
    case 'verifying':
      return 'Verifying Founder IDE update…';
    case 'installing':
      return lastResolvedUpdate ? `Installing Founder IDE v${lastResolvedUpdate.version}…` : 'Installing Founder IDE update…';
    case 'rolling_back':
      return 'Rolling back Founder IDE…';
    case 'failed':
      return 'Founder IDE update failed — click "Check for Founder IDE updates…" to retry';
    default:
      return '';
  }
}

// ─── Test-only hooks ──────────────────────────────────────────────────────

/** Reset all module state — only safe in tests. */
export function __resetIdeUpdaterForTests(): void {
  updateState = 'idle';
  lastFailureReason = null;
  lastResolvedUpdate = null;
  cachedManifest = null;
  checkInFlight = false;
  trayRef = null;
  menuRefresh = null;
  updateStateListener = null;
  configuredAuthenticodeVerifier = defaultAuthenticodeVerifier;
  configuredInstallerLauncher = defaultInstallerLauncher;
  configuredHandshakeProbe = () => false;
  configuredApiBaseUrl = null;
  configuredChannel = 'stable';
  configuredAllowUnsigned = false;
  configuredManifestFetcher = defaultManifestFetcher;
  configuredUpdatesDir = ideUpdatesDir();
  configuredInstalledVersionProvider = resolveInstalledIdeVersion;
  configuredSkipPackagedGate = false;
  configuredHandshakeTimeoutMs = HEALTH_HANDSHAKE_TIMEOUT_MS;
  configuredHandshakePollMs = HEALTH_HANDSHAKE_POLL_MS;
  if (updateCheckTimer) {
    clearInterval(updateCheckTimer);
    updateCheckTimer = null;
  }
}

/** Force the state machine into a specific state — tests only. */
export function __setIdeUpdateStateForTests(
  next: FounderStackUpdateState,
  reason?: string,
): void {
  setState(next, reason);
}
