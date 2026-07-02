import * as fs from 'node:fs';
import * as path from 'node:path';
import * as os from 'node:os';
import { spawnSync } from 'node:child_process';
import type {
  BridgeAgent,
  BridgeSession,
  BridgeWorkspace,
} from '@dcf/utils';

/**
 * Claude Code discovery.
 *
 * Claude Code (Anthropic's CLI coding agent) stores per-project session
 * history on disk:
 *
 *   ~/.claude/projects/<slug>/*.jsonl
 *
 * Where `<slug>` is derived from the absolute project path by replacing
 * `:`, `\`, `/`, and space characters with `-` (e.g.
 * `c:\Users\me\repo` → `c--Users-me-repo`). Each `.jsonl` file is one
 * conversation/session, newline-delimited JSON records of type `user`,
 * `assistant`, `summary`, etc.
 *
 * This module scans that storage so Founder OS can surface Claude Code
 * workspaces and sessions alongside Cursor ones in the IDE bridge.
 *
 * Only Node.js built-ins are used — no external dependencies — so the
 * Electron Founder Node stays self-contained.
 */

const MAX_WORKSPACES = 10;
const MAX_SESSIONS = 20;
const SUBTITLE_MAX = 120;
const TITLE_MAX = 120;

function getClaudeDir(): string {
  return path.join(os.homedir(), '.claude');
}

function getClaudeProjectsDir(): string {
  return path.join(getClaudeDir(), 'projects');
}

/**
 * Reverse Claude Code's path → slug encoding. Best-effort: replaces `-`
 * back with the OS separator. Ambiguous when a path segment itself
 * contains `-`, so callers should verify the result with `fs.existsSync`
 * before trusting it.
 */
function slugToPath(slug: string): string {
  if (!slug) return '';
  const isWin = process.platform === 'win32';
  // Windows drive letter: `<drive>--<rest>` → `<drive>:\rest` (the leading
  // `--` came from `:` + `\`).
  if (isWin && /^[a-zA-Z]--/.test(slug)) {
    const drive = slug[0]!;
    const rest = slug.slice(3);
    const segs = rest.split('-').filter(Boolean);
    return `${drive.toUpperCase()}:\\${segs.join('\\')}`;
  }
  // POSIX: leading `-` came from a leading `/`.
  const stripped = slug.replace(/^-+/, '');
  const segs = stripped.split('-').filter(Boolean);
  return (isWin ? segs.join('\\') : '/' + segs.join('/'));
}

function folderNameFromPath(p: string): string {
  const norm = p.replace(/\\/g, '/').replace(/\/$/, '');
  const seg = norm.split('/').filter(Boolean).pop();
  return seg || p;
}

function truncate(s: string | undefined, max: number): string | undefined {
  if (!s) return undefined;
  const trimmed = s.trim();
  if (!trimmed) return undefined;
  return trimmed.length > max ? `${trimmed.slice(0, max)}…` : trimmed;
}

type JsonlRecord = {
  type?: string;
  summary?: string;
  message?: {
    role?: string;
    content?: string | Array<{ type?: string; text?: string }>;
  };
  timestamp?: string;
  cwd?: string;
  gitBranch?: string;
};

function readJsonlRecords(file: string): JsonlRecord[] {
  try {
    const raw = fs.readFileSync(file, 'utf8');
    const records: JsonlRecord[] = [];
    for (const line of raw.split(/\r?\n/)) {
      const trimmed = line.trim();
      if (!trimmed) continue;
      try {
        records.push(JSON.parse(trimmed) as JsonlRecord);
      } catch {
        /* skip malformed line */
      }
    }
    return records;
  } catch {
    return [];
  }
}

type MessageContent = string | Array<{ type?: string; text?: string }> | undefined;

function extractText(content: MessageContent): string | undefined {
  if (!content) return undefined;
  if (typeof content === 'string') return content;
  if (Array.isArray(content)) {
    for (const part of content) {
      if (part && typeof part === 'object' && typeof part.text === 'string') {
        return part.text;
      }
    }
  }
  return undefined;
}

function deriveSessionTitle(records: JsonlRecord[], fallback: string): string {
  // Prefer a `summary` record (Claude Code writes one at the start of a
  // session when it can).
  for (const r of records) {
    if (r.type === 'summary' && r.summary) {
      return truncate(r.summary, TITLE_MAX) ?? fallback;
    }
  }
  // Otherwise use the first user message.
  for (const r of records) {
    if (r.type === 'user' && r.message?.role === 'user') {
      const text = extractText(r.message?.content);
      if (text) return truncate(text, TITLE_MAX) ?? fallback;
    }
  }
  return fallback;
}

function deriveBranch(records: JsonlRecord[]): string | undefined {
  for (const r of records) {
    if (typeof r.gitBranch === 'string' && r.gitBranch.trim()) {
      return r.gitBranch.trim();
    }
  }
  return undefined;
}

function countMessages(records: JsonlRecord[]): number {
  let n = 0;
  for (const r of records) {
    if (r.type === 'user' || r.type === 'assistant') n += 1;
  }
  return n;
}

function listJsonlFiles(dir: string): string[] {
  try {
    const entries = fs.readdirSync(dir, { withFileTypes: true });
    return entries
      .filter((e) => e.isFile() && e.name.endsWith('.jsonl'))
      .map((e) => path.join(dir, e.name));
  } catch {
    return [];
  }
}

function mostRecentJsonl(dir: string): { file: string; mtime: Date } | null {
  const files = listJsonlFiles(dir);
  if (files.length === 0) return null;
  let best: { file: string; mtime: Date } | null = null;
  for (const file of files) {
    try {
      const st = fs.statSync(file);
      if (!best || st.mtime > best.mtime) {
        best = { file, mtime: st.mtime };
      }
    } catch {
      /* ignore */
    }
  }
  return best;
}

/**
 * Best-effort detection of whether Claude Code is running on this machine.
 */
export function isClaudeCodeRunning(): boolean {
  try {
    if (process.platform === 'win32') {
      const res = spawnSync(
        'tasklist',
        ['/FI', 'IMAGENAME eq node.exe', '/FO', 'CSV', '/NH'],
        { encoding: 'utf8', windowsHide: true, timeout: 4_000 },
      );
      if (res.error || res.status !== 0) return false;
      // Claude Code is a Node CLI; we can't distinguish it from other Node
      // processes by name alone. Treat any running Node as "possibly Claude
      // Code" so the UI shows an active agent when the user is using it.
      return /node\.exe/i.test(res.stdout || '');
    }
    if (process.platform === 'darwin') {
      const res = spawnSync('pgrep', ['-x', 'claude'], {
        encoding: 'utf8',
        timeout: 4_000,
      });
      if (res.status === 0 && Boolean((res.stdout || '').trim())) return true;
      // Fall back to looking for a `claude-code` process name.
      const res2 = spawnSync('pgrep', ['-f', 'claude-code'], {
        encoding: 'utf8',
        timeout: 4_000,
      });
      return res2.status === 0 && Boolean((res2.stdout || '').trim());
    }
    const res = spawnSync('pgrep', ['-f', 'claude'], {
      encoding: 'utf8',
      timeout: 4_000,
    });
    return res.status === 0 && Boolean((res.stdout || '').trim());
  } catch {
    return false;
  }
}

/**
 * Discover Claude Code projects from `~/.claude/projects/`. Each project
 * folder is one workspace; the most recent `.jsonl` file in it defines the
 * last-active time and the workspace title.
 */
export function discoverClaudeCodeWorkspaces(): BridgeWorkspace[] {
  const projectsDir = getClaudeProjectsDir();
  if (!fs.existsSync(projectsDir)) return [];

  let entries: fs.Dirent[];
  try {
    entries = fs.readdirSync(projectsDir, { withFileTypes: true });
  } catch {
    return [];
  }

  const running = isClaudeCodeRunning();
  const discovered: Array<{
    id: string;
    title: string;
    repository: string | undefined;
    branch: string | undefined;
    folderPath: string | null;
    lastActiveAt: string;
    messageCount: number;
  }> = [];

  for (const entry of entries) {
    if (!entry.isDirectory()) continue;
    const projectDir = path.join(projectsDir, entry.name);
    const recent = mostRecentJsonl(projectDir);
    if (!recent) continue;

    const candidatePath = slugToPath(entry.name);
    const folderPath =
      candidatePath && fs.existsSync(candidatePath) ? candidatePath : null;

    const records = readJsonlRecords(recent.file);
    const title = folderPath
      ? folderNameFromPath(folderPath)
      : deriveSessionTitle(records, entry.name);
    const branch = deriveBranch(records);
    const repository = folderPath ?? undefined;

    discovered.push({
      id: `claude_code:${entry.name}`,
      title,
      repository,
      branch,
      folderPath,
      lastActiveAt: recent.mtime.toISOString(),
      messageCount: countMessages(records),
    });
  }

  discovered.sort(
    (a, b) =>
      new Date(b.lastActiveAt).getTime() - new Date(a.lastActiveAt).getTime(),
  );

  return discovered.slice(0, MAX_WORKSPACES).map((w) => ({
    id: w.id,
    title: w.title,
    repository: w.repository,
    branch: w.branch,
    ideProvider: 'claude_code',
    lastActiveAt: w.lastActiveAt,
    hasActiveAgent: running,
    messageCount: w.messageCount,
  }));
}

/**
 * Discover recent Claude Code sessions. Each `.jsonl` file in each project
 * folder is one session. Marked `restorable: true` because Claude Code can
 * resume a session via `claude --resume <sessionId>` using the file's UUID.
 */
export function discoverClaudeCodeSessions(): BridgeSession[] {
  const projectsDir = getClaudeProjectsDir();
  if (!fs.existsSync(projectsDir)) return [];

  let projectEntries: fs.Dirent[];
  try {
    projectEntries = fs.readdirSync(projectsDir, { withFileTypes: true });
  } catch {
    return [];
  }

  const sessions: Array<{
    id: string;
    workspaceId: string;
    title: string;
    subtitle: string | undefined;
    repository: string | undefined;
    branch: string | undefined;
    lastActiveAt: string;
    messageCount: number;
    isAgentProject: boolean;
  }> = [];

  for (const projEntry of projectEntries) {
    if (!projEntry.isDirectory()) continue;
    const projectDir = path.join(projectsDir, projEntry.name);
    const files = listJsonlFiles(projectDir);
    const candidatePath = slugToPath(projEntry.name);
    const folderPath =
      candidatePath && fs.existsSync(candidatePath) ? candidatePath : null;
    const repoName = folderPath
      ? folderNameFromPath(folderPath)
      : projEntry.name;

    for (const file of files) {
      let mtime: Date;
      try {
        mtime = fs.statSync(file).mtime;
      } catch {
        continue;
      }
      const records = readJsonlRecords(file);
      if (records.length === 0) continue;

      const sessionId = path.basename(file, '.jsonl');
      const title = deriveSessionTitle(records, sessionId);
      const branch = deriveBranch(records);
      const messageCount = countMessages(records);
      const firstUser = records.find(
        (r) => r.type === 'user' && r.message?.role === 'user',
      );
      const subtitle = truncate(
        extractText(firstUser?.message?.content),
        SUBTITLE_MAX,
      );

      sessions.push({
        id: `claude_code:${projEntry.name}:${sessionId}`,
        workspaceId: `claude_code:${projEntry.name}`,
        title,
        subtitle,
        repository: repoName,
        branch,
        lastActiveAt: mtime.toISOString(),
        messageCount,
        // Claude Code sessions are always agent runs (the CLI IS the agent).
        isAgentProject: true,
      });
    }
  }

  sessions.sort(
    (a, b) =>
      new Date(b.lastActiveAt).getTime() - new Date(a.lastActiveAt).getTime(),
  );

  return sessions.slice(0, MAX_SESSIONS).map((s) => ({
    id: s.id,
    workspaceId: s.workspaceId,
    title: s.title,
    subtitle: s.subtitle,
    repository: s.repository,
    branch: s.branch,
    ideProvider: 'claude_code',
    restorable: true,
    lastActiveAt: s.lastActiveAt,
    messageCount: s.messageCount,
    isAgentProject: s.isAgentProject,
  }));
}

/**
 * Report active Claude Code agents. Best-effort — surfaces one aggregate
 * agent whose status reflects whether the Claude Code CLI is running.
 */
export function discoverClaudeCodeAgents(): BridgeAgent[] {
  const running = isClaudeCodeRunning();
  return [
    {
      id: 'claude-code-local',
      label: 'Claude Code',
      status: running ? 'running' : 'idle',
      task: undefined,
      startedAt: undefined,
    },
  ];
}
