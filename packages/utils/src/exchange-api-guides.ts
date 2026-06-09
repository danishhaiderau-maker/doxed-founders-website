import type { ExchangeProvider } from './trading-agent-adapters';
import { EXCHANGE_PROVIDER_LABELS } from './trading-agent-adapters';

export type ExchangeApiGuide = {
  provider: ExchangeProvider;
  recommended?: boolean;
  recommendReason?: string;
  steps: string[];
  requiredPermissions: string[];
  forbiddenPermissions: string[];
  docsUrl: string;
  maxCapitalWarningUsd: number;
};

export const BITFINEX_RECOMMEND_BANNER =
  'Recommended: Bitfinex currently offers zero trading fees for eligible users — ideal for beta testing while fees would otherwise eat into small accounts.';

export const AGENT_BETA_RISK_COPY = {
  title: 'Experimental agent — beta phase',
  bullets: [
    'This strategy remains in active testing. Do not allocate more than $500.',
    'Past performance does not guarantee future results.',
    'The agent may lose capital. Use only funds you can afford to lose.',
    'High risk. Not financial advice.',
  ],
  checkboxLabel: 'I understand the risks and will not allocate more than $500.',
};

export const EXCHANGE_API_GUIDES: Record<ExchangeProvider, ExchangeApiGuide> = {
  bitfinex: {
    provider: 'bitfinex',
    recommended: true,
    recommendReason: BITFINEX_RECOMMEND_BANNER,
    docsUrl: 'https://support.bitfinex.com/hc/en-us/articles/115003363429-How-to-create-and-revoke-a-Bitfinex-API-Key',
    maxCapitalWarningUsd: 500,
    requiredPermissions: ['Read balance', 'Read orders', 'Create orders', 'Cancel orders'],
    forbiddenPermissions: ['Withdraw funds'],
    steps: [
      'Log in to Bitfinex and open Account → API Keys.',
      'Click Create New Key and label it (e.g. Founder OS Conservative BTC).',
      'Enable Read and Write permissions for Orders and Wallets — do NOT enable Withdraw.',
      'Optionally restrict by IP for extra security.',
      'Copy the API Key and API Secret immediately (secret shown once).',
      'Paste both into the Hire Agent wizard below.',
    ],
  },
  bybit: {
    provider: 'bybit',
    docsUrl: 'https://www.bybit.com/en/help-center/article/How-to-create-your-API-key',
    maxCapitalWarningUsd: 500,
    requiredPermissions: ['Read', 'Trade (spot/derivatives as needed)'],
    forbiddenPermissions: ['Withdraw', 'Transfer'],
    steps: [
      'Log in to Bybit → Account & Security → API Management.',
      'Create a new API key with System-generated keys.',
      'Enable Read-Write for trading; disable Withdraw and Transfer.',
      'Complete 2FA verification and save Key + Secret.',
      'Paste credentials into Hire Agent.',
    ],
  },
  binance: {
    provider: 'binance',
    docsUrl: 'https://www.binance.com/en/support/faq/how-to-create-api-keys-on-binance-360002502072',
    maxCapitalWarningUsd: 500,
    requiredPermissions: ['Enable Reading', 'Enable Spot & Margin Trading (or Futures if needed)'],
    forbiddenPermissions: ['Enable Withdrawals'],
    steps: [
      'Binance → Profile → API Management → Create API.',
      'Label the key and complete security verification.',
      'Enable Reading + Trading; keep Withdrawals disabled.',
      'Restrict trusted IPs if possible.',
      'Paste API Key and Secret into Hire Agent.',
    ],
  },
  okx: {
    provider: 'okx',
    docsUrl: 'https://www.okx.com/help-center/how-to-create-an-api-key',
    maxCapitalWarningUsd: 500,
    requiredPermissions: ['Read', 'Trade'],
    forbiddenPermissions: ['Withdraw'],
    steps: [
      'OKX → Profile → API → Create API key.',
      'Set a passphrase (required by OKX).',
      'Enable Read + Trade; disable Withdraw.',
      'Paste Key, Secret, and Passphrase into Hire Agent.',
    ],
  },
  hyperliquid: {
    provider: 'hyperliquid',
    docsUrl: 'https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/nonces-and-api-wallets',
    maxCapitalWarningUsd: 500,
    requiredPermissions: ['Agent wallet trading'],
    forbiddenPermissions: ['Main wallet private key export'],
    steps: [
      'Open Hyperliquid → Settings → API.',
      'Create an API wallet (agent wallet) — never use your main wallet key.',
      'Copy agent wallet address and private key.',
      'Paste into Hire Agent (address + private key fields).',
    ],
  },
};

export function exchangeGuideLabel(provider: ExchangeProvider): string {
  return EXCHANGE_PROVIDER_LABELS[provider];
}
