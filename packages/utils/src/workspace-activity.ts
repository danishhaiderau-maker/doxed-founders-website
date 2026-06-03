export type WorkspaceCommit = {
  sha: string;
  message: string;
  date: string;
  branch?: string;
};

export type WorkspaceActivity = {
  repoFullName: string | null;
  defaultBranch: string;
  syncedAt: string;
  commitsLast24h: WorkspaceCommit[];
  commitsLast2h: WorkspaceCommit[];
  cursorBranchCommits: WorkspaceCommit[];
  /** Shown when GitHub has recent pushes but agent output claims silence */
  localWorkHint?: string;
};

const TWO_HOURS_MS = 2 * 60 * 60 * 1000;

export function filterCommitsSince(commits: WorkspaceCommit[], sinceMs: number): WorkspaceCommit[] {
  const cutoff = Date.now() - sinceMs;
  return commits.filter((c) => new Date(c.date).getTime() >= cutoff);
}

export function formatWorkspaceActivityForPrompt(activity: WorkspaceActivity): string {
  if (!activity.repoFullName) {
    return [
      '## Workspace sync (Founder OS)',
      'No GitHub repo linked — connect repo in Integrations so Copilot and Cursor share the same ground truth.',
    ].join('\n');
  }

  const lines = [
    '## Workspace sync (Founder OS — GitHub ground truth)',
    `Repository: \`${activity.repoFullName}\` · default branch \`${activity.defaultBranch}\``,
    `Synced at: ${activity.syncedAt}`,
    '',
  ];

  const recent2h = activity.commitsLast2h;
  if (recent2h.length > 0) {
    lines.push('### Commits pushed in the last 2 hours (all tracked branches)');
    for (const c of recent2h.slice(0, 12)) {
      const branch = c.branch ? ` · \`${c.branch}\`` : '';
      lines.push(`- \`${c.sha}\` ${c.message}${branch} (${c.date})`);
    }
    lines.push('');
  } else {
    lines.push(
      '### Commits in the last 2 hours',
      '_None on GitHub yet._ If the founder is coding in Cursor desktop, changes only appear here after **git push**.',
      '',
    );
  }

  if (activity.commitsLast24h.length > 0 && recent2h.length === 0) {
    lines.push('### Last 24h on default branch (for context)');
    for (const c of activity.commitsLast24h.slice(0, 8)) {
      lines.push(`- \`${c.sha}\` ${c.message} (${c.date})`);
    }
    lines.push('');
  }

  lines.push(
    '**Important:** Cursor Cloud Agents only see **pushed** GitHub state. Do not claim "no work" if commits exist above. If none exist, say the founder may be working locally and should push to sync.',
  );

  return lines.join('\n');
}

export function formatWorkspaceActivityForChat(activity: WorkspaceActivity): string {
  if (!activity.repoFullName) {
    return '_Link GitHub in Integrations to sync commits with Copilot and Cursor._';
  }

  const lines = [
    `**GitHub sync** · \`${activity.repoFullName}\` (${activity.defaultBranch})`,
    `Last checked: ${new Date(activity.syncedAt).toLocaleString()}`,
  ];

  if (activity.commitsLast2h.length > 0) {
    lines.push('', '**Your recent pushes (last 2h)**');
    for (const c of activity.commitsLast2h.slice(0, 6)) {
      const branch = c.branch ? ` · ${c.branch}` : '';
      lines.push(`- \`${c.sha}\` ${c.message}${branch}`);
    }
  } else {
    lines.push(
      '',
      '_No commits on GitHub in the last 2 hours._ Coding in **Cursor desktop** only shows here after you **push**.',
    );
  }

  if (activity.localWorkHint) {
    lines.push('', activity.localWorkHint);
  }

  return lines.join('\n');
}

/** When Cursor Cloud result disagrees with GitHub, append a correction block. */
export function reconcileCursorAgentResult(
  agentResult: string | null | undefined,
  activity: WorkspaceActivity,
): string | null {
  if (!agentResult?.trim() || !activity.repoFullName) return null;

  const claimsQuiet =
    /no new commits|no commits|nothing new|past 2 hours|last 2 hours|no activity/i.test(agentResult);
  if (!claimsQuiet) return null;

  const hasRecent = activity.commitsLast2h.length > 0;
  if (!hasRecent && activity.commitsLast24h.length === 0) return null;

  if (hasRecent) {
    const list = activity.commitsLast2h
      .slice(0, 8)
      .map((c) => `- \`${c.sha}\` ${c.message}${c.branch ? ` (${c.branch})` : ''}`)
      .join('\n');
    return [
      '**Founder OS sync (GitHub)** — Cursor Cloud only inspected its agent branch; your repo **does** have recent pushes:',
      list,
      '',
      '_Push local Cursor IDE work to GitHub to keep Cloud Agents and this dashboard aligned._',
    ].join('\n');
  }

  return [
    '**Founder OS sync** — Cursor reported quiet activity on its branch. Your default branch may still have older commits; **push your latest local work** so Cloud Agents see it.',
    activity.commitsLast24h[0]
      ? `Latest on GitHub: \`${activity.commitsLast24h[0].sha}\` ${activity.commitsLast24h[0].message}`
      : '',
  ]
    .filter(Boolean)
    .join('\n');
}

export function mergeCommitsDeduped(...groups: WorkspaceCommit[][]): WorkspaceCommit[] {
  const seen = new Set<string>();
  const out: WorkspaceCommit[] = [];
  for (const group of groups) {
    for (const c of group) {
      const key = `${c.sha}:${c.branch ?? ''}`;
      if (seen.has(key)) continue;
      seen.add(key);
      out.push(c);
    }
  }
  return out.sort((a, b) => new Date(b.date).getTime() - new Date(a.date).getTime());
}

export { TWO_HOURS_MS };
