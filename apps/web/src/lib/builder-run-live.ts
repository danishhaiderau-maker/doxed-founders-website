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
};

export type OpenHandsRunSnapshot = {
  conversationId: string;
  status: string;
  result?: string | null;
  terminal?: boolean;
  conversationUrl?: string | null;
};

export function formatBuilderRunInChat(input: {
  workerLabel: string;
  task: string;
  repo?: string | null;
  snapshot: BuilderRunSnapshot;
  mode?: string;
}): string {
  const { workerLabel, task, repo, snapshot, mode } = input;
  const lines: string[] = [
    `**${workerLabel}** · ${mode === 'follow_up' ? 'continuing on repo' : 'coding on your repo'}`,
    repo ? `Repository: \`${repo}\`` : '',
    '',
    `**Your task**`,
    task.trim().slice(0, 1200),
    '',
    `**Status:** ${snapshot.status}`,
  ].filter(Boolean);

  if (snapshot.result?.trim()) {
    lines.push('', '**Agent output**', snapshot.result.trim());
  }

  const branches = snapshot.git?.branches ?? [];
  if (branches.length > 0) {
    lines.push('', '**Git**');
    for (const b of branches) {
      const ref = [b.repoUrl, b.branch].filter(Boolean).join(' · ');
      if (b.prUrl) lines.push(`- PR: ${b.prUrl}`);
      else if (ref) lines.push(`- ${ref}`);
    }
  }

  if (snapshot.terminal) {
    lines.push(
      '',
      snapshot.status === 'FINISHED'
        ? '_Run finished — review the branch/PR, commit from GitHub, then publish from Founder OS._'
        : '_Run ended — details below._',
    );
  } else {
    lines.push('', '_Live output streams in this chat — no need to open another tab._');
  }

  return lines.join('\n');
}

export function formatOpenHandsRunInChat(input: {
  workerLabel: string;
  task: string;
  repo?: string | null;
  snapshot: OpenHandsRunSnapshot;
}): string {
  const { workerLabel, task, repo, snapshot } = input;
  const lines: string[] = [
    `**${workerLabel}** · coding on your repo`,
    repo ? `Repository: \`${repo}\`` : '',
    '',
    `**Your task**`,
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
