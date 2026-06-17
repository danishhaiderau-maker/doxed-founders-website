/** Phase 1 — Founder Den onboarding paths (shared web + API). */

export type OnboardingPathId =
  | 'SOVEREIGN'
  | 'BYO_CLOUD'
  | 'MIGRATE_PRIVATE'
  | 'FREE_STARTER'
  | 'FOUNDER_CLOUD';

export type ComputePlaneModeId = 'LOCAL' | 'CLOUD' | 'HYBRID';

export type StarterPackId = 'render' | 'railway' | 'vercel_neon' | 'supabase';

export type OnboardingStepId =
  | 'path'
  | 'founder'
  | 'starter_pack'
  | 'github'
  | 'platform'
  | 'ai_stack'
  | 'goal'
  | 'founder_node'
  | 'migrate';

export type FounderPathDefinition = {
  id: OnboardingPathId;
  title: string;
  tagline: string;
  icon: string;
  computePlane: ComputePlaneModeId;
  stepOrder: OnboardingStepId[];
  playbook: { action: string; time?: string }[];
  topology: { memory: string; compute: string; publish: string };
};

export const ONBOARDING_PATHS: FounderPathDefinition[] = [
  {
    id: 'SOVEREIGN',
    title: 'Sovereign',
    tagline: 'Your machine. Your keys. No cloud required.',
    icon: '🛡️',
    computePlane: 'LOCAL',
    stepOrder: ['path', 'founder', 'founder_node', 'ai_stack', 'goal', 'github'],
    topology: {
      memory: 'Founder Vault (local)',
      compute: 'Founder Node + Ollama',
      publish: 'Off until you opt in',
    },
    playbook: [
      { action: 'Activate profile (name + one-line idea)', time: '30s' },
      { action: 'Install Founder Node → pair with a code from Settings', time: '2m' },
      { action: 'Memory → Founder Vault (Founder Node)', time: '15s' },
      { action: 'AI Stack → Ollama at http://127.0.0.1:11434 (optional)', time: '30s' },
      { action: 'Set current goal → ask Brain: “What’s my setup status?”', time: '30s' },
    ],
  },
  {
    id: 'BYO_CLOUD',
    title: 'Bring your cloud',
    tagline: 'Connect what you already use. Founder OS orchestrates.',
    icon: '☁️',
    computePlane: 'HYBRID',
    stepOrder: ['path', 'founder', 'platform', 'github', 'ai_stack', 'goal', 'founder_node'],
    topology: {
      memory: 'Platform or vault',
      compute: 'Your Vercel / Railway / Render',
      publish: 'Per-connector toggles',
    },
    playbook: [
      { action: 'Activate profile', time: '30s' },
      { action: 'Connect GitHub (OAuth or owner/repo + token)', time: '1m' },
      { action: 'Paste host URL + DB string in Settings → Builder', time: '2m' },
      { action: 'AI Stack → DeepSeek, OpenRouter, Cursor, or Jatevo', time: '1m' },
      { action: 'Validate green checks → Brain: “Summarize my stack”', time: '30s' },
    ],
  },
  {
    id: 'MIGRATE_PRIVATE',
    title: 'Migrate to private',
    tagline: 'Pull cloud data in, then work local or hybrid.',
    icon: '📥',
    computePlane: 'HYBRID',
    stepOrder: ['path', 'founder', 'github', 'migrate', 'founder_node', 'ai_stack', 'goal'],
    topology: {
      memory: 'Import → Founder Vault',
      compute: 'Local primary or mirror',
      publish: 'Off during import',
    },
    playbook: [
      { action: 'Activate profile → connect read-only GitHub + DB', time: '2m' },
      { action: 'Import wizard: repo clone + env mirror (large DBs: run overnight)', time: '5m+' },
      { action: 'Pair Founder Node → rebuild vector index', time: '2m' },
      { action: 'Choose local-primary or hybrid mirror', time: '30s' },
      { action: 'Brain: “What did we import?”', time: '30s' },
    ],
  },
  {
    id: 'FREE_STARTER',
    title: 'Free cloud starter',
    tagline: 'One platform, one OAuth — live URL in minutes.',
    icon: '🚀',
    computePlane: 'CLOUD',
    stepOrder: ['path', 'founder', 'starter_pack', 'github', 'ai_stack', 'goal', 'platform'],
    topology: {
      memory: 'Platform Postgres',
      compute: 'Starter pack host (recommended: Render)',
      publish: 'When you connect deploy URL',
    },
    playbook: [
      { action: 'Pick a starter pack (Render recommended for beginners)', time: '30s' },
      { action: 'Connect GitHub OAuth', time: '1m' },
      { action: 'Create Render web + Postgres (free tier — note 30-day DB expiry)', time: '3m' },
      { action: 'Paste DATABASE_URL + service URL into Founder OS', time: '1m' },
      { action: 'AI Stack → Jatevo, OpenRouter, or DeepSeek → set goal', time: '1m' },
    ],
  },
  {
    id: 'FOUNDER_CLOUD',
    title: 'Founder Cloud',
    tagline: 'Install Founder Node → personal founder cloud on your PC.',
    icon: '🏠',
    computePlane: 'LOCAL',
    stepOrder: ['path', 'founder', 'founder_node', 'ai_stack', 'goal', 'github'],
    topology: {
      memory: 'Local Postgres + vector + vault',
      compute: 'Founder Node stack (localhost)',
      publish: 'GitHub / Render when ready',
    },
    playbook: [
      { action: 'Activate profile → install Founder Node v0.4+', time: '2m' },
      { action: 'Enable Founder Cloud mode in tray → start local stack', time: '2m' },
      { action: 'Memory → Founder Vault; AI → Ollama or Jatevo', time: '1m' },
      { action: 'Open Mission Control at localhost — no GitHub required', time: '30s' },
      { action: 'When ready: Publish → GitHub / Render / marketplaces', time: 'later' },
    ],
  },
];

export const STARTER_PACKS: {
  id: StarterPackId;
  label: string;
  description: string;
  recommended?: boolean;
  freeTierNote: string;
  steps: string[];
}[] = [
  {
    id: 'render',
    label: 'Render all-in-one',
    description: 'GitHub + Render web + Render Postgres — one dashboard, no card for hobby tier.',
    recommended: true,
    freeTierNote:
      'Free web spins down after 15m idle; free Postgres expires in 30 days — upgrade before production.',
    steps: [
      'Connect GitHub OAuth in Founder OS',
      'render.com → New Web Service → connect repo → Free instance',
      'New → PostgreSQL → Free → copy DATABASE_URL into web service env',
      'Deploy → paste service URL in Settings → Builder',
    ],
  },
  {
    id: 'railway',
    label: 'Railway unified',
    description: 'GitHub + Railway for API, web, Postgres, and long-running bots in one project.',
    freeTierNote: '~$5 trial then ~$1/mo free credit — tight for API + DB + bot together.',
    steps: [
      'Connect GitHub in Founder OS',
      'railway.app → New Project → Deploy from GitHub',
      'Add PostgreSQL plugin → copy DATABASE_URL to services',
      'Paste public URL in Settings → Builder',
    ],
  },
  {
    id: 'vercel_neon',
    label: 'Vercel + Neon',
    description: 'Best Next.js DX — web on Vercel, database on Neon (two OAuth flows).',
    freeTierNote: 'Strong free tiers; you manage two providers instead of one.',
    steps: [
      'Connect GitHub OAuth',
      'Import repo on vercel.com → deploy',
      'console.neon.tech → create project → copy DATABASE_URL to Vercel env',
      'Paste Vercel URL in Founder OS Settings',
    ],
  },
  {
    id: 'supabase',
    label: 'Supabase backend',
    description: 'DB + auth + storage bundle — host frontend on Render or Vercel.',
    freeTierNote: 'Generous free tier; may need API pattern adjustments for greenfield apps.',
    steps: [
      'Create Supabase project → copy connection string',
      'Connect GitHub → deploy frontend to Render or Vercel',
      'Paste Supabase URL + anon key in Settings → Builder',
    ],
  },
];

export function getPathDefinition(path: OnboardingPathId | null | undefined): FounderPathDefinition {
  return ONBOARDING_PATHS.find((p) => p.id === path) ?? ONBOARDING_PATHS.find((p) => p.id === 'BYO_CLOUD')!;
}

export function defaultComputePlaneForPath(path: OnboardingPathId): ComputePlaneModeId {
  return getPathDefinition(path).computePlane;
}

export function parseOnboardingPathParam(value: string | null | undefined): OnboardingPathId | null {
  if (!value) return null;
  const normalized = value.toUpperCase().replace(/-/g, '_');
  const aliases: Record<string, OnboardingPathId> = {
    SOVEREIGN: 'SOVEREIGN',
    BYO: 'BYO_CLOUD',
    BYO_CLOUD: 'BYO_CLOUD',
    MIGRATE: 'MIGRATE_PRIVATE',
    MIGRATE_PRIVATE: 'MIGRATE_PRIVATE',
    STARTER: 'FREE_STARTER',
    FREE_STARTER: 'FREE_STARTER',
    FOUNDER_CLOUD: 'FOUNDER_CLOUD',
    FOUNDER: 'FOUNDER_CLOUD',
  };
  return aliases[normalized] ?? null;
}

export function pathStepOptional(path: OnboardingPathId, stepId: OnboardingStepId): boolean {
  if (stepId === 'path') return false;
  if (stepId === 'founder_node' && (path === 'BYO_CLOUD' || path === 'FREE_STARTER')) return true;
  if (stepId === 'github' && (path === 'SOVEREIGN' || path === 'FOUNDER_CLOUD')) return true;
  if (stepId === 'platform' && path === 'FREE_STARTER') return true;
  if (stepId === 'migrate' && path !== 'MIGRATE_PRIVATE') return true;
  if (stepId === 'starter_pack' && path !== 'FREE_STARTER') return true;
  return false;
}

export function pathLabel(path: OnboardingPathId | null | undefined): string {
  if (!path) return 'Not chosen';
  return getPathDefinition(path).title;
}
