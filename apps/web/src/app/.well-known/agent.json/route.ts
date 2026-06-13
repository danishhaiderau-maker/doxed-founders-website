import { NextResponse } from 'next/server';
import { buildConservativeBtcErc8004AgentJson } from '@dcf/utils';

export function GET() {
  const card = buildConservativeBtcErc8004AgentJson({
    feeWalletSolana: process.env.NEXT_PUBLIC_AGENT_FEE_WALLET_SOLANA?.trim() || null,
  });

  return NextResponse.json(card, {
    headers: {
      'Cache-Control': 'public, max-age=300',
      'Access-Control-Allow-Origin': '*',
    },
  });
}
