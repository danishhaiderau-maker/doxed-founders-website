import type { DesktopBridgeSnapshot } from './desktop-bridge';

export type LiveFounderContextSnapshot = {
  collectedAt: string;
  repoFullName: string | null;
  githubLinked: boolean;
  branch: string | null;
  recentCommitCount: number;
  latestCommitMessage: string | null;
  openFileCount: number;
  openFileNames: string[];
  founderNodeOnline: boolean;
  cursorConnected: boolean;
  deployCardCount: number;
  vaultSynced: boolean;
  sourcesConsulted: string[];
};

export type ContextCollectionStep = {
  id: string;
  label: string;
  status: 'done' | 'skipped';
};

export function buildLiveFounderContextSnapshot(input: {
  repoFullName: string | null;
  commits: { message: string }[];
  desktop: DesktopBridgeSnapshot | null;
  deployCardCount: number;
  founderNodeOnline?: boolean;
  cursorConnected?: boolean;
  vaultSynced?: boolean;
}): LiveFounderContextSnapshot {
  const githubLinked = Boolean(input.repoFullName?.trim());
  const latest = input.commits[0]?.message?.split('\n')[0]?.trim() ?? null;
  const openFileNames = input.desktop?.openFilePaths ?? [];
  const sourcesConsulted: string[] = ['mission memory'];
  if (githubLinked) sourcesConsulted.push('GitHub');
  if (input.desktop) sourcesConsulted.push('Desktop bridge');
  if (input.deployCardCount > 0) sourcesConsulted.push('Deployments');
  if (input.founderNodeOnline) sourcesConsulted.push('Founder Node');
  if (input.vaultSynced) sourcesConsulted.push('Founder Vault');

  return {
    collectedAt: new Date().toISOString(),
    repoFullName: input.repoFullName,
    githubLinked,
    branch: input.desktop?.branch ?? null,
    recentCommitCount: input.commits.length,
    latestCommitMessage: latest,
    openFileCount: openFileNames.length,
    openFileNames: openFileNames.slice(0, 12),
    founderNodeOnline: Boolean(input.founderNodeOnline),
    cursorConnected: Boolean(input.cursorConnected),
    deployCardCount: input.deployCardCount,
    vaultSynced: Boolean(input.vaultSynced),
    sourcesConsulted,
  };
}

export function buildContextCollectionSteps(snapshot: LiveFounderContextSnapshot): ContextCollectionStep[] {
  const steps: ContextCollectionStep[] = [
    {
      id: 'repo',
      label: snapshot.githubLinked
        ? `Repository linked — ${snapshot.repoFullName}`
        : 'Repository not linked in Builder settings',
      status: snapshot.githubLinked ? 'done' : 'skipped',
    },
  ];

  if (snapshot.githubLinked) {
    steps.push({
      id: 'git',
      label:
        snapshot.recentCommitCount > 0
          ? `Git — ${snapshot.recentCommitCount} recent commit(s)${snapshot.latestCommitMessage ? `: ${snapshot.latestCommitMessage.slice(0, 60)}` : ''}`
          : 'Git — linked, no recent commits synced yet',
      status: 'done',
    });
  }

  if (snapshot.branch || snapshot.openFileCount > 0) {
    steps.push({
      id: 'desktop',
      label: snapshot.openFileCount
        ? `Desktop bridge — branch ${snapshot.branch ?? 'unknown'}, ${snapshot.openFileCount} open file(s)`
        : `Desktop bridge — branch ${snapshot.branch ?? 'unknown'}`,
      status: 'done',
    });
  } else {
    steps.push({
      id: 'desktop',
      label: 'Desktop bridge — no live IDE metadata (pair Founder Node)',
      status: 'skipped',
    });
  }

  steps.push({
    id: 'deploy',
    label:
      snapshot.deployCardCount > 0
        ? `Deployments — ${snapshot.deployCardCount} recent deploy(s)`
        : 'Deployments — none in last 30 days',
    status: snapshot.deployCardCount > 0 ? 'done' : 'skipped',
  });

  steps.push({
    id: 'vault',
    label: snapshot.vaultSynced
      ? 'Founder Vault — synced'
      : 'Founder Vault — waiting for Founder Node sync',
    status: snapshot.vaultSynced ? 'done' : 'skipped',
  });

  return steps;
}

/** Ground-truth block injected ahead of stale vault/mission text. */
export function formatLiveContextGroundTruthBlock(snapshot: LiveFounderContextSnapshot): string {
  const lines = [
    '## LIVE PROJECT SNAPSHOT (ground truth — prefer this over older vault notes or mission memory)',
    `Collected: ${snapshot.collectedAt}`,
    `GitHub repo linked: ${snapshot.githubLinked ? 'yes' : 'no'}${snapshot.repoFullName ? ` (\`${snapshot.repoFullName}\`)` : ''}`,
  ];

  if (snapshot.githubLinked) {
    lines.push(`Recent commits available: ${snapshot.recentCommitCount}`);
    if (snapshot.latestCommitMessage) {
      lines.push(`Latest commit subject: ${snapshot.latestCommitMessage.slice(0, 120)}`);
    }
    lines.push(
      'Do NOT tell the user to "clone the repo" or that "no GitHub sync" exists when GitHub is linked and commits are present.',
    );
  } else if (snapshot.branch || snapshot.openFileCount > 0) {
    lines.push(
      `Local IDE active: branch ${snapshot.branch ?? 'unknown'}, ${snapshot.openFileCount} open file(s) via Founder Node.`,
    );
    lines.push(
      'Do NOT claim "no local repo" when desktop bridge shows an active branch or open files. Say GitHub linking is optional if local/Founder Node workflow is active.',
    );
  }

  if (snapshot.openFileNames.length) {
    lines.push(`Open files (names only): ${snapshot.openFileNames.join(', ')}`);
  }
  if (snapshot.founderNodeOnline) lines.push('Founder Node: online');
  if (snapshot.cursorConnected) lines.push('Cursor integration: connected');
  if (snapshot.deployCardCount > 0) {
    lines.push(`Recent deployments tracked: ${snapshot.deployCardCount}`);
  }

  lines.push(`Sources consulted: ${snapshot.sourcesConsulted.join(', ')}`);
  return lines.join('\n');
}

export function formatContextEvidenceFooter(snapshot: LiveFounderContextSnapshot): string {
  const rows = [
    snapshot.repoFullName ? `Repository: ${snapshot.repoFullName} ✓` : 'Repository: not linked',
    snapshot.branch ? `Branch: ${snapshot.branch} ✓` : null,
    snapshot.recentCommitCount > 0 ? `Git: ${snapshot.recentCommitCount} commit(s) ✓` : 'Git: no recent commits',
    snapshot.openFileCount > 0 ? `Open files: ${snapshot.openFileCount} ✓` : null,
    snapshot.founderNodeOnline ? 'Founder Node: connected ✓' : null,
    snapshot.cursorConnected ? 'Cursor: connected ✓' : null,
    snapshot.deployCardCount > 0 ? `Deploys: ${snapshot.deployCardCount} ✓` : null,
  ].filter(Boolean);
  return `\n\n---\n**Live scan** (${new Date(snapshot.collectedAt).toLocaleTimeString()})\n${rows.map((r) => `- ${r}`).join('\n')}\n_Based on live project state, not cached mission assumptions._`;
}
