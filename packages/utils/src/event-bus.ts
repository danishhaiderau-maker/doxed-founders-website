export const EVENT_SOURCE_LABELS: Record<string, string> = {
  github: 'GitHub',
  vercel: 'Vercel',
  railway: 'Railway',
  neon: 'Neon',
  supabase: 'Supabase',
  digitalocean: 'DigitalOcean',
  cursor: 'Cursor',
  'founder-os': 'Founder OS',
  agent: 'Agent',
  copilot: 'Founder Copilot',
};

export type CopilotIntent =
  | 'weekly_summary'
  | 'community_update'
  | 'launch_report'
  | 'publish_progress'
  | 'what_happened';

export type HandsFreeAction =
  | 'quick_build'
  | 'weekly_summary'
  | 'publish_progress'
  | 'create_github_issues'
  | 'launch_report'
  | 'roadmap'
  | 'community_update'
  | 'resume_work'
  | 'cursor_dispatch';

/** Detect when the user wants Copilot to dispatch Cursor Cloud directly. */
export function detectCursorDispatchIntent(prompt: string): boolean {
  const p = prompt.trim();
  if (!p) return false;
  return (
    /\b(command|run|dispatch|start|use)\s+cursor\b/i.test(p) ||
    /\bcursor\s+(build|run|fix|implement|ship)\b/i.test(p) ||
    /^cursor[:\s]/i.test(p)
  );
}

export function detectHandsFreeAction(prompt: string): HandsFreeAction {
  const p = prompt.toLowerCase();
  if (detectCursorDispatchIntent(prompt)) return 'cursor_dispatch';
  if (/^(finish|continue|resume|where i left|pick up)/.test(p.trim()) || /\bfinish it\b/.test(p)) {
    return 'resume_work';
  }
  if (/publish|push.*(community|everywhere|progress|update)/.test(p)) return 'publish_progress';
  if (/community update|announce.*community/.test(p)) return 'community_update';
  if (/github issue|create issue/.test(p)) return 'create_github_issues';
  if (/launch readiness|launch report|go.?live/.test(p)) return 'launch_report';
  if (/roadmap|quarter|phase/.test(p)) return 'roadmap';
  if (/weekly|this week|summary|recap/.test(p)) return 'weekly_summary';
  return 'quick_build';
}

export function buildWeeklySummary(input: {
  projectName: string;
  commitCount: number;
  deployCount: number;
  followerCount: number;
  featureRequests: number;
  launchReadiness: number;
  launchReadinessDelta?: number;
  buildStreak: number;
  recentHeadlines: string[];
}): { title: string; body: string; traderView: string } {
  const delta =
    input.launchReadinessDelta != null && input.launchReadinessDelta !== 0
      ? ` Launch readiness ${input.launchReadinessDelta > 0 ? '+' : ''}${input.launchReadinessDelta}%.`
      : '';

  const highlights =
    input.recentHeadlines.length > 0
      ? input.recentHeadlines.slice(0, 4).map((h) => `• ${h}`).join('\n')
      : '• Building in public on Founder OS';

  return {
    title: `This week on ${input.projectName}`,
    body: [
      `${input.commitCount} commits`,
      `${input.deployCount} deployments`,
      `${input.followerCount} project followers`,
      `${input.featureRequests} open feature requests`,
      `Launch readiness ${input.launchReadiness}%${delta}`,
      `Build streak: ${input.buildStreak} days`,
      '',
      'Highlights:',
      highlights,
    ].join('\n'),
    traderView: [
      `✓ ${input.commitCount} commits shipped — active development`,
      `✓ ${input.deployCount} deploys — product moving to production`,
      `✓ Launch readiness at ${input.launchReadiness}%`,
    ].join('\n'),
  };
}

export function buildCommunityUpdateFromSummary(summary: {
  title: string;
  body: string;
  traderView: string;
}): string {
  return [summary.title, '', summary.body, '', '---', summary.traderView].join('\n');
}
