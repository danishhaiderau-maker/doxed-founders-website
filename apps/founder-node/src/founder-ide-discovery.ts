/**
 * Phase A - Desktop Runtime.
 *
 * Founder IDE discovery module. Founder IDE is a VS Code fork (see
 * packages/founder-ide/assets/product.json.template: nameLong "Founder IDE",
 * dataFolderName ".founder-ide"), so its on-disk workspace/session storage is
 * IDENTICAL to Cursor's:
 *   workspaceStorage/<uuid>/workspace.json  -> { folder: "file:///path" }
 *   workspaceStorage/<uuid>/                -> mtime = last activity
 *   globalStorage/state.vscdb               -> SQLite (chat history)
 *
 * This module mirrors cursor-discovery.ts but resolves the User directory via
 * getFounderIdeUserDirs() (cross-platform: FounderIDE / "Founder IDE" /
 * .founder-ide), probing each candidate. The SQLite reading logic is reused
 * verbatim because state.vscdb's schema is the VS Code schema.
 */

import * as fs from 'node:fs';
import * as path from 'node:path';
import { spawnSync } from 'node:child_process';
import type {
  BridgeAgent,
  BridgeMessage,
  BridgeSession,
  BridgeWorkspace,
} from '@dcf/utils';
import { getFounderIdeUserDirs } from './connect-ide';

const MAX_WORKSPACES = 10;
const SESSION_DB_TIMEOUT_MS = 5_000;
const MAX_SESSIONS = 20;
const SUBTITLE_MAX = 100;
const MAX_MESSAGES_PER_SESSION = 50;
const MAX_SESSIONS_WITH_MESSAGES = 20;
const MESSAGE_TEXT_MAX = 1000;

type WorkspaceJson = { folder?: string; configuration?: { folderPath?: string } };

/**
 * Pick the first Founder IDE User/ directory that actually exists on disk.
 * Returns null when no candidate is present (IDE not installed / never run).
 */
function resolveFounderIdeUserDir(): string | null {
  for (const dir of getFounderIdeUserDirs()) {
    if (fs.existsSync(dir)) return dir;
  }
  return null;
}

/** workspaceStorage path under the resolved Founder IDE User dir. */
export function getFounderIdeWorkspaceStoragePath(): string | null {
  const user = resolveFounderIdeUserDir();
  if (!user) return null;
  return path.join(user, 'workspaceStorage');
}

/** globalStorage path under the resolved Founder IDE User dir. */
export function getFounderIdeGlobalStoragePath(): string | null {
  const user = resolveFounderIdeUserDir();
  if (!user) return null;
  return path.join(user, 'globalStorage');
}

/** Path to the global state.vscdb (Composer/agent chat history). */
export function getFounderIdeGlobalStateDbPath(): string | null {
  const global = getFounderIdeGlobalStoragePath();
  if (!global) return null;
  const dbPath = path.join(global, 'state.vscdb');
  return fs.existsSync(dbPath) ? dbPath : null;
}

/** Alias for the workspaceStorage root (mirrors cursor-discovery naming). */
export function getFounderIdeWorkspaceStorageRoot(): string | null {
  return getFounderIdeWorkspaceStoragePath();
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

function getGitBranch(workspacePath: string): string | undefined {
  try {
    const headPath = path.join(workspacePath, '.git', 'HEAD');
    if (!fs.existsSync(headPath)) return undefined;
    const raw = fs.readFileSync(headPath, 'utf8').trim();
    const match = raw.match(/ref:\s+refs\/heads\/(.+)/);
    return match?.[1];
  } catch {
    return undefined;
  }
}

/**
 * Discover Founder IDE workspaces from the local machine.
 * Scans Founder IDE's workspaceStorage directory for recent workspaces.
 * Mirrors discoverCursorWorkspaces() with founder-ide ids + ideProvider.
 */
export function discoverFounderIdeWorkspaces(): BridgeWorkspace[] {
  const storage = getFounderIdeWorkspaceStoragePath();
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

  const running = isFounderIdeRunning();

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
    const repository = readGitRemote(folderPath) ?? folderPath;

    discovered.push({
      id: `founder-ide:${entry.name}`,
      title,
      repository,
      branch: getGitBranch(folderPath),
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
    ideProvider: 'founder-ide',
    lastActiveAt: w.lastActiveAt,
    hasActiveAgent: running,
    messageCount: undefined,
  }));
}

/**
 * Discover Founder IDE recent sessions/conversations.
 *
 * Founder IDE stores chat history in the same SQLite database Cursor uses
 * (globalStorage/state.vscdb) because it is a VS Code fork. We reuse the same
 * Composer/AgentProject reading logic verbatim. If anything fails (node:sqlite
 * unavailable, DB locked, schema changed, or this Founder IDE build doesn't
 * store Composer data) we fall back to workspace-derived sessions.
 */
export function discoverFounderIdeSessions(): BridgeSession[] {
  const sqliteSessions = readFounderIdeComposerSessionsFromSqlite();
  if (sqliteSessions && sqliteSessions.length > 0) return sqliteSessions;
  return discoverFounderIdeSessionsFromWorkspaces();
}

/**
 * Legacy fallback - derive placeholder sessions from workspace activity.
 * Marked `restorable: false` because we have no real conversation handle.
 */
function discoverFounderIdeSessionsFromWorkspaces(): BridgeSession[] {
  const workspaces = discoverFounderIdeWorkspaces();
  const globalStorage = getFounderIdeGlobalStoragePath();
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
    ideProvider: 'founder-ide',
    restorable: false,
    lastActiveAt: globalMtime
      ? new Date(
          Math.max(new Date(w.lastActiveAt).getTime(), globalMtime.getTime()),
        ).toISOString()
      : w.lastActiveAt,
  }));
}

/** Map a Founder IDE workspaceStorage UUID to the local folder path on disk. */
export function resolveFounderIdeWorkspaceFolder(workspaceStorageId: string): string | null {
  const root = getFounderIdeWorkspaceStorageRoot();
  if (!root) return null;
  const wsJsonPath = path.join(root, workspaceStorageId, 'workspace.json');
  const wsJson = safeReadJson<WorkspaceJson>(wsJsonPath);
  if (!wsJson) return null;
  const folderUri = wsJson.folder ?? wsJson.configuration?.folderPath;
  if (!folderUri) return null;
  return fileUriToPath(folderUri);
}

// ---------------------------------------------------------------------------
// SQLite reader (verbatim mirror of cursor-discovery's Composer logic - the
// state.vscdb schema is identical because Founder IDE is a VS Code fork).
// ---------------------------------------------------------------------------

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
 * Lazily load `node:sqlite`. Returns null if the module is unavailable in this
 * runtime (e.g. an Electron build bundled with a Node version that predates
 * `node:sqlite`).
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

function isTopLevelComposerHeader(c: ComposerHead, subagentIds: Set<string>): boolean {
  if (!c.composerId || !c.name) return false;
  if (c.isArchived || c.isDraft) return false;
  if (c.subagentInfo?.parentComposerId || c.subagentInfo?.rootParentConversationId) {
    return false;
  }
  if (c.subagentInfo?.subagentTypeName || typeof c.subagentInfo?.subagentType === 'number') {
    return false;
  }
  if (c.isBestOfNSubcomposer) return false;
  if (c.glassMetaParentAgent) return false;
  if (subagentIds.has(c.composerId)) return false;
  if (c.hasBeenInSidebar === false) return false;
  return true;
}

function truncate(s: string | undefined, max: number): string | undefined {
  if (!s) return undefined;
  const trimmed = s.trim();
  if (!trimmed) return undefined;
  return trimmed.length > max ? `${trimmed.slice(0, max)}...` : trimmed;
}

function isoFromEpochMs(ms: number | undefined): string | undefined {
  if (typeof ms !== 'number' || !Number.isFinite(ms) || ms <= 0) return undefined;
  return new Date(ms).toISOString();
}

function buildSessionFromHeader(
  c: ComposerHead,
  agentNameById: Map<string, string>,
): BridgeSession | null {
  if (!c.composerId || !c.name) return null;
  const lastActiveAt = isoFromEpochMs(c.lastUpdatedAt) ?? isoFromEpochMs(c.createdAt);
  if (!lastActiveAt) return null;

  const repo = c.trackedGitRepos?.[0]?.repoPath;
  const branch = c.trackedGitRepos?.[0]?.branches?.[0]?.branchName;
  const repository = repo ? folderNameFromPath(repo) : undefined;
  const workspaceStorageId = c.workspaceIdentifier?.id;
  const folderPath = workspaceStorageId
    ? resolveFounderIdeWorkspaceFolder(workspaceStorageId) ?? undefined
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
    ideProvider: 'founder-ide',
    restorable: true,
    lastActiveAt,
    totalLinesAdded: typeof c.totalLinesAdded === 'number' ? c.totalLinesAdded : undefined,
    totalLinesRemoved: typeof c.totalLinesRemoved === 'number' ? c.totalLinesRemoved : undefined,
    filesChangedCount: typeof c.filesChangedCount === 'number' ? c.filesChangedCount : undefined,
    isAgentProject: workspaceStorageId ? agentNameById.has(workspaceStorageId) : false,
  };
}

function shouldIncludeHeader(header: ConversationHeader): boolean {
  if (!header.bubbleId) return false;
  if (header.grouping?.isSimulatedMsg) return false;
  if (header.grouping?.hasText === false) return false;
  return true;
}

function headerRole(header: ConversationHeader): BridgeMessage['role'] | null {
  if (header.type === 1) return 'user';
  if (header.type === 2) return 'assistant';
  return null;
}

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

/** Read messages plus agent-typing state from Founder IDE bubble DB. */
function readComposerMessagesWithMeta(
  composerId: string,
  limit = MAX_MESSAGES_PER_SESSION,
): { messages: BridgeMessage[]; agentTyping: boolean } {
  const sqlite = loadNodeSqlite();
  if (!sqlite) return { messages: [], agentTyping: false };
  const dbPath = getFounderIdeGlobalStateDbPath();
  if (!dbPath) return { messages: [], agentTyping: false };

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
 * Read real Founder IDE chat/agent sessions from the global SQLite store.
 * Returns null on any failure so the caller can fall back to the legacy
 * workspace-derived sessions.
 */
function readFounderIdeComposerSessionsFromSqlite(): BridgeSession[] | null {
  const sqlite = loadNodeSqlite();
  if (!sqlite) return null;
  const dbPath = getFounderIdeGlobalStateDbPath();
  if (!dbPath) return null;

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
    for (const c of headers.allComposers) {
      if (!isTopLevelComposerHeader(c, subagentIds)) continue;
      const session = buildSessionFromHeader(c, agentNameById);
      if (session) topLevel.push(session);
    }

    topLevel.sort(
      (a, b) =>
        new Date(b.lastActiveAt).getTime() - new Date(a.lastActiveAt).getTime(),
    );
    const top = topLevel.slice(0, MAX_SESSIONS);

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
 * Best-effort detection of whether Founder IDE is running on this machine.
 * Matches the VS Code-fork binary names: "Founder IDE.exe" on Windows,
 * "Founder IDE" on macOS, "founder-ide" on Linux. Also tolerates QA/internal
 * builds suffixed like "Founder IDE Next QA".
 */
export function isFounderIdeRunning(): boolean {
  try {
    if (process.platform === 'win32') {
      // Scan the full process list once and match any Founder IDE image.
      // We deliberately don't use tasklist's /FI IMAGENAME filter here:
      // (a) it returns exit 0 even when nothing matches, and
      // (b) QA/internal builds are named differently (e.g. 'Founder IDE Next QA.exe').
      // Matching /^Founder IDE.*\.exe/i covers the VS Code-fork binary and its variants.
      const res = spawnSync('tasklist', ['/FO', 'CSV', '/NH'], {
        encoding: 'utf8',
        windowsHide: true,
        timeout: 4_000,
      });
      if (res.error || res.status !== 0) return false;
      return /Founder IDE[^"]*\.exe/i.test(res.stdout || '');
    }
    if (process.platform === 'darwin') {
      const res = spawnSync('pgrep', ['-fx', 'Founder IDE'], {
        encoding: 'utf8',
        timeout: 4_000,
      });
      return res.status === 0 && Boolean((res.stdout || '').trim());
    }
    const res = spawnSync('pgrep', ['-f', '[F]ounder[- ]IDE'], {
      encoding: 'utf8',
      timeout: 4_000,
    });
    return res.status === 0 && Boolean((res.stdout || '').trim());
  } catch {
    return false;
  }
}

/**
 * Report active Founder IDE agents. Best-effort - surfaces one aggregate agent
 * whose status reflects whether the Founder IDE process is alive.
 */
export function discoverFounderIdeAgents(): BridgeAgent[] {
  const running = isFounderIdeRunning();
  return [
    {
      id: 'founder-ide-local',
      label: 'Founder IDE',
      status: running ? 'running' : 'idle',
      task: undefined,
      startedAt: undefined,
    },
  ];
}