import { buildSuggestionFromBuildPrompt } from './cursor-build-room';

export const AGENT_RUN_CREDITS = 15;

export const AGENT_CATEGORY_LABELS: Record<string, string> = {
  RESEARCH: 'Research',
  TRADING: 'Trading',
  COMMUNITY: 'Community',
  SUPPORT: 'Customer support',
  MARKETING: 'Marketing',
  TOKENOMICS: 'Tokenomics',
  AUDIT: 'Audit',
  BUILDER: 'Builder',
  FUNDRAISING: 'Fundraising',
  LAUNCH: 'Launch',
};

export const WORKFORCE_TEMPLATES: {
  key: string;
  label: string;
  category: string;
  description: string;
}[] = [
  { key: 'PRODUCT_MANAGER', label: 'Product Manager', category: 'BUILDER', description: 'Specs, roadmap, and task breakdown' },
  { key: 'RESEARCHER', label: 'Researcher', category: 'RESEARCH', description: 'Competitor and market research briefs' },
  { key: 'BUILDER', label: 'Builder', category: 'BUILDER', description: 'Code plans and GitHub-ready tasks' },
  { key: 'MARKETER', label: 'Marketer', category: 'MARKETING', description: 'Posts, threads, and launch copy' },
  { key: 'COMMUNITY_MANAGER', label: 'Community Manager', category: 'COMMUNITY', description: 'FAQ replies and community updates' },
  { key: 'FUNDRAISING', label: 'Fundraising Agent', category: 'FUNDRAISING', description: 'Demand signals and raise readiness' },
  { key: 'LAUNCH', label: 'Launch Agent', category: 'LAUNCH', description: 'Launch checklist and readiness score' },
];

export function agentRating(ratingSum: number, ratingCount: number): number {
  if (ratingCount === 0) return 0;
  return Math.round((ratingSum / ratingCount) * 10) / 10;
}

export function runWorkforceAgent(
  template: string,
  prompt: string,
  projectName?: string,
): {
  title: string;
  summary: string;
  tasks: string[];
  githubIssues: string[];
  buildPlan: string[];
  traderView: string;
} {
  const name = projectName ?? 'your project';
  const lines = prompt.trim().split(/\n+/).filter(Boolean);
  const goal = lines[0] ?? prompt.trim();

  switch (template) {
    case 'PRODUCT_MANAGER':
      return {
        title: `Spec: ${goal.slice(0, 72)}`,
        summary: `Product spec for ${name}. Scope derived from founder prompt — review in Founder Copilot.`,
        tasks: [
          'Define MVP user stories',
          'List acceptance criteria',
          'Prioritize P0 vs P1',
          'Map to Founder OS publish cadence',
        ],
        githubIssues: [`[Spec] ${goal.slice(0, 60)}`, '[Tasks] Break down MVP milestones'],
        buildPlan: ['Week 1: Spec + design', 'Week 2: MVP core', 'Week 3: Community beta'],
        traderView: `Founder is formalizing the roadmap for ${name} — clearer delivery timeline.`,
      };
    case 'RESEARCHER':
      return {
        title: `Research brief: ${goal.slice(0, 72)}`,
        summary: `Competitive landscape and demand signals for ${name}.`,
        tasks: ['Identify 5 comparable products', 'Summarize pricing models', 'Note community gaps'],
        githubIssues: ['[Research] Competitor matrix doc'],
        buildPlan: ['Desk research', 'Community poll', 'Synthesize findings'],
        traderView: 'Team is validating market before heavy build spend.',
      };
    case 'MARKETER':
      return {
        title: `Campaign: ${goal.slice(0, 72)}`,
        summary: `Marketing angles for ${name} — ready to publish everywhere.`,
        tasks: ['Draft X thread (3 tweets)', 'Build feed headline', 'Community announcement'],
        githubIssues: [],
        buildPlan: ['Hook', 'Proof points', 'CTA to project room'],
        traderView: 'Founder sharpening public narrative — visibility increasing.',
      };
    case 'COMMUNITY_MANAGER':
      return {
        title: `Community playbook: ${goal.slice(0, 72)}`,
        summary: `Responses and pinned FAQ for ${name} project room.`,
        tasks: ['Draft welcome message', 'FAQ for top 5 questions', 'Weekly office hours outline'],
        githubIssues: [],
        buildPlan: ['General channel pin', 'Feature request triage', 'Contributor thanks'],
        traderView: 'Community layer getting structured — lower support friction.',
      };
    case 'FUNDRAISING':
      return {
        title: `Demand check: ${goal.slice(0, 72)}`,
        summary: `Simulated raise readiness for ${name}.`,
        tasks: ['Run demand poll copy', 'Allocator talking points', 'Launch readiness gaps'],
        githubIssues: ['[Fundraising] Demand validation checklist'],
        buildPlan: ['Poll → allocate → iterate', 'Track conviction score'],
        traderView: 'Founder testing demand before token narrative hardens.',
      };
    case 'LAUNCH':
      return {
        title: `Launch readiness: ${goal.slice(0, 72)}`,
        summary: `Pre-launch checklist for ${name}.`,
        tasks: ['Security review items', 'Deploy verification', 'Comms timeline'],
        githubIssues: ['[Launch] Go-live checklist'],
        buildPlan: ['T-7 audit', 'T-3 deploy', 'T-0 publish everywhere'],
        traderView: 'Launch discipline visible — reduces last-minute surprises.',
      };
    case 'BUILDER':
    default: {
      const built = buildSuggestionFromBuildPrompt(prompt);
      return {
        title: built.headline,
        summary: built.devSummary,
        tasks: lines.slice(1).length ? lines.slice(1) : [goal],
        githubIssues: [`[Build] ${goal.slice(0, 55)}`],
        buildPlan: ['Open Founder Copilot', 'Sync GitHub', 'Publish everywhere'],
        traderView: built.traderSummary,
      };
    }
  }
}
