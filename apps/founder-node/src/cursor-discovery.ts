import * as fs from 'node:fs';
import * as path from 'node:path';
import * as os from 'node:os';
import { spawnSync } from 'node:child_process';
import type {
  BridgeAgent,
  BridgeGitState,
  BridgeSession,
  BridgeWorkspace,
} from '@dcf/utils';

/**
 * Phase A — Desktop Runtime.
 *
 * Cursor stores workspace/session metadata on disk. This module scans that
 * storage to surface real workspaces to Founder OS via the IDE bridge.
 *
 * Storage layout (Windows, %APPDATA%/Cursor/User):
 *   workspaceStorage/<uuid>/workspace.json  -> { folder: "file:///path" }
 *   workspaceStorage/<uuid>/                -> mtime = last activity
 *   globalStorage/state.vscdb               -> SQLite (chat history) — not read here
 *
 * We deliberately avoid shelling out to `git` so the Electron app stays
 * self-contained and works on machines without a git CLI on PATH.
 */

const MAX_WORKSPACES = 10;
const MAX_RECENT_COMMITS = 5;
const MAX_MODIFIED_FILES = 50;

type WorkspaceJson = { folder?: string; configuration?: { folderPath?: string } };

function getCursorWorkspaceStoragePath(): string | null {
  const appdata =
    process.env.APPDATA || path.join(os.homedir(), 'AppData', 'Roaming');
  if (!appdata) return null;
  return path.join(appdata, 'Cursor', 'User', 'workspaceStorage');
}

function getCursorGlobalStoragePath(): string | null {
  const appdata =
    process.env.APPDATA || path.join(os.homedir(), 'AppData', 'Roaming');
  if (!appdata) return null;
  return path.join(appdata, 'Cursor', 'User', 'globalStorage');
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

function fileUriToPath(uri: string): string | null {
  try {
    if (!uri || typeof uri !== 'string') return null;
    if (uri.startsWith('file://')) {
      const decoded = decodeURIComponent(uri.slice('file://'.length));
      return decoded.replace(/^\/([a-zA-Z]:)/, '$1');
    }
    if (fs.existsSync(uri)) return uri;
    return null;
  } catch {
    return null;
  }
}

function folderNameFromPath(p: string): string {
  const norm = p.replace(/\\/g, '/').replace(/\/$/, '');
  const seg = norm.split('/').filter(Boolean).pop();
  return seg || p;
}

function readGitRemote(workspacePath: string): string | undefined {
  try {
    const configPath = path.join(workspacePath, '.git', 'config');
    if (!fs.existsSync(configPath)) return undefined;
    const raw = fs.readFileSync(configPath, 'utf8');
    const match = raw.match(/url\s*=\s*([^\s\r\n]+)/);
    return match?.[1];
  } catch {
    return undefined;
  }
}

/**
 * Get git state for a workspace path by reading the `.git` directory directly.
 * No `git` subprocess — keeps the Electron app self-contained.
 */
export function getGitStateForWorkspace(
  workspacePath: string,
): BridgeGitState | null {
  try {
    const gitDir = path.join(workspacePath, '.git');
    if (!fs.existsSync(gitDir)) return null;

    const isGitDirFile = fs.statSync(gitDir).isFile();
    let realGitDir = gitDir;
    let worktreeRoot = workspacePath;

    if (isGitDirFile) {
      const raw = fs.readFileSync(gitDir, 'utf8').trim();
      const m = raw.match(/^gitdir:\s*(.+)$/);
      if (!m) return null;
      realGitDir = m[1]!;
      const parent = path.dirname(realGitDir);
      if (path.basename(parent) === 'worktrees') {
        const dotGit = path.dirname(parent);
        worktreeRoot = path.dirname(dotGit);
      }
    }

    const headPath = path.join(realGitDir, 'HEAD');
    if (!fs.existsSync(headPath)) return null;
    const head = fs.readFileSync(headPath, 'utf8').trim();

    let branch = 'detached';
    if (head.startsWith('ref:')) {
      const ref = head.replace(/^ref:\s*/, '');
      branch = ref.split('/').pop() ?? 'detached';
    } else {
      branch = head.slice(0, 8);
    }

    const recentCommits: BridgeGitState['recentCommits'] = [];
    const logsHead = path.join(realGitDir, 'logs', 'HEAD');
    if (fs.existsSync(logsHead)) {
      const lines = fs.readFileSync(logsHead, 'utf8').split(/\r?\n/).filter(Boolean);
      const last = lines.slice(-MAX_RECENT_COMMITS).reverse();
      for (const line of last) {
        const m = line.match(/^([0-9a-f]+)\s+([0-9a-f]+)\s+([^\t<]*?)\s*<[^>]*>\s+(\d+).*?\t(.*)$/);
        if (m) {
          const tsSec = parseInt(m[4]!, 10);
          recentCommits.push({
            hash: m[1]!.slice(0, 8),
            message: (m[5] ?? '').slice(0, 160),
            author: (m[3] ?? '').trim().slice(0, 80) || 'unknown',
            timestamp: new Date(tsSec * 1000).toISOString(),
          });
        }
      }
    }

    const modifiedFiles = detectModifiedFiles(worktreeRoot, realGitDir);

    return {
      branch,
      clean: modifiedFiles.length === 0,
      ahead: 0,
      behind: 0,
      modifiedFiles: modifiedFiles.slice(0, MAX_MODIFIED_FILES),
      recentCommits,
    };
  } catch {
    return null;
  }
}

function detectModifiedFiles(workspacePath: string, gitDir: string): string[] {
  try {
    const indexPath = path.join(gitDir, 'index');
    const headPath = path.join(gitDir, 'HEAD');
    if (!fs.existsSync(indexPath) || !fs.existsSync(headPath)) return [];

    const indexMtime = fs.statSync(indexPath).mtimeMs;
    const logsHead = path.join(gitDir, 'logs', 'HEAD');
    const lastCommitMs = fs.existsSync(logsHead)
      ? fs.statSync(logsHead).mtimeMs
      : fs.statSync(headPath).mtimeMs;

    if (indexMtime <= lastCommitMs) return [];

    const candidates: Array<{ file: string; mtime: number }> = [];
    const entries = fs.readdirSync(workspacePath, { withFileTypes: true });
    for (const entry of entries) {
      if (entry.name.startsWith('.') || entry.name === 'node_modules') continue;
      if (!entry.isFile()) continue;
      const full = path.join(workspacePath, entry.name);
      try {
        const st = fs.statSync(full);
        if (st.mtimeMs > lastCommitMs) {
          candidates.push({ file: entry.name, mtime: st.mtimeMs });
        }
      } catch {
        /* ignore */
      }
    }
    candidates.sort((a, b) => b.mtime - a.mtime);
    return candidates.slice(0, 12).map((c) => c.file);
  } catch {
    return [];
  }
}

/**
 * Discover Cursor workspaces from the local machine.
 * Scans Cursor's workspaceStorage directory for recent workspaces.
 */
export function discoverCursorWorkspaces(): BridgeWorkspace[] {
  const storage = getCursorWorkspaceStoragePath();
  if (!storage || !fs.existsSync(storage)) return [];

  let entries: fs.Dirent[];
  try {
    entries = fs.readdirSync(storage, { withFileTypes: true });
  } catch {
    return [];
  }

  const discovered: Array<{
    id: string;
    title: string;
    repository: string | undefined;
    branch: string | undefined;
    folderPath: string | null;
    lastActiveAt: string;
  }> = [];

  const cursorRunning = isCursorRunning();

  for (const entry of entries) {
    if (!entry.isDirectory()) continue;
    const wsDir = path.join(storage, entry.name);
    const wsJsonPath = path.join(wsDir, 'workspace.json');
    const wsJson = safeReadJson<WorkspaceJson>(wsJsonPath);
    if (!wsJson) continue;

    const folderUri = wsJson.folder ?? wsJson.configuration?.folderPath;
    if (!folderUri) continue;

    const folderPath = fileUriToPath(folderUri);
    if (!folderPath || !fs.existsSync(folderPath)) continue;

    let statMtime: Date;
    try {
      statMtime = fs.statSync(wsDir).mtime;
    } catch {
      continue;
    }

    const title = folderNameFromPath(folderPath);
    const gitState = getGitStateForWorkspace(folderPath);
    const repository = readGitRemote(folderPath) ?? folderPath;

    discovered.push({
      id: `cursor:${entry.name}`,
      title,
      repository,
      branch: gitState?.branch,
      folderPath,
      lastActiveAt: statMtime.toISOString(),
    });
  }

  discovered.sort(
    (a, b) => new Date(b.lastActiveAt).getTime() - new Date(a.lastActiveAt).getTime(),
  );

  return discovered.slice(0, MAX_WORKSPACES).map((w) => ({
    id: w.id,
    title: w.title,
    repository: w.repository,
    branch: w.branch,
    ideProvider: 'cursor',
    lastActiveAt: w.lastActiveAt,
    hasActiveAgent: cursorRunning,
    messageCount: undefined,
  }));
}

/**
 * Discover Cursor recent sessions/conversations.
 *
 * Cursor stores chat history in SQLite (`state.vscdb`) which we can't read
 * without adding a dependency. For now we derive sessions from workspace
 * activity and mark them `restorable: false`.
 */
export function discoverCursorSessions(): BridgeSession[] {
  const workspaces = discoverCursorWorkspaces();
  const globalStorage = getCursorGlobalStoragePath();
  let globalMtime: Date | null = null;
  if (globalStorage && fs.existsSync(globalStorage)) {
    try {
      globalMtime = fs.statSync(globalStorage).mtime;
    } catch {
      globalMtime = null;
    }
  }

  return workspaces.map((w) => ({
    id: `session:${w.id}`,
    workspaceId: w.id,
    title: w.title,
    messages: undefined,
    restorable: false,
    lastActiveAt: globalMtime
      ? new Date(
          Math.max(new Date(w.lastActiveAt).getTime(), globalMtime.getTime()),
        ).toISOString()
      : w.lastActiveAt,
  }));
}

/**
 * Best-effort detection of whether Cursor is running on this machine.
 */
export function isCursorRunning(): boolean {
  try {
    if (process.platform === 'win32') {
      const res = spawnSync(
        'tasklist',
        ['/FI', 'IMAGENAME eq Cursor.exe', '/FO', 'CSV', '/NH'],
        { encoding: 'utf8', windowsHide: true, timeout: 4_000 },
      );
      if (res.error || res.status !== 0) return false;
      return /Cursor\.exe/i.test(res.stdout || '');
    }
    if (process.platform === 'darwin') {
      const res = spawnSync('pgrep', ['-x', 'Cursor'], {
        encoding: 'utf8',
        timeout: 4_000,
      });
      return res.status === 0 && Boolean((res.stdout || '').trim());
    }
    const res = spawnSync('pgrep', ['-f', '[C]ursor'], {
      encoding: 'utf8',
      timeout: 4_000,
    });
    return res.status === 0 && Boolean((res.stdout || '').trim());
  } catch {
    return false;
  }
}

/**
 * Report active Cursor agents. Best-effort — we can't yet enumerate individual
 * agent runs without the Cursor SDK, so we surface one aggregate agent whose
 * status reflects whether the Cursor process is alive.
 */
export function discoverCursorAgents(): BridgeAgent[] {
  const running = isCursorRunning();
  return [
    {
      id: 'cursor-local',
      label: 'Cursor',
      status: running ? 'running' : 'idle',
      task: undefined,
      startedAt: undefined,
    },
  ];
}
