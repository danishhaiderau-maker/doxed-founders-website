/** Shared discovery metadata for Conservative BTC Agent (SAID, ERC-8004, AgentCard). */

export const CONSERVATIVE_BTC_AGENT_SLUG = 'conservative-btc';

export type ConservativeBtcAgentUrls = {
  site: string;
  api: string;
  hub: string;
  agentCard: string;
  agentJson: string;
  docs: string;
  mandate: string;
  latest: string;
};

export function resolveConservativeBtcAgentUrls(
  site = process.env.NEXT_PUBLIC_SITE_URL ?? 'https://doxxedcrypto.digital',
  api = process.env.NEXT_PUBLIC_API_URL ?? 'https://doxed-founders-website-production.up.railway.app',
): ConservativeBtcAgentUrls {
  const baseApi = api.replace(/\/$/, '');
  const baseSite = site.replace(/\/$/, '');
  return {
    site: baseSite,
    api: baseApi,
    hub: `${baseSite}/agent-hub/conservative-btc`,
    agentCard: `${baseSite}/.well-known/agent-card.json`,
    agentJson: `${baseSite}/.well-known/agent.json`,
    docs: `${baseSite}/docs/signal-api`,
    mandate: `${baseApi}/api/trading-agents/conservative-btc/signals/mandate`,
    latest: `${baseApi}/api/trading-agents/conservative-btc/signals/latest`,
  };
}

export function buildConservativeBtcAgentCard(input?: {
  site?: string;
  api?: string;
  feeWalletSolana?: string | null;
  feeWalletEvm?: string | null;
}) {
  const urls = resolveConservativeBtcAgentUrls(input?.site, input?.api);
  return {
    name: 'Conservative BTC Agent',
    description:
      'Exchange-neutral BTC perp signal cycles from a transparent Bitfinex research pipeline. Pay success fee (10% of profit) only on winning closes.',
    url: urls.hub,
    version: '1.0.0',
    skills: ['btc-perp-signals', 'exchange-agnostic', 'signal-lifecycle', 'success-fee-settlement'],
    endpoints: [
      { name: 'signal-mandate', url: urls.mandate },
      { name: 'signal-latest', url: urls.latest },
      {
        name: 'signal-events',
        url: `${urls.api}/api/trading-agents/conservative-btc/signals/cycles/{cycleId}/events`,
      },
      { name: 'dashboard', url: urls.hub },
      { name: 'subscriber-docs', url: urls.docs },
    ],
    x402Support: false,
    active: true,
    supportedTrust: ['reputation', 'on-chain-performance'],
    pricing: {
      model: 'success_fee',
      fee_pct: 0.1,
      min_profit_fee_usd: 0.2,
      charge_on_loss: false,
      settlement: {
        primary: 'solana_usdc',
        treasury_solana: input?.feeWalletSolana ?? null,
        treasury_evm: input?.feeWalletEvm ?? null,
        fallback: 'ddollar',
      },
    },
    metadata_uri: urls.agentCard,
    erc8004_uri: urls.agentJson,
  };
}

/** ERC-8004 / The Spawn metadata shape. */
export function buildConservativeBtcErc8004AgentJson(input?: {
  site?: string;
  api?: string;
  imageUrl?: string;
  feeWalletSolana?: string | null;
  ownerAddress?: string | null;
}) {
  const urls = resolveConservativeBtcAgentUrls(input?.site, input?.api);
  const image =
    input?.imageUrl?.trim() ||
    `${urls.site}/icons/conservative-btc-agent.png`;

  return {
    name: 'Conservative BTC Agent',
    description:
      'Live BTC perp signal cycles for any exchange. Subscribers receive exchange-neutral ENSE intents, must arm exchange-native stop-loss at fill, and pay a 10% success fee on profit only (post-trade USDC to admin treasury).',
    image,
    url: urls.hub,
    version: '1.0.0',
    services: [
      {
        name: 'REST',
        endpoint: `${urls.api}/api/trading-agents/conservative-btc/signals/mandate`,
      },
      {
        name: 'web',
        endpoint: urls.hub,
      },
      {
        name: 'docs',
        endpoint: urls.docs,
      },
    ],
    capabilities: ['signal-intent', 'lifecycle-events', 'success-fee-settlement'],
    x402Support: false,
    payment: {
      model: 'success_fee',
      fee_pct: 0.1,
      min_profit_fee_usd: 0.2,
      asset: 'USDC',
      chain: 'solana',
      treasury: input?.feeWalletSolana ?? null,
    },
    owner: input?.ownerAddress ?? null,
    agentCard: urls.agentCard,
  };
}

export const AGENT_REGISTRY_TARGETS = [
  {
    id: 'AGENT_CARD',
    label: 'AgentCard (canonical)',
    metadataUriKey: 'agentCard' as const,
    docs: 'https://doxxedcrypto.digital/.well-known/agent-card.json',
  },
  {
    id: 'SAID',
    label: 'SAID Protocol (Solana)',
    metadataUriKey: 'agentCard' as const,
    registerUrl: 'https://www.saidprotocol.com',
    cli: 'npx said-sdk register',
    verifyCostSol: 0.01,
  },
  {
    id: 'SPAWN',
    label: 'The Spawn (ERC-8004 on Base)',
    metadataUriKey: 'agentJson' as const,
    registerUrl: 'https://thespawn.io',
    api: 'POST https://thespawn.io/api/v1/agents',
    chainId: 8453,
  },
  {
    id: 'ERC8004_SCAN',
    label: '8004scan / AgentScan (auto-index)',
    metadataUriKey: 'agentJson' as const,
    note: 'Indexed after on-chain ERC-8004 mint on Base.',
  },
  {
    id: 'AGENTSCAN',
    label: 'AI Agents Directory (aiagentsdirectory.com)',
    metadataUriKey: 'agentCard' as const,
    registerUrl: 'https://aiagentsdirectory.com/submit-agent',
    note: 'Free listing — badge on Agent Hub footer. See docs/ALL_AGENT_DIRECTORIES.md',
  },
  {
    id: 'PIKAGENT',
    label: 'Pikagent',
    metadataUriKey: 'agentCard' as const,
    registerUrl: 'https://www.pikagent.com/submit',
    note: 'Free — Data Analysis / Workflow. npm run submit:agent-directories -- --open',
  },
  {
    id: 'FUSHU',
    label: 'Fushu (fushu.dev)',
    metadataUriKey: 'agentJson' as const,
    registerUrl: 'https://fushu.dev/register',
    note: 'Free — fushu.json in repo + API submit script.',
  },
  {
    id: 'AGENTS_ONE',
    label: 'Agents.one',
    metadataUriKey: 'agentCard' as const,
    registerUrl: 'https://agents.one/submit-agent/',
    note: 'Free basic listing forever.',
  },
  {
    id: 'LISTMYAGENT',
    label: 'ListMyAgent',
    metadataUriKey: 'agentCard' as const,
    registerUrl: 'https://listmyagent.com/add-ai-agent-listing/',
    note: 'Free submit — logo + thumbnail URLs ready.',
  },
  {
    id: 'AIAGENTS_BUZZ',
    label: 'AI Agents Buzz',
    metadataUriKey: 'agentCard' as const,
    registerUrl: 'https://aiagentsbuzz.com/submit/',
    note: 'Free — Finance + Research categories.',
  },
  {
    id: 'SKILLS_SH',
    label: 'OpenServ',
    metadataUriKey: 'agentJson' as const,
    registerUrl: 'https://www.openserv.ai/',
    note: 'Apply with API docs + mandate URL.',
  },
] as const;
