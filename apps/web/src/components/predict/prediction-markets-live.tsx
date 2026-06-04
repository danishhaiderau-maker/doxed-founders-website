'use client';

import Link from 'next/link';
import { useSession } from 'next-auth/react';
import { FormEvent, useCallback, useEffect, useState } from 'react';
import {
  formatDdollar,
  PREDICTION_MARKET_CREATE_COST_DD,
  PREDICTION_MARKET_HOURS,
  PREDICTION_MARKET_MIN_STAKE_DD,
  buildSiteUrl,
  buildPredictionShareMessage,
} from '@dcf/utils';
import { ShareOnXButton, useShareOrigin } from '@/components/share-on-x-button';
import { heatBadgeClass } from '@/components/engagement-flash-layer';
import {
  createPredictionMarket,
  fetchPredictionMarkets,
  fetchProjects,
  PredictionMarketItem,
  ProjectSummary,
  stakePredictionMarket,
} from '@/lib/api';

function sourceLabel(source?: string) {
  if (source === 'USER') return '👤 Community';
  if (source === 'AI') return '📋 Platform suggestion';
  return 'Default';
}

export function PredictionMarketsLive() {
  const { data: session } = useSession();
  const origin = useShareOrigin();
  const [markets, setMarkets] = useState<PredictionMarketItem[]>([]);
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [amounts, setAmounts] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true);
  const [msg, setMsg] = useState<string | null>(null);
  const [createForm, setCreateForm] = useState({ projectSlug: '', question: '' });
  const [creating, setCreating] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [m, p] = await Promise.all([
        fetchPredictionMarkets(session?.accessToken),
        fetchProjects(),
      ]);
      setMarkets(m);
      setProjects(p);
      setCreateForm((f) => (f.projectSlug ? f : { ...f, projectSlug: p[0]?.slug ?? '' }));
    } catch {
      setMarkets([]);
    } finally {
      setLoading(false);
    }
  }, [session?.accessToken]);

  useEffect(() => {
    load();
  }, [load]);

  async function handleStake(marketId: string, side: 'YES' | 'NO') {
    if (!session?.accessToken) {
      setMsg('Sign in and start paper trading to stake DDollar');
      return;
    }
    const amount = Number(amounts[marketId] ?? '100');
    if (amount < PREDICTION_MARKET_MIN_STAKE_DD) {
      setMsg(`Minimum stake is ${formatDdollar(PREDICTION_MARKET_MIN_STAKE_DD, 0)}`);
      return;
    }
    try {
      const result = await stakePredictionMarket(marketId, side, amount, session.accessToken);
      setMsg(
        `Staked ${formatDdollar(amount)} on ${side} — pool now ${formatDdollar(result.totalPoolUsd, 0)} (${result.conviction}% YES)`,
      );
      load();
    } catch (err) {
      setMsg(err instanceof Error ? err.message : 'Stake failed');
    }
  }

  async function handleCreate(e: FormEvent) {
    e.preventDefault();
    if (!session?.accessToken) {
      setMsg('Sign in to create a prediction market');
      return;
    }
    setCreating(true);
    setMsg(null);
    try {
      await createPredictionMarket(createForm, session.accessToken);
      setCreateForm((f) => ({ ...f, question: '' }));
      setMsg(`Market created — open for ${PREDICTION_MARKET_HOURS}h`);
      load();
    } catch (err) {
      setMsg(err instanceof Error ? err.message : 'Could not create market');
    } finally {
      setCreating(false);
    }
  }

  return (
    <div className="space-y-6">
      <p className="text-sm text-zinc-500">
        Markets must tie to ecosystem projects. Creation costs {formatDdollar(PREDICTION_MARKET_CREATE_COST_DD, 0)}{' '}
        (constitution). Read full rules in the{' '}
        <Link href="/predict?tab=rules" className="text-indigo-400 hover:underline">
          Constitution tab
        </Link>
        .
      </p>

      {msg && (
        <p className="rounded-lg border border-zinc-700 bg-zinc-900 px-4 py-3 text-sm text-zinc-200">{msg}</p>
      )}

      {session ? (
        <section className="rounded-xl border border-zinc-800 bg-zinc-950/60 p-5">
          <h2 className="font-semibold text-white">Create a market</h2>
          <p className="mt-1 text-xs text-zinc-500">
            Founder, community, or verified user — must include resolution intent (shipping, mcap, GitHub, launch)
          </p>
          <form onSubmit={handleCreate} className="mt-4 space-y-3">
            <select
              value={createForm.projectSlug}
              onChange={(e) => setCreateForm({ ...createForm, projectSlug: e.target.value })}
              className="w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm"
              required
            >
              {projects.map((p) => (
                <option key={p.slug} value={p.slug}>
                  {p.name} ({p.ticker})
                </option>
              ))}
            </select>
            <input
              type="text"
              value={createForm.question}
              onChange={(e) => setCreateForm({ ...createForm, question: e.target.value })}
              placeholder="Will we ship mobile app before August 31?"
              className="w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm"
              minLength={12}
              maxLength={280}
              required
            />
            <button
              type="submit"
              disabled={creating}
              className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-500 disabled:opacity-50"
            >
              {creating ? 'Creating…' : 'Launch market'}
            </button>
          </form>
        </section>
      ) : (
        <p className="text-sm text-zinc-500">
          <Link href="/login?callbackUrl=%2Fpredict%3Ftab%3Dmarkets" className="text-indigo-400 hover:underline">
            Sign in
          </Link>{' '}
          to create markets or stake DDollar.
        </p>
      )}

      <section>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="font-semibold text-white">Active markets</h2>
          <p className="text-xs text-zinc-500">Sorted by heat — parimutuel pool after window closes</p>
        </div>
        {loading ? (
          <p className="mt-4 text-sm text-zinc-500">Loading markets…</p>
        ) : markets.length === 0 ? (
          <p className="mt-4 text-sm text-zinc-500">No open markets yet — create one or check project pages.</p>
        ) : (
          <div className="mt-4 space-y-4">
            {markets.map((m) => (
              <article
                key={m.id}
                className={`rounded-xl border p-5 ${
                  m.heatLabel === 'Blazing'
                    ? 'border-orange-500/40 bg-orange-950/15'
                    : m.heatLabel === 'Heating up'
                      ? 'border-violet-500/30 bg-violet-950/10'
                      : 'border-indigo-500/25 bg-indigo-950/10'
                }`}
              >
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <div className="flex flex-wrap items-center gap-2">
                    <Link
                      href={`/project/${m.project.slug}`}
                      className="text-xs font-medium uppercase tracking-wide text-indigo-300 hover:underline"
                    >
                      {m.project.name} ({m.project.ticker})
                    </Link>
                    {m.heatLabel && (
                      <span
                        className={`rounded px-1.5 py-0.5 text-[10px] font-bold uppercase ${heatBadgeClass(m.heatLabel)}`}
                      >
                        {m.heatLabel}
                      </span>
                    )}
                  </div>
                  <span className="text-xs text-zinc-500">
                    {sourceLabel(m.source)}
                    {m.hoursLeft != null && m.hoursLeft > 0 && ` · ${m.hoursLeft}h left`}
                    {m.hoursLeft === 0 && ' · closing soon'}
                  </span>
                  <ShareOnXButton
                    text={buildPredictionShareMessage({
                      projectName: m.project.name,
                      ticker: m.project.ticker,
                      question: m.question,
                      poolUsd: m.totalPoolUsd ?? m.yesPoolUsd + m.noPoolUsd,
                    })}
                    url={buildSiteUrl(origin, '/predict')}
                    label="Share on X"
                  />
                </div>
                <p className="mt-2 font-medium text-white">{m.question}</p>
                <div className="mt-3 flex flex-wrap gap-4 text-sm">
                  <span className="text-emerald-300">{m.conviction}% YES</span>
                  <span className="text-zinc-400">
                    Pool {formatDdollar(m.totalPoolUsd ?? m.yesPoolUsd + m.noPoolUsd, 0)} (
                    {formatDdollar(m.yesPoolUsd, 0)} YES / {formatDdollar(m.noPoolUsd, 0)} NO)
                  </span>
                  <span className="text-zinc-600">{m.participantCount} forecasters</span>
                  {m.viewerPosition && (
                    <span className="rounded-full bg-violet-500/20 px-2 py-0.5 text-xs text-violet-200">
                      You: {formatDdollar(m.viewerPosition.amountUsd, 0)} {m.viewerPosition.side}
                    </span>
                  )}
                </div>
                <div className="mt-4 flex flex-col gap-2 sm:flex-row sm:items-center">
                  <input
                    type="number"
                    min={PREDICTION_MARKET_MIN_STAKE_DD}
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
              </article>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
