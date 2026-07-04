import * as fs from 'node:fs';
import * as path from 'node:path';
import * as os from 'node:os';
import { spawnSync } from 'node:child_process';
import type {
  BridgeAgent,
  BridgeGitState,
  BridgeMessage,
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
type ComposerSubagentInfo = {
  subagentType?: number;
  parentComposerId?: string;
  subagentTypeName?: string;
  rootParentConversationId?: string;
};
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
  isBestOfNSubcomposer?: boolean;
  glassMetaParentAgent?: unknown;
  hasBeenInSidebar?: boolean;
  type?: string;
  subagentInfo?: ComposerSubagentInfo;
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
const MAX_MESSAGES_PER_SESSION = 50;
const MAX_SESSIONS_WITH_MESSAGES = 12;
const MESSAGE_TEXT_MAX = 1000;

function getCursorWorkspaceStorageRoot(): string | null {
  const appdata =
    process.env.APPDATA || path.join(os.homedir(), 'AppData', 'Roaming');
  if (!appdata) return null;
  return path.join(appdata, 'Cursor', 'User', 'workspaceStorage');
}

/** Map a Cursor workspaceStorage UUID to the local folder path on disk. */
export function resolveCursorWorkspaceFolder(workspaceStorageId: string): string | null {
  const root = getCursorWorkspaceStorageRoot();
  if (!root) return null;
  const wsJsonPath = path.join(root, workspaceStorageId, 'workspace.json');
  const wsJson = safeReadJson<WorkspaceJson>(wsJsonPath);
  if (!wsJson) return null;
  const folderUri = wsJson.folder ?? wsJson.configuration?.folderPath;
  if (!folderUri) return null;
  return fileUriToPath(folderUri);
}

/** Path to a workspace's state.vscdb (composer tab focus lives here). */
export function getCursorWorkspaceStateDbPath(workspaceStorageId: string): string | null {
  const root = getCursorWorkspaceStorageRoot();
  if (!root) return null;
  const dbPath = path.join(root, workspaceStorageId, 'state.vscdb');
  return fs.existsSync(dbPath) ? dbPath : null;
}

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

type ConversationHeader = {
  bubbleId?: string;
  type?: number;
  createdAt?: string;
  grouping?: {
    hasText?: boolean;
    isSimulatedMsg?: boolean;
    simulatedMsgReason?: number;
    simulatedMessageMetadataTitle?: string;
    capabilityType?: number;
  };
};

type ComposerDataRow = {
  composerId?: string;
  fullConversationHeadersOnly?: ConversationHeader[];
  generatingBubbleIds?: string[];
  isContinuationInProgress?: boolean;
  status?: string;
  subagentComposerIds?: string[];
  subagentInfo?: ComposerSubagentInfo;
  isBestOfNSubcomposer?: boolean;
};

/**
 * Shape of a Cursor chat bubble stored under `cursorDiskKV` key
 * `bubbleId:<composerId>:<bubbleId>`. Cursor's schema is not documented and
 * varies between versions, so every field is optional and we extract
 * defensively.
 */
type CursorBubble = {
  type?: number;
  text?: string;
  content?: string;
  richText?: string;
  context?: { text?: string } | string;
  message?: string;
  createdAt?: number;
  updatedAt?: number;
  timestamp?: number;
  ts?: number;
  model?: string;
};

/**
 * Cursor stores message bodies as a Lexical editor JSON string in `richText`.
 * Walk the `root.children[].children[].text` tree and concatenate text nodes.
 * Returns '' if the input isn't a Lexical document.
 */
function extractTextFromLexical(raw: unknown): string {
  if (!raw || typeof raw !== 'string') return '';
  let doc: unknown;
  try {
    doc = JSON.parse(raw);
  } catch {
    return '';
  }
  if (!doc || typeof doc !== 'object') return '';
  const out: string[] = [];
  const visit = (node: unknown): void => {
    if (!node || typeof node !== 'object') return;
    const n = node as Record<string, unknown>;
    if (typeof n.text === 'string') {
      out.push(n.text);
      return;
    }
    const children = n.children;
    if (Array.isArray(children)) {
      for (const c of children) visit(c);
    }
  };
  const root = (doc as Record<string, unknown>).root;
  visit(root);
  return out.join('').trim();
}

/**
 * Pull the human-readable text out of a bubble using every known field, in
 * priority order. `richText` (Lexical JSON) is parsed and flattened as a
 * fallback when no plain `text`/`content` is present.
 */
function extractBubbleText(b: CursorBubble): string {
  const candidates = [
    b.text,
    b.content,
    typeof b.context === 'string' ? b.context : b.context?.text,
    b.message,
  ];
  for (const c of candidates) {
    if (typeof c === 'string' && c.trim()) return c.trim();
  }
  const fromLexical = extractTextFromLexical(b.richText);
  if (fromLexical) return fromLexical;
  return '';
}

function isSystemOrSimulatedBubbleText(text: string): boolean {
  const t = text.trim();
  if (!t) return true;
  if (t.startsWith('<timestamp>')) return true;
  if (t.startsWith('<system_notification>')) return true;
  if (t.startsWith('<user_query>')) return false;
  return false;
}

function headerRole(header: ConversationHeader): BridgeMessage['role'] | null {
  if (header.type === 1) return 'user';
  if (header.type === 2) return 'assistant';
  return null;
}

function readComposerDataRow(
  db: import('node:sqlite').DatabaseSync,
  composerId: string,
): ComposerDataRow | null {
  try {
    const headerRow = db
      .prepare('SELECT value FROM cursorDiskKV WHERE key = ?')
      .get(`composerData:${composerId}`) as { value?: string } | undefined;
    if (!headerRow || typeof headerRow.value !== 'string') return null;
    return JSON.parse(headerRow.value) as ComposerDataRow;
  } catch {
    return null;
  }
}

/**
 * Collect every composer UUID Cursor marks as a subagent/subtopic — from
 * composer headers (`subagentInfo`) and parent `subagentComposerIds` lists.
 */
function collectSubagentComposerIds(
  DatabaseSync: typeof import('node:sqlite').DatabaseSync,
  dbPath: string,
  headers: ComposerHead[],
): Set<string> {
  const ids = new Set<string>();
  let db: import('node:sqlite').DatabaseSync | null = null;
  try {
    db = new DatabaseSync(dbPath, { readOnly: true });
    for (const c of headers) {
      const parentId = c.subagentInfo?.parentComposerId;
      if (parentId && c.composerId) ids.add(c.composerId);
      if (c.isBestOfNSubcomposer && c.composerId) ids.add(c.composerId);
      if (c.glassMetaParentAgent && c.composerId) ids.add(c.composerId);
    }
    for (const c of headers) {
      if (!c.composerId || ids.has(c.composerId)) continue;
      const data = readComposerDataRow(db, c.composerId);
      if (!data) continue;
      if (data.subagentInfo?.parentComposerId) ids.add(c.composerId);
      if (data.isBestOfNSubcomposer) ids.add(c.composerId);
      for (const sid of data.subagentComposerIds ?? []) {
        if (typeof sid === 'string' && sid.trim()) ids.add(sid.trim());
      }
    }
  } catch {
    /* ignore */
  } finally {
    try {
      db?.close();
    } catch {
      /* ignore */
    }
  }
  return ids;
}

/** True for composers Cursor shows as top-level chat tabs in the sidebar. */
function isTopLevelComposerHeader(c: ComposerHead, subagentIds: Set<string>): boolean {
  if (!c.composerId || !c.name) return false;
  if (c.isArchived || c.isDraft) return false;
  if (c.subagentInfo?.parentComposerId) return false;
  if (c.isBestOfNSubcomposer) return false;
  if (c.glassMetaParentAgent) return false;
  if (subagentIds.has(c.composerId)) return false;
  return true;
}

function parentComposerIdFromHeader(c: ComposerHead): string | undefined {
  return c.subagentInfo?.parentComposerId?.trim() || undefined;
}

function buildSessionFromHeader(
  c: ComposerHead,
  agentNameById: Map<string, string>,
  parentComposerId?: string,
): BridgeSession | null {
  if (!c.composerId || !c.name) return null;
  const lastActiveAt = isoFromEpochMs(c.lastUpdatedAt) ?? isoFromEpochMs(c.createdAt);
  if (!lastActiveAt) return null;

  const repo = c.trackedGitRepos?.[0]?.repoPath;
  const branch = c.trackedGitRepos?.[0]?.branches?.[0]?.branchName;
  const repository = repo ? folderNameFromPath(repo) : undefined;
  const workspaceStorageId = c.workspaceIdentifier?.id;
  const folderPath = workspaceStorageId
    ? resolveCursorWorkspaceFolder(workspaceStorageId) ?? undefined
    : undefined;

  return {
    id: c.composerId,
    composerId: c.composerId,
    workspaceId: workspaceStorageId,
    workspaceStorageId,
    folderPath,
    title: c.name,
    subtitle: truncate(c.subtitle, SUBTITLE_MAX),
    repository,
    branch,
    ideProvider: 'cursor',
    restorable: true,
    lastActiveAt,
    totalLinesAdded: typeof c.totalLinesAdded === 'number' ? c.totalLinesAdded : undefined,
    totalLinesRemoved: typeof c.totalLinesRemoved === 'number' ? c.totalLinesRemoved : undefined,
    filesChangedCount: typeof c.filesChangedCount === 'number' ? c.filesChangedCount : undefined,
    isAgentProject: workspaceStorageId ? agentNameById.has(workspaceStorageId) : false,
    ...(parentComposerId ? { parentComposerId } : {}),
  };
}

function shouldIncludeHeader(header: ConversationHeader): boolean {
  if (!header.bubbleId) return false;
  if (header.grouping?.isSimulatedMsg) return false;
  if (header.grouping?.hasText === false) return false;
  return true;
}

/**
 * Read the conversation thread for a composer using Cursor's authoritative
 * `composerData:<id>.fullConversationHeadersOnly` ordering, then fetch each
 * bubble by exact key. This avoids the broken `ORDER BY key DESC` scan that
 * pulled unrelated/stale bubbles when a composer has tens of thousands of
 * bubble rows (subagent/tool traffic).
 */
export function readComposerMessages(
  composerId: string,
  limit = MAX_MESSAGES_PER_SESSION,
): BridgeMessage[] {
  const result = readComposerMessagesWithMeta(composerId, limit);
  return result.messages;
}

/** Read messages plus agent-typing state from Cursor bubble DB. */
export function readComposerMessagesWithMeta(
  composerId: string,
  limit = MAX_MESSAGES_PER_SESSION,
): { messages: BridgeMessage[]; agentTyping: boolean } {
  const sqlite = loadNodeSqlite();
  if (!sqlite) return { messages: [], agentTyping: false };
  const dbPath = getCursorGlobalStateDbPath();
  if (!dbPath || !fs.existsSync(dbPath)) return { messages: [], agentTyping: false };

  let db: import('node:sqlite').DatabaseSync | null = null;
  try {
    db = new sqlite.DatabaseSync(dbPath, { readOnly: true });
    const parsed = readComposerDataRow(db, composerId);
    if (!parsed) return { messages: [], agentTyping: false };
    const headers = Array.isArray(parsed.fullConversationHeadersOnly)
      ? parsed.fullConversationHeadersOnly
      : [];
    if (headers.length === 0) return { messages: [], agentTyping: false };

    const generating = new Set(
      (Array.isArray(parsed.generatingBubbleIds) ? parsed.generatingBubbleIds : [])
        .filter((id): id is string => typeof id === 'string' && id.length > 0),
    );
    const agentTyping =
      generating.size > 0 ||
      parsed.isContinuationInProgress === true ||
      parsed.status === 'generating';

    const tail = headers.filter(shouldIncludeHeader).slice(-limit * 2);
    const bubbleStmt = db.prepare('SELECT value FROM cursorDiskKV WHERE key = ?');
    const messages: BridgeMessage[] = [];
    const seenBubbleIds = new Set<string>();

    for (const header of tail) {
      const role = headerRole(header);
      if (!role || !header.bubbleId) continue;
      seenBubbleIds.add(header.bubbleId);
      const isStreaming = generating.has(header.bubbleId);
      const bubbleRow = bubbleStmt.get(
        `bubbleId:${composerId}:${header.bubbleId}`,
      ) as { value?: string } | undefined;
      if (!bubbleRow || typeof bubbleRow.value !== 'string') {
        if (isStreaming && role === 'assistant') {
          messages.push({
            role,
            content: '',
            streaming: true,
            partial: true,
            ...(header.createdAt ? { timestamp: header.createdAt } : {}),
          });
        }
        continue;
      }
      let bubble: CursorBubble;
      try {
        bubble = JSON.parse(bubbleRow.value) as CursorBubble;
      } catch {
        continue;
      }
      const text = extractBubbleText(bubble);
      if (!text && !isStreaming) continue;
      if (text && isSystemOrSimulatedBubbleText(text) && !isStreaming) continue;
      messages.push({
        role,
        content: (text || '').slice(0, MESSAGE_TEXT_MAX),
        ...(header.createdAt ? { timestamp: header.createdAt } : {}),
        ...(bubble.model ? { model: bubble.model.slice(0, 60) } : {}),
        ...(isStreaming ? { streaming: true, partial: true } : {}),
      });
    }

    // In-progress bubbles not yet listed in conversation headers.
    for (const bubbleId of generating) {
      if (seenBubbleIds.has(bubbleId)) continue;
      const bubbleRow = bubbleStmt.get(
        `bubbleId:${composerId}:${bubbleId}`,
      ) as { value?: string } | undefined;
      let text = '';
      if (bubbleRow && typeof bubbleRow.value === 'string') {
        try {
          text = extractBubbleText(JSON.parse(bubbleRow.value) as CursorBubble);
        } catch {
          /* ignore */
        }
      }
      messages.push({
        role: 'assistant',
        content: text.slice(0, MESSAGE_TEXT_MAX),
        streaming: true,
        partial: true,
      });
    }

    return { messages: messages.slice(-limit), agentTyping };
  } catch {
    return { messages: [], agentTyping: false };
  } finally {
    try {
      db?.close();
    } catch {
      /* ignore */
    }
  }
}

/**
 * Resolve dispatch context for a Cursor composer session.
 */
export function resolveCursorComposerContext(composerId: string): {
  composerId: string;
  workspaceStorageId?: string;
  folderPath?: string;
  title?: string;
} | null {
  const sqlite = loadNodeSqlite();
  if (!sqlite) return null;
  const dbPath = getCursorGlobalStateDbPath();
  if (!dbPath || !fs.existsSync(dbPath)) return null;

  const headers = readComposerHeaders(sqlite.DatabaseSync, dbPath);
  if (!headers) return null;
  const match = headers.allComposers.find((c) => c.composerId === composerId);
  if (!match) return null;

  const workspaceStorageId = match.workspaceIdentifier?.id;
  const folderPath = workspaceStorageId
    ? resolveCursorWorkspaceFolder(workspaceStorageId) ?? undefined
    : undefined;

  return {
    composerId,
    workspaceStorageId,
    folderPath,
    title: match.name,
  };
}

/**
 * Move a composer to the front of Cursor's workspace tab focus list so the
 * next window activation shows that chat tab instead of whatever was last open.
 */
export function focusComposerInWorkspaceState(
  workspaceStorageId: string,
  composerId: string,
): boolean {
  const sqlite = loadNodeSqlite();
  if (!sqlite) return false;
  const dbPath = getCursorWorkspaceStateDbPath(workspaceStorageId);
  if (!dbPath) return false;

  let db: import('node:sqlite').DatabaseSync | null = null;
  try {
    db = new sqlite.DatabaseSync(dbPath);
    const row = db
      .prepare("SELECT value FROM ItemTable WHERE key = 'composer.composerData'")
      .get() as { value?: string } | undefined;
    if (!row || typeof row.value !== 'string') return false;

    const data = JSON.parse(row.value) as {
      selectedComposerIds?: string[];
      lastFocusedComposerIds?: string[];
    };
    const selected = Array.isArray(data.selectedComposerIds)
      ? [...data.selectedComposerIds]
      : [];
    const focused = Array.isArray(data.lastFocusedComposerIds)
      ? [...data.lastFocusedComposerIds]
      : [];

    if (!selected.includes(composerId)) selected.unshift(composerId);
    else {
      const idx = selected.indexOf(composerId);
      selected.splice(idx, 1);
      selected.unshift(composerId);
    }

    data.selectedComposerIds = selected;
    data.lastFocusedComposerIds = [
      composerId,
      ...focused.filter((id) => id !== composerId),
    ];

    db.prepare(
      "INSERT OR REPLACE INTO ItemTable (key, value) VALUES ('composer.composerData', ?)",
    ).run(JSON.stringify(data));
    return true;
  } catch (err) {
    console.warn('focusComposerInWorkspaceState failed:', err);
    return false;
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

    const subagentIds = collectSubagentComposerIds(
      sqlite.DatabaseSync,
      dbPath,
      headers.allComposers,
    );

    const topLevel: BridgeSession[] = [];
    const subagentsByParent = new Map<string, BridgeSession[]>();

    for (const c of headers.allComposers) {
      const parentId = parentComposerIdFromHeader(c);
      if (parentId && c.composerId) {
        const sub = buildSessionFromHeader(c, agentNameById, parentId);
        if (sub) {
          const list = subagentsByParent.get(parentId) ?? [];
          list.push(sub);
          subagentsByParent.set(parentId, list);
        }
        continue;
      }
      if (!isTopLevelComposerHeader(c, subagentIds)) continue;
      const session = buildSessionFromHeader(c, agentNameById);
      if (session) topLevel.push(session);
    }

    for (const s of topLevel) {
      const children = subagentsByParent.get(s.id);
      if (children && children.length > 0) {
        children.sort(
          (a, b) =>
            new Date(b.lastActiveAt).getTime() - new Date(a.lastActiveAt).getTime(),
        );
        for (const sub of children.slice(0, 3)) {
          if (Date.now() - started > SESSION_DB_TIMEOUT_MS) break;
          const { messages: subMsgs, agentTyping: subTyping } = readComposerMessagesWithMeta(
            sub.id,
            20,
          );
          if (subMsgs.length > 0) {
            sub.messages = subMsgs;
            sub.messageCount = subMsgs.length;
          }
          if (subTyping) sub.agentTyping = true;
        }
        s.subagents = children.slice(0, 8);
      }
    }

    topLevel.sort(
      (a, b) =>
        new Date(b.lastActiveAt).getTime() - new Date(a.lastActiveAt).getTime(),
    );
    const top = topLevel.slice(0, MAX_SESSIONS);

    // Attach the recent message thread to the most recent sessions so the
    // dashboard can show what was actually said without an extra round trip.
    // Only the top N sessions get messages — reading bubbles for every
    // session would balloon the heartbeat payload and the SQLite scan time.
    const withMessages = top.slice(0, MAX_SESSIONS_WITH_MESSAGES);
    for (const s of withMessages) {
      const composerId = s.composerId ?? s.id;
      const { messages: msgs, agentTyping } = readComposerMessagesWithMeta(composerId);
      if (msgs.length > 0) {
        s.messages = msgs;
        s.messageCount = msgs.length;
      }
      if (agentTyping) s.agentTyping = true;
      if (Date.now() - started > SESSION_DB_TIMEOUT_MS) break;
    }

    return top;
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
