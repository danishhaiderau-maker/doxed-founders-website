/**
 * Connect-IDE — writes Founder OS AI proxy credentials into IDE configs.
 *
 * One click in Founder Node's UI calls the `connect-cursor` /
 * `connect-founder-ide` IPC handlers, which write Founder OS's proxy URL +
 * bearer token into the target IDE's User-level `settings.json`. After a
 * reload, every chat / agent request goes through Founder OS.
 *
 * Also supports Claude Code, Continue.dev, and OpenHands via shell-profile
 * env vars (`OPENAI_BASE_URL` + `OPENAI_API_KEY`).
 *
 * Storage layout (Windows):
 *   Cursor:      %APPDATA%/Cursor/User/settings.json
 *   Founder IDE: %LOCALAPPDATA%/FounderIDE/User/settings.json
 *                (also .founder-ide under the install / home)
 *
 * macOS:
 *   Cursor:      ~/Library/Application Support/Cursor/User/settings.json
 *   Founder IDE: ~/Library/Application Support/FounderIDE/User/settings.json
 *
 * Linux:
 *   Cursor:      ~/.config/Cursor/User/settings.json
 *   Founder IDE: ~/.config/FounderIDE/User/settings.json
 */
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import type { FounderNodeConfig } from '@dcf/founder-vault';

export type ConnectResult =
  | { ok: true; target: string; backupPath: string | null; reloaded: boolean }
  | { ok: false; error: string; target?: string };

const FOUNDER_OS_MODEL = 'founder-os-auto';

/** Public proxy path on the API host. Global prefix is `/api` so the OpenAI
 *  endpoint is `/api/v1`. Founders paste this whole URL into the IDE. */
export function proxyBaseUrl(apiBaseUrl: string): string {
  return `${apiBaseUrl.replace(/\/$/, '')}/api/v1`;
}

export function bearerFromConfig(config: FounderNodeConfig): string {
  return `fos_${config.nodeId}:${config.nodeToken}`;
}

function getCursorUserDir(): string | null {
  const home = os.homedir();
  const platform = process.platform;
  if (platform === 'win32') {
    const appdata = process.env.APPDATA || path.join(home, 'AppData', 'Roaming');
    return path.join(appdata, 'Cursor', 'User');
  }
  if (platform === 'darwin') {
    return path.join(home, 'Library', 'Application Support', 'Cursor', 'User');
  }
  // linux
  const xdg = process.env.XDG_CONFIG_HOME || path.join(home, '.config');
  return path.join(xdg, 'Cursor', 'User');
}

/**
 * Candidate Founder IDE User directories, in preference order.
 * Windows Void/FounderIDE forks commonly use LOCALAPPDATA; we also probe
 * APPDATA and a home `.founder-ide` folder for portable installs.
 */
export function getFounderIdeUserDirs(): string[] {
  const home = os.homedir();
  const dirs: string[] = [];
  const platform = process.platform;

  if (platform === 'win32') {
    const local = process.env.LOCALAPPDATA || path.join(home, 'AppData', 'Local');
    const roaming = process.env.APPDATA || path.join(home, 'AppData', 'Roaming');
    dirs.push(
      path.join(local, 'FounderIDE', 'User'),
      path.join(roaming, 'FounderIDE', 'User'),
      path.join(local, 'Founder IDE', 'User'),
      path.join(home, '.founder-ide', 'User'),
    );
  } else if (platform === 'darwin') {
    dirs.push(
      path.join(home, 'Library', 'Application Support', 'FounderIDE', 'User'),
      path.join(home, 'Library', 'Application Support', 'Founder IDE', 'User'),
      path.join(home, '.founder-ide', 'User'),
    );
  } else {
    const xdg = process.env.XDG_CONFIG_HOME || path.join(home, '.config');
    dirs.push(
      path.join(xdg, 'FounderIDE', 'User'),
      path.join(xdg, 'Founder IDE', 'User'),
      path.join(home, '.founder-ide', 'User'),
    );
  }
  return dirs;
}

/** Prefer an existing Founder IDE User dir; otherwise create the primary path. */
export function resolveFounderIdeUserDir(): string {
  const candidates = getFounderIdeUserDirs();
  for (const dir of candidates) {
    if (fs.existsSync(dir) || fs.existsSync(path.dirname(dir))) {
      return dir;
    }
  }
  return candidates[0];
}

function safeReadJson<T>(file: string): T | null {
  try {
    if (!fs.existsSync(file)) return null;
    const raw = fs.readFileSync(file, 'utf8');
    return JSON.parse(raw) as T;
  } catch {
    return null;
  }
}

function safeWriteJsonWithBackup(file: string, data: unknown): string | null {
  try {
    const dir = path.dirname(file);
    fs.mkdirSync(dir, { recursive: true });
    let backupPath: string | null = null;
    if (fs.existsSync(file)) {
      backupPath = `${file}.founder-os-backup-${Date.now()}.json`;
      fs.copyFileSync(file, backupPath);
    }
    fs.writeFileSync(file, JSON.stringify(data, null, 2), 'utf8');
    return backupPath;
  } catch (err) {
    throw new Error(
      `Could not write ${file}: ${err instanceof Error ? err.message : String(err)}`,
    );
  }
}

function mergeFounderOsSettings(
  existing: Record<string, unknown>,
  config: FounderNodeConfig,
  extras: Record<string, unknown> = {},
): Record<string, unknown> {
  const proxyBase = proxyBaseUrl(config.apiBaseUrl);
  const bearer = bearerFromConfig(config);
  return {
    ...existing,
    'openai.apiBase': proxyBase,
    'openai.apiKey': bearer,
    'founder-os.connected': true,
    'founder-os.nodeId': config.nodeId,
    // Extension settings — auto-load so pairing isn't manual every time.
    'founderOs.apiBaseUrl': config.apiBaseUrl.replace(/\/$/, ''),
    'founderOs.nodeId': config.nodeId,
    'founderOs.nodeToken': config.nodeToken,
    ...extras,
  };
}

/** Writes the Founder OS proxy URL + bearer into Cursor's settings.json. */
export function connectCursor(config: FounderNodeConfig): ConnectResult {
  const userDir = getCursorUserDir();
  if (!userDir) {
    return { ok: false, error: `Unsupported platform: ${process.platform}` };
  }
  const settingsFile = path.join(userDir, 'settings.json');
  type CursorSettings = Record<string, unknown>;
  const existing = safeReadJson<CursorSettings>(settingsFile) ?? {};

  const next = mergeFounderOsSettings(existing, config, {
    'cursor.generalModel': FOUNDER_OS_MODEL,
    'cursor.largeModel': FOUNDER_OS_MODEL,
    'cursor.smallModel': FOUNDER_OS_MODEL,
  });

  try {
    const backupPath = safeWriteJsonWithBackup(settingsFile, next);
    return {
      ok: true,
      target: settingsFile,
      backupPath,
      reloaded: false,
    };
  } catch (err) {
    return {
      ok: false,
      error: err instanceof Error ? err.message : String(err),
      target: settingsFile,
    };
  }
}

/**
 * Writes Founder OS credentials into Founder IDE User settings.json and
 * mirrors apiBaseUrl / nodeId / nodeToken into `founderOs.*` so the
 * founder-ide-extension picks them up without reading the vault every time.
 */
export function connectFounderIde(config: FounderNodeConfig): ConnectResult {
  const userDir = resolveFounderIdeUserDir();
  const settingsFile = path.join(userDir, 'settings.json');
  const existing = safeReadJson<Record<string, unknown>>(settingsFile) ?? {};
  const next = mergeFounderOsSettings(existing, config);

  try {
    const backupPath = safeWriteJsonWithBackup(settingsFile, next);
    return {
      ok: true,
      target: settingsFile,
      backupPath,
      reloaded: false,
    };
  } catch (err) {
    return {
      ok: false,
      error: err instanceof Error ? err.message : String(err),
      target: settingsFile,
    };
  }
}

/**
 * Connect both Founder IDE (preferred) and Cursor. Returns the Founder IDE
 * result as primary; Cursor failures are non-fatal when Founder IDE succeeds.
 */
export function connectAllIdes(config: FounderNodeConfig): ConnectResult & {
  cursor?: ConnectResult;
  founderIde?: ConnectResult;
} {
  const founderIde = connectFounderIde(config);
  const cursor = connectCursor(config);
  if (founderIde.ok) {
    return { ...founderIde, founderIde, cursor };
  }
  if (cursor.ok) {
    return { ...cursor, founderIde, cursor };
  }
  return {
    ok: false,
    error: `Founder IDE: ${founderIde.error ?? 'failed'}; Cursor: ${cursor.error ?? 'failed'}`,
    founderIde,
    cursor,
  };
}

/** Writes OPENAI_BASE_URL + OPENAI_API_KEY into the founder's shell profile
 *  so Claude Code, Continue.dev, OpenHands, Aider, and any other OpenAI-
 *  compat tool picks up Founder OS automatically. */
export function connectShellEnv(config: FounderNodeConfig): ConnectResult {
  const home = os.homedir();
  const shellFiles = [
    path.join(home, '.bashrc'),
    path.join(home, '.zshrc'),
    path.join(home, '.profile'),
  ];
  if (process.platform === 'win32') {
    // On Windows we also write a PowerShell profile entry — best-effort.
    const docs = process.env.USERPROFILE || home;
    shellFiles.push(path.join(documentsDirWindows() ?? docs, 'WindowsPowerShell', 'Microsoft.PowerShell_profile.ps1'));
  }

  const proxyBase = proxyBaseUrl(config.apiBaseUrl);
  const beginMarker = '# >>> Founder OS AI proxy >>>';
  const endMarker = '# <<< Founder OS AI proxy <<<';
  const block = [
    beginMarker,
    `export OPENAI_BASE_URL="${proxyBase}"`,
    `export OPENAI_API_KEY="fos_${config.nodeId}:${config.nodeToken}"`,
    `export FOUNDER_OS_NODE_ID="${config.nodeId}"`,
    endMarker,
  ].join('\n');

  let lastBackup: string | null = null;
  let lastFile: string | undefined;

  for (const file of shellFiles) {
    try {
      const dir = path.dirname(file);
      fs.mkdirSync(dir, { recursive: true });
      let existing = '';
      if (fs.existsSync(file)) {
        existing = fs.readFileSync(file, 'utf8');
      }
      // Replace existing Founder OS block if present, else append.
      const nextContents = existing.includes(beginMarker)
        ? existing.replace(
            new RegExp(`${beginMarker}[\\s\\S]*?${endMarker}`, 'm'),
            block,
          )
        : `${existing}\n${block}\n`;

      const backupPath = `${file}.founder-os-backup-${Date.now()}`;
      if (fs.existsSync(file)) {
        fs.copyFileSync(file, backupPath);
        lastBackup = backupPath;
      }
      fs.writeFileSync(file, nextContents, 'utf8');
      lastFile = file;
    } catch {
      // Continue to the next shell file — at least one usually succeeds.
    }
  }

  if (!lastFile) {
    return { ok: false, error: 'Could not write any shell profile.' };
  }

  return {
    ok: true,
    target: lastFile,
    backupPath: lastBackup,
    reloaded: false,
  };
}

function stripFounderOsKeys(existing: Record<string, unknown>): Record<string, unknown> {
  const next: Record<string, unknown> = { ...existing };
  delete next['openai.apiBase'];
  delete next['openai.apiKey'];
  delete next['cursor.generalModel'];
  delete next['cursor.largeModel'];
  delete next['cursor.smallModel'];
  delete next['founder-os.connected'];
  delete next['founder-os.nodeId'];
  delete next['founderOs.apiBaseUrl'];
  delete next['founderOs.nodeId'];
  delete next['founderOs.nodeToken'];
  return next;
}

/** Removes Founder OS keys from Cursor's settings.json — undoes connectCursor. */
export function disconnectCursor(config: FounderNodeConfig): ConnectResult {
  const userDir = getCursorUserDir();
  if (!userDir) return { ok: false, error: `Unsupported platform: ${process.platform}` };

  const settingsFile = path.join(userDir, 'settings.json');
  const existing = safeReadJson<Record<string, unknown>>(settingsFile);
  if (!existing) return { ok: true, target: settingsFile, backupPath: null, reloaded: false };

  void config;
  try {
    const backupPath = safeWriteJsonWithBackup(settingsFile, stripFounderOsKeys(existing));
    return { ok: true, target: settingsFile, backupPath, reloaded: false };
  } catch (err) {
    return { ok: false, error: err instanceof Error ? err.message : String(err), target: settingsFile };
  }
}

/** Removes Founder OS keys from Founder IDE settings.json. */
export function disconnectFounderIde(config: FounderNodeConfig): ConnectResult {
  const userDir = resolveFounderIdeUserDir();
  const settingsFile = path.join(userDir, 'settings.json');
  const existing = safeReadJson<Record<string, unknown>>(settingsFile);
  if (!existing) return { ok: true, target: settingsFile, backupPath: null, reloaded: false };

  void config;
  try {
    const backupPath = safeWriteJsonWithBackup(settingsFile, stripFounderOsKeys(existing));
    return { ok: true, target: settingsFile, backupPath, reloaded: false };
  } catch (err) {
    return { ok: false, error: err instanceof Error ? err.message : String(err), target: settingsFile };
  }
}

function documentsDirWindows(): string | null {
  try {
    // USERPROFILE usually points at C:\Users\<name>; Documents is one level down.
    const profile = process.env.USERPROFILE;
    if (!profile) return null;
    const docs = path.join(profile, 'Documents');
    return fs.existsSync(docs) ? docs : null;
  } catch {
    return null;
  }
}
