import { groupCommitsByInitiative, type CommitSignal } from './commit-intelligence.js';

export type OutcomeImpact = {
  discovery?: string;
  engagement?: string;
  trust?: string;
  revenue?: string;
  summary: string;
};

const THEME_IMPACT: Record<
  string,
  { discovery?: string; engagement?: string; trust?: string; revenue?: string }
> = {
  discover: {
    discovery: 'Improves project discovery and listing visibility',
    engagement: 'More scouts and traders can find relevant projects',
  },
  feed: {
    engagement: 'Strengthens money-signal feed and trader retention',
    revenue: 'Supports DDollar trading activity on the platform',
  },
  founder_os: {
    engagement: 'Improves founder workflow and ship velocity',
    trust: 'Reinforces build-in-public credibility',
  },
  vault: {
    trust: 'Strengthens privacy posture and founder data ownership',
  },
  predictions: {
    engagement: 'Expands conviction markets and scout participation',
    revenue: 'More prediction and stake activity on platform',
  },
  rewards: {
    engagement: 'Incentivizes builder contributions and community growth',
    revenue: 'Ties DDollar economy to shipped work',
  },
  mobile: {
    discovery: 'Extends Founder OS to mobile founders',
    engagement: 'Vault sync on device increases daily active use',
  },
  builder: {
    engagement: 'Accelerates code shipping via Builder Agent',
    trust: 'Visible PRs and deploys increase transparency',
  },
  security: {
    trust: 'Reduces risk and increases platform credibility',
  },
  product: {
    engagement: 'Advances core product capabilities',
  },
};

/** Heuristic commit → user/revenue/trust impact (no LLM). */
export function inferOutcomeImpact(
  commitMessage: string,
  themeKey?: string,
): OutcomeImpact {
  const msg = commitMessage.trim();
  const themes = groupCommitsByInitiative([{ sha: '', message: msg }]);
  const key = themeKey ?? themes[0]?.key ?? 'product';
  const base = THEME_IMPACT[key] ?? THEME_IMPACT.product!;

  const feat = msg.replace(/^(feat|fix|add|implement|ship)[:(\s]+/i, '').slice(0, 100);
  const summaryParts = [feat || msg.slice(0, 80)];
  if (base.discovery) summaryParts.push(base.discovery);
  else if (base.engagement) summaryParts.push(base.engagement);
  else if (base.trust) summaryParts.push(base.trust);

  return {
    ...base,
    summary: summaryParts.filter(Boolean).join(' — '),
  };
}

export function formatOutcomeIntelligenceExcerpt(
  commits: CommitSignal[],
  max = 6,
): string | null {
  const themes = groupCommitsByInitiative(commits);
  if (themes.length === 0) return null;

  const lines = ['## Outcome intelligence (commit → impact)'];
  for (const theme of themes.slice(0, max)) {
    const sample = theme.samples[0];
    if (!sample) continue;
    const impact = inferOutcomeImpact(sample, theme.key);
    lines.push(`- **${theme.label}**: ${impact.summary.slice(0, 160)}`);
  }
  return lines.length > 1 ? lines.join('\n') : null;
}
