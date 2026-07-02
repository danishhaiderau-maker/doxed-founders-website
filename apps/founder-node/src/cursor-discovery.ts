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
 * Cursor stores chat history in a SQLite database at
 * `%APPDATA%/Cursor/User/globalStorage/state.vscdb`. Node 22+ ships a built-in
 * `node:sqlite` module (`DatabaseSync`) that can read it without any native
 * dependency — important for Electron packaging (no `better-sqlite3` rebuild).
 *
 * We read two keys from the `ItemTable`:
 *   - `composer.composerHeaders`  → { allComposers: ComposerHead[] }
 *   - `glass.localAgentProjects.v1` → AgentProject[]
 *
 * If anything fails (module unavailable in this Electron's Node version, DB
 * locked, schema changed, …) we fall back to the legacy workspace-derived
 * sessions so the dashboard still shows something.
 */
export function discoverCursorSessions(): BridgeSession[] {
  const sqliteSessions = readCursorComposerSessionsFromSqlite();
  if (sqliteSessions && sqliteSessions.length > 0) return sqliteSessions;
  return discoverCursorSessionsFromWorkspaces();
}

/**
 * Legacy fallback — derive placeholder sessions from workspace activity.
 * Marked `restorable: false` because we have no real conversation handle.
 */
function discoverCursorSessionsFromWorkspaces(): BridgeSession[] {
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
    repository: w.repository,
    branch: w.branch,
    ideProvider: 'cursor',
    restorable: false,
    lastActiveAt: globalMtime
      ? new Date(
          Math.max(new Date(w.lastActiveAt).getTime(), globalMtime.getTime()),
        ).toISOString()
      : w.lastActiveAt,
  }));
}

type ComposerBranch = { branchName?: string; lastInteractionAt?: number };
type ComposerTrackedRepo = { repoPath?: string; branches?: ComposerBranch[] };
type ComposerWorkspaceId = { id?: string };
type ComposerHead = {
  composerId?: string;
  name?: string;
  subtitle?: string;
  lastUpdatedAt?: number;
  createdAt?: number;
  totalLinesAdded?: number;
  totalLinesRemoved?: number;
  filesChangedCount?: number;
  isArchived?: boolean;
  isDraft?: boolean;
  trackedGitRepos?: ComposerTrackedRepo[];
  workspaceIdentifier?: ComposerWorkspaceId;
};

type AgentProject = {
  id?: string;
  name?: string;
  createdAt?: number;
  lastUpdatedAt?: number;
  isArchived?: boolean;
};

const SESSION_DB_TIMEOUT_MS = 5_000;
const MAX_SESSIONS = 20;
const SUBTITLE_MAX = 100;

function getCursorGlobalStateDbPath(): string | null {
  const appdata =
    process.env.APPDATA || path.join(os.homedir(), 'AppData', 'Roaming');
  if (!appdata) return null;
  return path.join(appdata, 'Cursor', 'User', 'globalStorage', 'state.vscdb');
}

/**
 * Lazily load `node:sqlite`. Returns null if the module is unavailable in this
 * runtime (e.g. an Electron build bundled with a Node version that predates
 * `node:sqlite`, or where it is gated behind an experimental flag we can't
 * set in a packaged tray app).
 */
function loadNodeSqlite(): { DatabaseSync: typeof import('node:sqlite').DatabaseSync } | null {
  try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    return require('node:sqlite') as typeof import('node:sqlite');
  } catch {
    return null;
  }
}

function readComposerHeaders(
  DatabaseSync: typeof import('node:sqlite').DatabaseSync,
  dbPath: string,
): { allComposers: ComposerHead[] } | null {
  let db: import('node:sqlite').DatabaseSync | null = null;
  try {
    db = new DatabaseSync(dbPath, { readOnly: true });
    const row = db
      .prepare("SELECT value FROM ItemTable WHERE key = 'composer.composerHeaders'")
      .get() as { value?: string } | undefined;
    if (!row || typeof row.value !== 'string') return null;
    const parsed = JSON.parse(row.value) as { allComposers?: ComposerHead[] };
    return { allComposers: Array.isArray(parsed.allComposers) ? parsed.allComposers : [] };
  } catch {
    return null;
  } finally {
    try {
      db?.close();
    } catch {
      /* ignore */
    }
  }
}

function readAgentProjects(
  DatabaseSync: typeof import('node:sqlite').DatabaseSync,
  dbPath: string,
): AgentProject[] {
  let db: import('node:sqlite').DatabaseSync | null = null;
  try {
    db = new DatabaseSync(dbPath, { readOnly: true });
    const row = db
      .prepare("SELECT value FROM ItemTable WHERE key = 'glass.localAgentProjects.v1'")
      .get() as { value?: string } | undefined;
    if (!row || typeof row.value !== 'string') return [];
    const parsed = JSON.parse(row.value) as unknown;
    return Array.isArray(parsed) ? (parsed as AgentProject[]) : [];
  } catch {
    return [];
  } finally {
    try {
      db?.close();
    } catch {
      /* ignore */
    }
  }
}

function truncate(s: string | undefined, max: number): string | undefined {
  if (!s) return undefined;
  const trimmed = s.trim();
  if (!trimmed) return undefined;
  return trimmed.length > max ? `${trimmed.slice(0, max)}…` : trimmed;
}

function isoFromEpochMs(ms: number | undefined): string | undefined {
  if (typeof ms !== 'number' || !Number.isFinite(ms) || ms <= 0) return undefined;
  return new Date(ms).toISOString();
}

/**
 * Read real Cursor chat/agent sessions from the global SQLite store.
 * Returns null on any failure so the caller can fall back to the legacy
 * workspace-derived sessions.
 */
function readCursorComposerSessionsFromSqlite(): BridgeSession[] | null {
  const sqlite = loadNodeSqlite();
  if (!sqlite) return null;
  const dbPath = getCursorGlobalStateDbPath();
  if (!dbPath || !fs.existsSync(dbPath)) return null;

  // Run the read with a hard timeout so a locked/oversized DB never blocks
  // the Founder Node sync loop. SQLite opens the file lazily and our query
  // is a single key lookup, so this is normally <50ms even on a 24GB file.
  const started = Date.now();
  try {
    const headers = readComposerHeaders(sqlite.DatabaseSync, dbPath);
    if (!headers) return null;
    const agentProjects = readAgentProjects(sqlite.DatabaseSync, dbPath);
    if (Date.now() - started > SESSION_DB_TIMEOUT_MS) return null;

    const agentNameById = new Map<string, string>();
    for (const ap of agentProjects) {
      if (ap.id && ap.name && !ap.isArchived) agentNameById.set(ap.id, ap.name);
    }

    const sessions: BridgeSession[] = [];
    for (const c of headers.allComposers) {
      if (c.isArchived || c.isDraft) continue;
      if (!c.composerId || !c.name) continue;
      const lastActiveAt = isoFromEpochMs(c.lastUpdatedAt) ?? isoFromEpochMs(c.createdAt);
      if (!lastActiveAt) continue;

      const repo = c.trackedGitRepos?.[0]?.repoPath;
      const branch = c.trackedGitRepos?.[0]?.branches?.[0]?.branchName;
      const repository = repo ? folderNameFromPath(repo) : undefined;
      const workspaceId = c.workspaceIdentifier?.id;

      sessions.push({
        id: c.composerId,
        workspaceId,
        title: c.name,
        subtitle: truncate(c.subtitle, SUBTITLE_MAX),
        repository,
        branch,
        ideProvider: 'cursor',
        restorable: true,
        lastActiveAt,
        totalLinesAdded:
          typeof c.totalLinesAdded === 'number' ? c.totalLinesAdded : undefined,
        totalLinesRemoved:
          typeof c.totalLinesRemoved === 'number' ? c.totalLinesRemoved : undefined,
        filesChangedCount:
          typeof c.filesChangedCount === 'number' ? c.filesChangedCount : undefined,
        isAgentProject: workspaceId ? agentNameById.has(workspaceId) : false,
      });
    }

    sessions.sort(
      (a, b) =>
        new Date(b.lastActiveAt).getTime() - new Date(a.lastActiveAt).getTime(),
    );
    return sessions.slice(0, MAX_SESSIONS);
  } catch {
    return null;
  }
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
