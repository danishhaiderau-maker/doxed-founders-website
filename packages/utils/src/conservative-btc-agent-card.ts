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
  intent: string;
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
    intent: `${baseApi}/api/trading-agents/conservative-btc/signals/intent`,
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
      'Exchange-neutral BTC perp signal cycles from a transparent Bitfinex research pipeline. Pay $0.10 USDC per signal poll (x402) or use API key; success fee (10% of profit) only on winning closes.',
    url: urls.hub,
    version: '1.0.0',
    skills: ['btc-perp-signals', 'exchange-agnostic', 'signal-lifecycle', 'success-fee-settlement'],
    endpoints: [
      { name: 'signal-mandate', url: urls.mandate },
      { name: 'signal-latest-preview', url: urls.latest },
      { name: 'signal-intent-x402', url: urls.intent },
      {
        name: 'signal-events',
        url: `${urls.api}/api/trading-agents/conservative-btc/signals/cycles/{cycleId}/events`,
      },
      { name: 'dashboard', url: urls.hub },
      { name: 'subscriber-docs', url: urls.docs },
    ],
    x402Support: Boolean(input?.feeWalletEvm),
    x402: input?.feeWalletEvm
      ? {
          intent_url: urls.intent,
          price_usd: 0.1,
          network: 'eip155:8453',
          scheme: 'exact',
          asset: 'USDC',
          pay_to: input.feeWalletEvm,
          facilitator: 'https://api.cdp.coinbase.com/platform/v2/x402',
          bazaar: {
            catalog: 'https://api.cdp.coinbase.com/platform/v2/x402/discovery/resources',
            search: 'https://api.cdp.coinbase.com/platform/v2/x402/discovery/search',
          },
        }
      : null,
    active: true,
    supportedTrust: ['reputation', 'on-chain-performance'],
    pricing: {
      model: 'hybrid_x402_success_fee',
      access: {
        x402_per_poll_usd: 0.1,
        intent_endpoint: urls.intent,
        preview_endpoint: urls.latest,
      },
      success_fee: {
        fee_pct: 0.1,
        min_profit_fee_usd: 0.2,
        charge_on_loss: false,
      },
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
  feeWalletEvm?: string | null;
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
    capabilities: ['signal-intent', 'lifecycle-events', 'success-fee-settlement', 'x402-micropay'],
    x402Support: Boolean(input?.feeWalletEvm),
    x402: input?.feeWalletEvm
      ? {
          intent_url: urls.intent,
          price_usd: 0.1,
          network: 'eip155:8453',
          pay_to: input.feeWalletEvm,
        }
      : null,
    payment: {
      model: 'hybrid_x402_success_fee',
      x402_per_poll_usd: 0.1,
      success_fee_pct: 0.1,
      min_profit_fee_usd: 0.2,
      asset: 'USDC',
      chain: 'solana',
      treasury: input?.feeWalletSolana ?? null,
      evm_treasury: input?.feeWalletEvm ?? null,
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
    id: 'FUSHU',
    label: 'Fushu (fushu.dev)',
    metadataUriKey: 'agentJson' as const,
    registerUrl: 'https://fushu.dev/register',
    cli: 'npm run register:agents-automated',
    note: 'fushu.json manifest + POST /api/v1/submit when API is healthy.',
  },
  {
    id: 'SKILLS_SH',
    label: 'OpenServ (platform API)',
    metadataUriKey: 'agentJson' as const,
    registerUrl: 'https://platform.openserv.ai',
    cli: 'npm run provision:openserv',
    note: 'OPENSERV_USER_API_KEY in vault → creates agent + workflow via API.',
  },
] as const;
