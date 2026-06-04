import type { CommitSignal } from './commit-intelligence.js';

export type DeployRisk = 'low' | 'medium' | 'high';

export type DeployIntelligenceCard = {
  id: string;
  at: string;
  title: string;
  provider: string | null;
  impact: string;
  affectedRoutes: string[];
  risk: DeployRisk;
  nextSteps: string[];
};

const ROUTE_HINTS: { pattern: RegExp; route: string }[] = [
  { pattern: /discover|listing|scout|bubble/i, route: '/discover' },
  { pattern: /feed|money\s*feed|terminal/i, route: '/feed' },
  { pattern: /founder\s*os|mission\s*control|copilot|command\s*center/i, route: '/founder-den' },
  { pattern: /vault|founder\s*node|encrypt/i, route: '/settings/builder' },
  { pattern: /predict|market|oracle/i, route: '/scout-votes' },
  { pattern: /auth|login|register|webauthn/i, route: '/login' },
  { pattern: /mobile|android|capacitor/i, route: '/mobile' },
  { pattern: /api\b|nestjs|railway/i, route: 'API' },
];

export function inferAffectedRoutes(text: string): string[] {
  const routes = new Set<string>();
  for (const { pattern, route } of ROUTE_HINTS) {
    if (pattern.test(text)) routes.add(route);
  }
  return [...routes].slice(0, 6);
}

export function inferDeployRisk(commitText: string, deployTitle: string): DeployRisk {
  const blob = `${deployTitle}\n${commitText}`.toLowerCase();
  if (/breaking|migration|schema\s*change|drop\s*table|destructive/i.test(blob)) return 'high';
  if (/migration|prisma|database|env\s*var|auth\s*change/i.test(blob)) return 'medium';
  return 'low';
}

export function buildDeployIntelligenceCard(input: {
  id: string;
  at: string;
  title: string;
  provider?: string | null;
  commitsSinceDeploy: CommitSignal[];
}): DeployIntelligenceCard {
  const commitBlob = input.commitsSinceDeploy.map((c) => c.message).join('\n');
  const routes = inferAffectedRoutes(`${input.title}\n${commitBlob}`);
  const risk = inferDeployRisk(commitBlob, input.title);

  const themes = routes.length > 0 ? routes.join(', ') : 'platform surfaces';
  const impact =
    routes.length > 0
      ? `Changes likely touch ${themes}`
      : 'Production deploy — review recent commits for scope';

  const nextSteps: string[] = [];
  if (routes.includes('/discover')) nextSteps.push('Smoke test /discover and listing cards');
  if (routes.includes('/feed')) nextSteps.push('Verify Money Feed and terminal cards');
  if (routes.includes('/founder-den')) nextSteps.push('Open Mission Control and run a Brain prompt');
  if (risk !== 'low') nextSteps.push('Check DB migrations and env before announcing');
  if (nextSteps.length === 0) nextSteps.push('Run platform smoke tests', 'Publish a founder update if user-facing');

  return {
    id: input.id,
    at: input.at,
    title: input.title.slice(0, 160),
    provider: input.provider ?? null,
    impact,
    affectedRoutes: routes,
    risk,
    nextSteps: nextSteps.slice(0, 4),
  };
}

export function formatDeployIntelligenceExcerpt(cards: DeployIntelligenceCard[], max = 4): string {
  if (cards.length === 0) return '- No deploy intelligence in window';
  return cards
    .slice(0, max)
    .map((c) => {
      const routes = c.affectedRoutes.length ? c.affectedRoutes.join(', ') : 'general';
      return `- ${c.at.slice(0, 10)} · ${c.title} (${c.provider ?? 'deploy'}) · Impact: ${c.impact} · Routes: ${routes} · Risk: ${c.risk} · Next: ${c.nextSteps[0]}`;
    })
    .join('\n');
}
