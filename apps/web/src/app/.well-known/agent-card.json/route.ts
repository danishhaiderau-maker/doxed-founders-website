import { NextResponse } from 'next/server';

const SITE = process.env.NEXT_PUBLIC_SITE_URL ?? 'https://doxxedcrypto.digital';
const API = process.env.NEXT_PUBLIC_API_URL ?? 'https://doxed-founders-website-production.up.railway.app';

export function GET() {
  const card = {
    name: 'Conservative BTC Agent',
    description:
      'Exchange-neutral BTC perp signal cycles from a transparent Bitfinex research pipeline. Pay success fee (10% of profit) only on winning closes.',
    url: `${SITE}/agent-hub/conservative-btc`,
    version: '1.0.0',
    skills: ['btc-perp-signals', 'exchange-agnostic', 'signal-lifecycle', 'success-fee-settlement'],
    endpoints: [
      {
        name: 'signal-mandate',
        url: `${API}/trading-agents/conservative-btc/signals/mandate`,
      },
      {
        name: 'signal-latest',
        url: `${API}/trading-agents/conservative-btc/signals/latest`,
      },
      {
        name: 'signal-events',
        url: `${API}/trading-agents/conservative-btc/signals/cycles/{cycleId}/events`,
      },
      {
        name: 'dashboard',
        url: `${SITE}/agent-hub/conservative-btc`,
      },
      {
        name: 'subscriber-docs',
        url: `${SITE}/docs/signal-api`,
      },
    ],
    x402Support: false,
    active: true,
    supportedTrust: ['reputation', 'on-chain-performance'],
    pricing: {
      model: 'success_fee',
      fee_pct: 0.1,
      min_profit_fee_usd: 0.2,
      charge_on_loss: false,
    },
  };

  return NextResponse.json(card, {
    headers: {
      'Cache-Control': 'public, max-age=300',
      'Access-Control-Allow-Origin': '*',
    },
  });
}
