/** Hybrid control plane — Founder OS orchestrates; workers (Cursor, LLMs) execute. */

export type ControlPlaneModeKey = 'CURSOR_FIRST' | 'FULL_STACK';

export type ControlPlaneLegKey = 'ask' | 'code' | 'ship';

export type ControlPlaneLeg = {
  key: ControlPlaneLegKey;
  label: string;
  subtitle: string;
  connected: boolean;
  detail?: string;
  provider?: string;
};

export const CONTROL_PLANE_MODES: {
  key: ControlPlaneModeKey;
  label: string;
  description: string;
}[] = [
  {
    key: 'CURSOR_FIRST',
    label: 'Cursor for code',
    description:
      'Minimal connect: GitHub + optional Cursor API. You code in Cursor; Founder OS syncs commits and can publish your story.',
  },
  {
    key: 'FULL_STACK',
    label: 'Full Founder OS stack',
    description:
      'GitHub, Neon, Vercel, Railway, chat AI, builder agent, and Autopilot — one control plane for sync, deploy, and publish.',
  },
];

export type ControlPlaneReadiness = {
  mode: ControlPlaneModeKey;
  legs: ControlPlaneLeg[];
  infraConnected: number;
  infraTotal: number;
  readyForAutopilot: boolean;
  missingForFullStack: string[];
};

export function buildControlPlaneReadiness(input: {
  mode: ControlPlaneModeKey;
  githubConnected: boolean;
  repoFullName?: string | null;
  neonConnected: boolean;
  vercelConnected: boolean;
  railwayConnected: boolean;
  chatConnected: boolean;
  chatProvider?: string;
  buildWorker: string;
  autopilotEnabled: boolean;
}): ControlPlaneReadiness {
  const legs: ControlPlaneLeg[] = [
    {
      key: 'ask',
      label: 'Ask',
      subtitle: 'Answers in Mission Control',
      connected: input.chatConnected,
      provider: input.chatProvider,
      detail: input.chatConnected ? undefined : 'Connect DeepSeek, OpenAI, or Ollama in AI Stack',
    },
    {
      key: 'code',
      label: 'Code in repo',
      subtitle: 'Commits & PRs on GitHub',
      connected: input.githubConnected && input.buildWorker !== 'NONE',
      provider: input.buildWorker !== 'NONE' ? input.buildWorker : undefined,
      detail: input.repoFullName ?? (input.githubConnected ? 'Set repo in Stack' : 'Connect GitHub'),
    },
    {
      key: 'ship',
      label: 'Ship story',
      subtitle: 'Feed, X, community via Founder OS',
      connected: input.autopilotEnabled || input.githubConnected,
      detail: input.autopilotEnabled ? 'Autopilot on' : 'Enable Autopilot or publish manually',
    },
  ];

  const infra = [
    { key: 'github', ok: input.githubConnected },
    { key: 'neon', ok: input.neonConnected },
    { key: 'vercel', ok: input.vercelConnected },
    { key: 'railway', ok: input.railwayConnected },
  ];
  const infraConnected = infra.filter((i) => i.ok).length;
  const missingForFullStack: string[] = [];
  if (!input.githubConnected) missingForFullStack.push('GitHub');
  if (!input.neonConnected) missingForFullStack.push('Neon');
  if (!input.vercelConnected) missingForFullStack.push('Vercel');
  if (!input.railwayConnected) missingForFullStack.push('Railway');

  const readyForAutopilot =
    input.mode === 'CURSOR_FIRST'
      ? input.githubConnected
      : input.githubConnected && infraConnected >= 2;

  return {
    mode: input.mode,
    legs,
    infraConnected,
    infraTotal: infra.length,
    readyForAutopilot,
    missingForFullStack,
  };
}
