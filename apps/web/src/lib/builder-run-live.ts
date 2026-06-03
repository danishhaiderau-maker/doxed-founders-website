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
        : '_Run ended — check details below or open Cursor for the full session._',
    );
  } else {
    lines.push('', '_Updating live in Mission Control…_');
  }

  return lines.join('\n');
}

export const CURSOR_TERMINAL_STATUSES = new Set(['FINISHED', 'ERROR', 'CANCELLED', 'EXPIRED']);

export function isBuilderRunTerminal(status: string): boolean {
  return CURSOR_TERMINAL_STATUSES.has(status.toUpperCase());
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
  let last: BuilderRunSnapshot = { id: runId, agentId, status: 'CREATING' };
  for (let i = 0; i < maxAttempts; i++) {
    last = await fetchRun(agentId, runId, token);
    onUpdate(last);
    if (last.terminal ?? isBuilderRunTerminal(last.status)) break;
    await new Promise((r) => setTimeout(r, intervalMs));
  }
  return last;
}
