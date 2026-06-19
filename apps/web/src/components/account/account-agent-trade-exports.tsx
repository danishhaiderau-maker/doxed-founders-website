'use client';

import Link from 'next/link';
import { useEffect, useState } from 'react';
import { AgentLiveTradeExportButton } from '@/components/agent-hub/agent-live-trade-export-button';
import { fetchTradingAgent } from '@/lib/api';

export function AccountAgentTradeExports({ token }: { token: string }) {
  const [slug, setSlug] = useState<string | null>(null);
  const [exchangeLabel, setExchangeLabel] = useState('Bitfinex');
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const agent = await fetchTradingAgent('conservative-btc', token);
        if (cancelled) return;
        if (agent.hired && agent.instanceMode === 'live' && agent.exchangeProvider === 'bitfinex') {
          setSlug('conservative-btc');
          setExchangeLabel(agent.exchangeLabel ?? 'Bitfinex');
        }
      } catch {
        // No live hire — hide panel
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [token]);

  if (loading || !slug) return null;

  return (
    <div className="rounded-xl border border-emerald-500/25 bg-emerald-950/15 p-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="font-semibold text-white">Agent live trade history</h3>
          <p className="mt-1 text-sm text-zinc-400">
            Download every real {exchangeLabel} copy trade from the Conservative BTC Agent — timestamps,
            fills, exits, P&amp;L, and order IDs.
          </p>
        </div>
        <Link
          href="/agent-hub/conservative-btc"
          className="text-xs text-emerald-400 hover:text-emerald-300"
        >
          Open agent hub →
        </Link>
      </div>
      <div className="mt-4">
        <AgentLiveTradeExportButton
          slug={slug}
          token={token}
          signedIn
          exchangeLabel={exchangeLabel}
        />
      </div>
    </div>
  );
}
