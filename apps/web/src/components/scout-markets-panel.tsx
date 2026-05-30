'use client';

import { formatUsd } from '@dcf/utils';
import { useCallback, useEffect, useState } from 'react';
import { fetchScoutMarkets, ScoutMarketItem, stakeScoutMarket } from '@/lib/api';

type ScoutMarketsPanelProps = {
  slug: string;
  accessToken?: string;
  onMessage?: (msg: string) => void;
};

export function ScoutMarketsPanel({ slug, accessToken, onMessage }: ScoutMarketsPanelProps) {
  const [markets, setMarkets] = useState<ScoutMarketItem[]>([]);
  const [amounts, setAmounts] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    try {
      setMarkets(await fetchScoutMarkets(slug, accessToken));
    } catch {
      setMarkets([]);
    } finally {
      setLoading(false);
    }
  }, [slug, accessToken]);

  useEffect(() => {
    load();
  }, [load]);

  async function handleStake(marketId: string, side: 'YES' | 'NO') {
    if (!accessToken) {
      onMessage?.('Sign in to stake paper dollars on scout markets');
      return;
    }
    const amount = Number(amounts[marketId] ?? '100');
    if (amount < 10) {
      onMessage?.('Minimum stake is $10');
      return;
    }
    try {
      const result = await stakeScoutMarket(marketId, side, amount, accessToken);
      onMessage?.(`Staked ${formatUsd(amount)} on ${side} — ${result.conviction}% YES conviction`);
      load();
    } catch (err) {
      onMessage?.(err instanceof Error ? err.message : 'Stake failed');
    }
  }

  if (loading) {
    return <p className="text-sm text-zinc-500">Loading scout markets…</p>;
  }

  if (markets.length === 0) {
    return (
      <p className="text-sm text-zinc-500">
        Scout prediction markets appear when a founder publishes their project.
      </p>
    );
  }

  return (
    <div className="space-y-4">
      <p className="text-xs text-zinc-500">
        Paper-money YES/NO markets — winners split the pool after 48h.{' '}
        <a href="/predict" className="text-indigo-400 hover:underline">
          All markets →
        </a>
      </p>
      {markets.map((m) => (
        <div key={m.id} className="rounded-xl border border-indigo-500/30 bg-indigo-950/10 p-5">
          <p className="font-medium text-white">{m.question}</p>
          {m.hoursLeft != null && m.hoursLeft > 0 && (
            <p className="mt-1 text-xs text-zinc-500">{m.hoursLeft}h remaining</p>
          )}
          <div className="mt-3 flex flex-wrap items-center gap-4 text-sm">
            <span className="text-emerald-300">{m.conviction}% YES</span>
            <span className="text-zinc-500">
              Pools: {formatUsd(m.yesPoolUsd, 0)} YES / {formatUsd(m.noPoolUsd, 0)} NO
            </span>
            <span className="text-zinc-600">{m.participantCount} scouts</span>
            {m.viewerPosition && (
              <span className="rounded-full bg-violet-500/20 px-2 py-0.5 text-xs text-violet-200">
                Your stake: {formatUsd(m.viewerPosition.amountUsd, 0)} {m.viewerPosition.side}
              </span>
            )}
          </div>
          <div className="mt-4 flex flex-col gap-2 sm:flex-row sm:items-center">
            <input
              type="number"
              min={10}
              value={amounts[m.id] ?? '100'}
              onChange={(e) => setAmounts({ ...amounts, [m.id]: e.target.value })}
              className="w-full max-w-[140px] rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm"
            />
            <button
              type="button"
              onClick={() => handleStake(m.id, 'YES')}
              className="rounded-lg bg-emerald-600 px-4 py-2 text-sm font-medium text-white hover:bg-emerald-500"
            >
              Stake YES
            </button>
            <button
              type="button"
              onClick={() => handleStake(m.id, 'NO')}
              className="rounded-lg border border-red-500/40 px-4 py-2 text-sm text-red-200 hover:bg-red-950/30"
            >
              Stake NO
            </button>
          </div>
        </div>
      ))}
    </div>
  );
}
