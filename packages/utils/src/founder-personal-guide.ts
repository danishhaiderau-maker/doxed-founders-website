export type FounderPhase = 'research' | 'building' | 'production';
export type FounderPriority = 'save_money' | 'ship_fast' | 'privacy';

export type InfraGap = {
  key: string;
  label: string;
  why: string;
  connectHref: string;
  guideKey?: string;
};

export type InfrastructurePathway = {
  id: 'sovereign' | 'hybrid' | 'cloud';
  optionLabel: string;
  title: string;
  tagline: string;
  bestFor: string;
  costNote: string;
  requires: InfraGap[];
  steps: string[];
};

export const FOUNDER_PHASE_OPTIONS: { id: FounderPhase; label: string; hint: string }[] = [
  {
    id: 'research',
    label: 'Research & plan',
    hint: 'Validate idea, competitor briefs, tokenomics — not ready for cloud bills yet',
  },
  {
    id: 'building',
    label: 'Build & iterate',
    hint: 'Ship code, sync GitHub, run agents on your repo',
  },
  {
    id: 'production',
    label: 'Go live',
    hint: 'Deploy web + API + database for real users',
  },
];

export const FOUNDER_PRIORITY_OPTIONS: { id: FounderPriority; label: string; hint: string }[] = [
  {
    id: 'save_money',
    label: 'Save money',
    hint: 'Keep infra local / free tier until traction',
  },
  {
    id: 'ship_fast',
    label: 'Ship fast',
    hint: 'Connect cloud now — Neon, Railway, Vercel',
  },
  {
    id: 'privacy',
    label: 'Privacy / Sovereign',
    hint: 'Vault + Founder Node on your machine first',
  },
];

const GITHUB_GAP: InfraGap = {
  key: 'github',
  label: 'GitHub',
  why: 'Tracks commits, powers Cursor builds, and gives Founder Brain real progress signals — not generic task boilerplate.',
  connectHref: '/account?tab=connected&connect=github',
  guideKey: 'github',
};

const NEON_GAP: InfraGap = {
  key: 'neon',
  label: 'Neon Postgres',
  why: 'Production database for users, wallets, and founder data when you leave research mode.',
  connectHref: '/account?tab=connected&connect=neon',
  guideKey: 'neon',
};

const RAILWAY_GAP: InfraGap = {
  key: 'railway',
  label: 'Railway',
  why: 'Hosts your API and long-running agents when you are ready for 24/7 uptime.',
  connectHref: '/account?tab=connected&connect=railway',
  guideKey: 'railway',
};

const VERCEL_GAP: InfraGap = {
  key: 'vercel',
  label: 'Vercel',
  why: 'Deploys the web app and receives ship webhooks into your build feed.',
  connectHref: '/account?tab=connected&connect=vercel',
  guideKey: 'vercel',
};

const LLM_GAP: InfraGap = {
  key: 'llm',
  label: 'Chat LLM (Gemini / DeepSeek)',
  why: 'Founder Brain needs a connected model for tailored answers — promo models work during your first month.',
  connectHref: '/settings/builder#connect-ai',
};

const CURSOR_GAP: InfraGap = {
  key: 'cursor',
  label: 'Cursor code agent',
  why: 'Implements tasks in your GitHub repo — PRs, fixes, and scaffold code.',
  connectHref: '/settings/builder#remote-builder',
  guideKey: 'cursor',
};

export const INFRASTRUCTURE_PATHWAYS: InfrastructurePathway[] = [
  {
    id: 'sovereign',
    optionLabel: 'Option A',
    title: 'Sovereign research stack',
    tagline: 'Local vault + promo LLMs — no Neon/Railway until you choose',
    bestFor: 'Idea phase, competitor research, saving cash before launch',
    costNote: 'Lowest cost: vault on your laptop, platform promo LLMs, defer cloud DB/hosting.',
    requires: [LLM_GAP],
    steps: [
      'Use Founder Vault on your machine for goals, roadmap, and private notes',
      'Ask Gemini or DeepSeek (Promo) for research — no repo required yet',
      'When ready to code, add GitHub and Cursor — still skip Neon/Railway until go-live',
    ],
  },
  {
    id: 'hybrid',
    optionLabel: 'Option B',
    title: 'Hybrid build stack',
    tagline: 'GitHub + vault + agents now — cloud when you flip the switch',
    bestFor: 'Active building with agents while keeping cloud bills optional',
    costNote: 'Pay for Cursor/API usage; keep Neon/Railway disconnected until production.',
    requires: [GITHUB_GAP, LLM_GAP, CURSOR_GAP],
    steps: [
      'Connect GitHub (owner/repo) so commits sync into Mission Control',
      'Connect Cursor (or promo) for Build with Cursor in chat',
      'Keep vault local; add Neon + Railway only when you answer “go live”',
    ],
  },
  {
    id: 'cloud',
    optionLabel: 'Option C',
    title: 'Full production stack',
    tagline: 'GitHub + Neon + Railway + Vercel — same path we use on doxxedcrypto.digital',
    bestFor: 'Ready for real users, webhooks, and 24/7 API',
    costNote: 'Neon + Railway + Vercel free tiers often cover early launch — connect when shipping.',
    requires: [GITHUB_GAP, NEON_GAP, RAILWAY_GAP, VERCEL_GAP, LLM_GAP, CURSOR_GAP],
    steps: [
      'Link GitHub repo for commit intelligence',
      'Connect Neon (database), Railway (API), Vercel (web)',
      'Use Take full control to sync vault config toward cloud when past research',
    ],
  },
];

export function recommendPathway(
  phase: FounderPhase,
  priority: FounderPriority,
): InfrastructurePathway {
  if (phase === 'research' || priority === 'save_money' || priority === 'privacy') {
    return INFRASTRUCTURE_PATHWAYS.find((p) => p.id === 'sovereign')!;
  }
  if (phase === 'production' || priority === 'ship_fast') {
    return INFRASTRUCTURE_PATHWAYS.find((p) => p.id === 'cloud')!;
  }
  return INFRASTRUCTURE_PATHWAYS.find((p) => p.id === 'hybrid')!;
}

export function listMissingRequirements(
  pathway: InfrastructurePathway,
  connected: Record<string, boolean>,
): InfraGap[] {
  return pathway.requires.filter((req) => {
    if (req.key === 'llm') return !connected.llm;
    return !connected[req.key];
  });
}

export function buildGuidePrompt(pathway: InfrastructurePathway, phase: FounderPhase): string {
  return [
    `I chose ${pathway.optionLabel} — ${pathway.title}.`,
    `My phase: ${phase}.`,
    'Walk me through the exact next setup steps for my stack and what I can skip for now.',
  ].join(' ');
}
