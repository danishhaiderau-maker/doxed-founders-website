/** Automated agent registry targets + profile metadata (CLI / API / manifest only). */

export const CONSERVATIVE_BTC_DIRECTORY_PROFILE = {
  name: 'Conservative BTC Agent',
  tagline: 'Exchange-neutral BTC perp signal API — pay success fee on profit only, not on losses.',
  description:
    'Conservative BTC Agent publishes live BTC perpetual signal cycles from a transparent research pipeline. Subscribers execute on their own exchange using exchange-neutral percentage offsets. Success fee: 10% of profit on close only; $0 on loss. REST Signal Cycle API with mandatory stop-loss-at-fill rule.',
  hubUrl: 'https://doxxedcrypto.digital/agent-hub/conservative-btc',
  siteUrl: 'https://doxxedcrypto.digital',
  docsUrl: 'https://doxxedcrypto.digital/docs/signal-api',
  agentCardUrl: 'https://doxxedcrypto.digital/.well-known/agent-card.json',
  agentJsonUrl: 'https://doxxedcrypto.digital/.well-known/agent.json',
  iconUrl: 'https://doxxedcrypto.digital/icons/conservative-btc-agent.png',
  thumbnailUrl: 'https://doxxedcrypto.digital/icons/conservative-btc-agent-thumbnail.png',
  mandateUrl:
    'https://doxed-founders-website-production.up.railway.app/api/trading-agents/conservative-btc/signals/mandate',
  company: 'Doxxed Crypto Founder',
  authorEmail: 'danish.haider.au@gmail.com',
} as const;

export type AgentAutomationKind = 'live' | 'cli' | 'api' | 'manifest' | 'auto';

export type AgentAutomatedRegistry = {
  id: string;
  label: string;
  kind: AgentAutomationKind;
  command?: string;
  docs?: string;
  note?: string;
};

/** Registries we automate — no manual web forms. */
export const AGENT_AUTOMATED_REGISTRIES: AgentAutomatedRegistry[] = [
  {
    id: 'AGENT_CARD',
    label: 'AgentCard (canonical metadata)',
    kind: 'live',
    docs: CONSERVATIVE_BTC_DIRECTORY_PROFILE.agentCardUrl,
    note: 'Already served at /.well-known/agent-card.json',
  },
  {
    id: 'SAID',
    label: 'SAID Protocol (Solana)',
    kind: 'cli',
    command: 'npm run register:said-simple',
    docs: 'https://www.saidprotocol.com',
    note: 'Fund agent-wallet.json with ~0.02 SOL, then re-run.',
  },
  {
    id: 'SPAWN',
    label: 'The Spawn / ERC-8004 (Base)',
    kind: 'api',
    command: 'npm run prepare:agent-registrations',
    docs: 'https://thespawn.io',
    note: 'Set THESPAWN_API_KEY in vault; sign Base mint tx.',
  },
  {
    id: 'ERC8004_SCAN',
    label: '8004scan / agentscan.info',
    kind: 'auto',
    docs: 'https://8004scan.io',
    note: 'Indexed automatically after Spawn mint confirms.',
  },
  {
    id: 'FUSHU',
    label: 'Fushu (manifest + API)',
    kind: 'manifest',
    command: 'npm run register:agents-automated',
    docs: 'https://fushu.dev',
    note: 'fushu.json in repo root; API submit when fushu.dev is up.',
  },
  {
    id: 'SKILLS_SH',
    label: 'OpenServ (platform API)',
    kind: 'api',
    command: 'npm run provision:openserv',
    docs: 'https://platform.openserv.ai',
    note: 'Set OPENSERV_USER_API_KEY in vault, then provision agent + workflow.',
  },
];

/** Manual web directories removed — no CLI/API, not worth ops time. */
export const DEPRECATED_MANUAL_DIRECTORIES = [
  'aiagentsdirectory.com',
  'pikagent.com',
  'agents.one',
  'listmyagent.com',
  'aiagentsbuzz.com',
] as const;
