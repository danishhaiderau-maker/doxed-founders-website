export type BrainConnectionSnapshot = {
  githubConnected: boolean;
  cursorConnected: boolean;
  llmConnected: boolean;
  founderNodeConnected?: boolean;
  neonConnected?: boolean;
  vercelConnected?: boolean;
  railwayConnected?: boolean;
  repoFullName?: string | null;
  vaultHealthy?: boolean;
};

export type SmartQuickPrompt = {
  id: string;
  label: string;
  prompt: string;
  kind: 'ask' | 'build' | 'action';
  priority: number;
};

const CONNECT_GITHUB_HREF = '/account?tab=connected&connect=github';
const AI_STACK_HREF = '/settings/builder';

/** Expert PM-style gap message — never a dead-end "connect GitHub" only. */
export function buildBrainCoachGapMessage(
  mode: 'ask' | 'build',
  conn: BrainConnectionSnapshot,
): string | null {
  if (mode === 'build' && conn.cursorConnected && conn.githubConnected) return null;

  if (mode === 'build' && !conn.githubConnected) {
    return [
      '**You asked to build — but no GitHub repo is linked yet.**',
      '',
      'I can still help two ways. Pick one:',
      '',
      '**Option A — Sovereign (save money, research phase)**',
      '• Keep code in **Founder Vault** on your machine (Founder Node memory client)',
      '• I draft contracts, specs, and file trees — you save locally until ready to push',
      '• No Neon/Railway bills until you choose production',
      '• Reply: **"Yes — draft in my vault using local compute"**',
      '',
      '**Option B — Connect GitHub (recommended for Cursor builds)**',
      `• Link owner/repo in [Connected Accounts](${CONNECT_GITHUB_HREF})`,
      '• Cursor implements in your repo with PRs',
      '• Reply: **"Walk me through GitHub setup step by step"**',
      '',
      '**Option C — Hybrid**',
      '• Connect GitHub now · defer Neon/Railway/Vercel until go-live',
      '',
      '_I will not dispatch Cursor until you confirm Option B/C or grant vault drafting (Option A)._',
    ].join('\n');
  }

  if (mode === 'build' && !conn.cursorConnected) {
    return [
      '**Build needs a code agent.**',
      '',
      `1. Open [AI Stack → Remote Builder](${AI_STACK_HREF}#remote-builder)`,
      '2. Connect **Cursor** (Promo works during your first month)',
      conn.githubConnected
        ? `3. Repo \`${conn.repoFullName}\` is linked — you're one step away`
        : `3. Or choose **Option A** above to draft in Founder Vault without Cursor`,
      '',
      'Reply **"Connect Cursor for me"** for a step-by-step guide.',
    ].join('\n');
  }

  return null;
}

/** Context-aware quick prompts for Mission Control chips. */
export function resolveSmartQuickPrompts(conn: BrainConnectionSnapshot): SmartQuickPrompt[] {
  const prompts: SmartQuickPrompt[] = [];

  if (!conn.githubConnected) {
    prompts.push({
      id: 'sovereign',
      label: 'Research on my laptop only',
      prompt:
        'I am in research phase — use Sovereign pathway: Founder Vault on my machine, no Neon/Railway yet. What should I set up first?',
      kind: 'ask',
      priority: 100,
    });
    prompts.push({
      id: 'github-guide',
      label: 'Walk me through GitHub',
      prompt:
        'Walk me step by step to connect GitHub for my project — what exactly do you need and why?',
      kind: 'ask',
      priority: 90,
    });
    prompts.push({
      id: 'vault-code',
      label: 'Draft code in my vault',
      prompt:
        'Draft the next code milestone in Founder Vault (local storage) — I have not linked GitHub yet. Ask me what to build first.',
      kind: 'ask',
      priority: 85,
    });
  } else if (!conn.cursorConnected) {
    prompts.push({
      id: 'cursor-setup',
      label: 'Connect Cursor to build',
      prompt: 'Guide me to connect Cursor so you can implement my top priority task in the repo.',
      kind: 'ask',
      priority: 95,
    });
  } else {
    prompts.push({
      id: 'build-cursor',
      label: 'Build with Cursor',
      prompt: 'Build with Cursor — implement my top priority task from the mission queue',
      kind: 'build',
      priority: 100,
    });
  }

  if (conn.githubConnected && conn.founderNodeConnected) {
    prompts.push({
      id: 'full-control',
      label: 'Take full control',
      prompt: 'Take full control — sync GitHub, vault, and push all updates to my local Sovereign stack',
      kind: 'action',
      priority: 80,
    });
  }

  if (!conn.neonConnected || !conn.railwayConnected || !conn.vercelConnected) {
    if (conn.githubConnected) {
      prompts.push({
        id: 'production-path',
        label: 'Ready for production?',
        prompt:
          'I want to go global — should I connect Neon, Railway, and Vercel now or stay Sovereign? Compare costs and give me a step plan.',
        kind: 'ask',
        priority: 70,
      });
    }
  } else {
    prompts.push({
      id: 'deploy',
      label: 'Deploy to cloud',
      prompt: 'Deploy my stack to Vercel and Railway — what is the correct order?',
      kind: 'ask',
      priority: 75,
    });
  }

  prompts.push(
    {
      id: 'working-on',
      label: 'What am I working on?',
      prompt: 'What am I working on? Summarize initiative, blockers, and the single best next step.',
      kind: 'ask',
      priority: 60,
    },
    {
      id: 'ship-today',
      label: 'What should I ship today?',
      prompt: 'What should I ship today? One concrete deliverable I can finish in this session.',
      kind: 'ask',
      priority: 55,
    },
  );

  return prompts.sort((a, b) => b.priority - a.priority).slice(0, 6);
}

export function isRecapOrHistoryPrompt(prompt: string): boolean {
  return /post.*(last|our|entire).*chat|last full conversation|recap|conversation history|what did we (discuss|talk)|repeat our chat/i.test(
    prompt,
  );
}

export function formatRecapCoachAnswer(input: {
  projectName: string;
  currentInitiative: string;
  recommendedNextStep: string;
  blocker: string | null;
  confidence: string;
  repoFullName?: string | null;
  conn: BrainConnectionSnapshot;
}): string {
  const lines = [
    '# Session recap (from mission memory — not a raw chat transcript)',
    '',
    `**Project:** ${input.projectName}`,
    `**Current initiative:** ${input.currentInitiative}`,
    `**Recommended next step:** ${input.recommendedNextStep}`,
    input.blocker ? `**Blocker:** ${input.blocker}` : '',
    `**Confidence:** ${input.confidence}`,
    '',
  ];

  if (!input.conn.githubConnected) {
    lines.push(
      '---',
      '',
      '**Before we ship code**, I need your pathway:',
      '',
      '1. **Sovereign** — draft in Founder Vault on your laptop (no cloud bills)',
      '2. **Hybrid** — connect GitHub + Cursor; cloud later',
      '3. **Production** — GitHub + Neon + Railway + Vercel',
      '',
      '**Reply with 1, 2, or 3** — or tell me your goal in one sentence (research vs go-live).',
      '',
      '_I will not recommend random smart-contract tasks until you pick a path and I understand $REM._',
    );
  } else {
    lines.push(
      '---',
      '',
      '**Ready to continue?** Reply **"Build with Cursor"** or ask a clarifying question if anything above looks wrong.',
    );
  }

  return lines.filter(Boolean).join('\n');
}

export const FOUNDER_BRAIN_EXPERT_PM_RULES = [
  'You are an expert product manager and technical advisor — not a generic chatbot.',
  'When repository is NOT linked: do NOT invent specific code tasks (e.g. random Solidity functions). Ask ONE clarifying question and offer Sovereign (local vault), Hybrid (GitHub only), or Production (full cloud) pathways.',
  'When confidence is low: state uncertainty explicitly and ask what the user wants (save money vs ship fast vs privacy).',
  'When user asks for chat history/recap: explain you have mission memory not verbatim transcripts; summarize initiative + blockers + ask which pathway they want.',
  'Explain WHY each integration is needed (GitHub = commit intelligence + Cursor; Neon = DB; Railway = API; Vercel = web).',
  'Offer Founder Vault / Founder Node for local code storage when GitHub is missing.',
  'Never contradict recommended_next_step without explaining the conflict.',
  'End actionable replies with exactly one clear question or A/B/C choice when blockers exist.',
].join('\n');
