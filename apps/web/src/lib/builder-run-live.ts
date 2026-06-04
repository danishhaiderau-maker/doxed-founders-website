import type { WorkspaceActivity } from '@dcf/utils';
import { formatWorkspaceActivityForChat } from '@dcf/utils';

export type BuilderRunSnapshot = {
  id: string;
  agentId: string;
  status: string;
  result?: string | null;
  durationMs?: number | null;
  terminal?: boolean;
  agentUrl?: string;
  git?: {
    branches?: { repoUrl?: string; branch?: string; prUrl?: string }[];
  } | null;
  workspaceActivity?: WorkspaceActivity | null;
  platformReconciliation?: string | null;
};

export type OpenHandsRunSnapshot = {
  conversationId: string;
  status: string;
  result?: string | null;
  terminal?: boolean;
  conversationUrl?: string | null;
};

function branchFromSnapshot(snapshot: BuilderRunSnapshot): string | null {
  const b = snapshot.git?.branches?.[0];
  return b?.branch?.trim() ?? null;
}

function prFromSnapshot(snapshot: BuilderRunSnapshot): string | null {
  const b = snapshot.git?.branches?.[0];
  return b?.prUrl?.trim() ?? null;
}

/** In-chat Builder Agent card — Cursor/OpenHands run inside Founder OS (Sprint 2). */
export function formatBuilderRunInChat(input: {
  workerLabel: string;
  task: string;
  repo?: string | null;
  snapshot: BuilderRunSnapshot;
  mode?: string;
}): string {
  const { task, repo, snapshot, mode } = input;
  const branch = branchFromSnapshot(snapshot);
  const prUrl = prFromSnapshot(snapshot);
  const recentCount = snapshot.workspaceActivity
    ? snapshot.workspaceActivity.commitsLast2h.length +
      snapshot.workspaceActivity.cursorBranchCommits.length
    : 0;
  const filesHint = recentCount > 0 ? `${recentCount} recent commit(s) on repo` : null;

  const lines: string[] = [
    '**Builder Agent**',
    mode === 'follow_up' ? '_Continuing on your repo_' : '_Coding on your repo_',
    '',
    `**Status:** ${snapshot.status}`,
    repo ? `**Repository:** \`${repo}\`` : '',
    branch ? `**Branch:** \`${branch}\`` : '',
    prUrl ? `**PR:** ${prUrl}` : '',
    filesHint ? `**Activity:** ${filesHint}` : '',
    '',
    '**Task**',
    task.trim().slice(0, 1200),
  ].filter(Boolean);

  if (snapshot.result?.trim()) {
    lines.push('', '**Latest output**', snapshot.result.trim().slice(0, 3500));
  }

  if (snapshot.platformReconciliation?.trim()) {
    lines.push('', snapshot.platformReconciliation.trim());
  }

  if (snapshot.workspaceActivity?.repoFullName) {
    lines.push('', formatWorkspaceActivityForChat(snapshot.workspaceActivity));
  }

  if (snapshot.terminal) {
    lines.push(
      '',
      snapshot.status === 'FINISHED'
        ? '_Finished — review PR/branch above, then publish from Founder OS._'
        : '_Run ended — see output above._',
    );
  } else {
    lines.push('', '_Working… updates stream here every few seconds._');
  }

  if (snapshot.agentUrl) {
    lines.push('', `_Optional:_ [Open full session in Cursor](${snapshot.agentUrl})`);
  }

  return lines.join('\n');
}

export function formatOpenHandsRunInChat(input: {
  workerLabel: string;
  task: string;
  repo?: string | null;
  snapshot: OpenHandsRunSnapshot;
}): string {
  const { task, repo, snapshot } = input;
  const lines: string[] = [
    '**Builder Agent** · OpenHands',
    repo ? `**Repository:** \`${repo}\`` : '',
    '',
    '**Task**',
    task.trim().slice(0, 1200),
    '',
    `**Status:** ${snapshot.status}`,
  ].filter(Boolean);

  if (snapshot.result?.trim()) {
    lines.push('', '**Agent output**', snapshot.result.trim().slice(0, 4000));
  }

  if (snapshot.terminal) {
    lines.push('', '_OpenHands run finished — review output above._');
  } else {
    lines.push('', '_Streaming OpenHands output here…_');
  }

  return lines.join('\n');
}

export const CURSOR_TERMINAL_STATUSES = new Set(['FINISHED', 'ERROR', 'CANCELLED', 'EXPIRED']);

export function isBuilderRunTerminal(status: string): boolean {
  return CURSOR_TERMINAL_STATUSES.has(status.toUpperCase());
}

export async function pollRunInChat<T extends { terminal?: boolean; status: string }>(
  fetchSnapshot: () => Promise<T>,
  onUpdate: (snapshot: T) => void,
  isTerminal: (status: string, snap: T) => boolean,
  maxAttempts = 45,
  intervalMs = 2500,
): Promise<T> {
  let last = await fetchSnapshot();
  onUpdate(last);
  for (let i = 0; i < maxAttempts; i++) {
    if (last.terminal ?? isTerminal(last.status, last)) break;
    await new Promise((r) => setTimeout(r, intervalMs));
    last = await fetchSnapshot();
    onUpdate(last);
  }
  return last;
}

export async function pollCursorRunInChat(
  agentId: string,
  runId: string,
  token: string,
  fetchRun: (agentId: string, runId: string, token: string) => Promise<BuilderRunSnapshot>,
  onUpdate: (snapshot: BuilderRunSnapshot) => void,
  maxAttempts = 45,
  intervalMs = 2500,
): Promise<BuilderRunSnapshot> {
  return pollRunInChat(
    () => fetchRun(agentId, runId, token),
    onUpdate,
    (status) => isBuilderRunTerminal(status),
    maxAttempts,
    intervalMs,
  );
}

export async function pollOpenHandsRunInChat(
  conversationId: string,
  token: string,
  fetchRun: (conversationId: string, token: string) => Promise<OpenHandsRunSnapshot>,
  onUpdate: (snapshot: OpenHandsRunSnapshot) => void,
  maxAttempts = 40,
  intervalMs = 3000,
): Promise<OpenHandsRunSnapshot> {
  return pollRunInChat(
    () => fetchRun(conversationId, token),
    onUpdate,
    (status, snap) => Boolean(snap.terminal) || ['COMPLETED', 'FINISHED', 'ERROR', 'FAILED', 'CANCELLED', 'STOPPED', 'DONE'].includes(status.toUpperCase()),
    maxAttempts,
    intervalMs,
  );
}
