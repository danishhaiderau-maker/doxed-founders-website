'use client';

import Link from 'next/link';
import { useSession } from 'next-auth/react';
import { FormEvent, useCallback, useEffect, useState } from 'react';
import { formatUsd, PREDICTION_MARKET_HOURS, TOP_UP_FEE_USD, RESTRICTED_CASH_THRESHOLD_USD, STARTING_CASH_USD, buildSiteUrl, buildPredictionShareMessage } from '@dcf/utils';
import { SiteBrand, SiteNav } from '@/components/site-nav';
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

export default function PredictPage() {
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
      setMsg('Sign in and start paper trading to stake');
      return;
    }
    const amount = Number(amounts[marketId] ?? '100');
    if (amount < 10) {
      setMsg('Minimum stake is $10 paper dollars');
      return;
    }
    try {
      const result = await stakePredictionMarket(marketId, side, amount, session.accessToken);
      setMsg(
        `Staked ${formatUsd(amount)} on ${side} — pool now ${formatUsd(result.totalPoolUsd, 0)} (${result.conviction}% YES)`,
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
      setMsg('Market created — open for 48 hours');
      load();
    } catch (err) {
      setMsg(err instanceof Error ? err.message : 'Could not create market');
    } finally {
      setCreating(false);
    }
  }

  return (
    <main className="min-h-screen bg-[#050508]">
      <header className="border-b border-zinc-800">
        <div className="mx-auto flex max-w-5xl flex-wrap items-center justify-between gap-4 px-6 py-5">
          <div>
            <SiteBrand className="text-sm" />
            <h1 className="mt-1 text-2xl font-bold">Predict the future</h1>
            <p className="text-sm text-zinc-500">
              Stake paper dollars on YES/NO — winners split the pool after {PREDICTION_MARKET_HOURS}h
            </p>
          </div>
          <SiteNav />
        </div>
      </header>

      <div className="mx-auto max-w-5xl space-y-8 px-6 py-8">
        <section className="rounded-xl border border-indigo-500/30 bg-indigo-950/15 p-5 text-sm text-indigo-100">
          <p className="font-medium">Gamified paper-money markets</p>
          <ul className="mt-2 list-inside list-disc space-y-1 text-indigo-200/90">
            <li>Stake virtual cash from your paper trading portfolio (min $10)</li>
            <li>
              Below ${RESTRICTED_CASH_THRESHOLD_USD.toLocaleString()}? Top up for ${TOP_UP_FEE_USD} real →
              ${STARTING_CASH_USD.toLocaleString()} paper — or earn points via comments &amp; research
            </li>
            <li>When the window closes, the side with more stake wins and splits the full pool fairly</li>
            <li>
              New listings get rule-based AI questions from DexScreener metrics (price, mcap, founder, product).
              When a 48h window closes, fresh questions open automatically.
            </li>
          </ul>
          <Link href="/paper-trading" className="mt-3 inline-block text-emerald-400 hover:underline">
            Open paper trading desk →
          </Link>
        </section>

        {msg && (
          <p className="rounded-lg border border-zinc-700 bg-zinc-900 px-4 py-3 text-sm text-zinc-200">{msg}</p>
        )}

        {session && (
          <section className="rounded-xl border border-zinc-800 bg-zinc-950/60 p-5">
            <h2 className="font-semibold text-white">Create a market</h2>
            <p className="mt-1 text-xs text-zinc-500">Ask any YES/NO question — closes in 48 hours</p>
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
                placeholder="Will GRID double market cap this week?"
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
        )}

        <section>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h2 className="font-semibold text-white">Live markets</h2>
            <p className="text-xs text-zinc-500">Sorted by heat — stake to push a question to the top</p>
          </div>
          {loading ? (
            <p className="mt-4 text-sm text-zinc-500">Loading markets…</p>
          ) : markets.length === 0 ? (
            <p className="mt-4 text-sm text-zinc-500">No open markets yet — create one above.</p>
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
                      {m.source === 'AI' ? '🤖 AI question' : m.source === 'USER' ? '👤 Community' : 'Default'}
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
                    <span className="text-emerald-300">{m.conviction}% YES conviction</span>
                    <span className="text-zinc-400">
                      Pool {formatUsd(m.totalPoolUsd ?? m.yesPoolUsd + m.noPoolUsd, 0)} ({formatUsd(m.yesPoolUsd, 0)} YES /{' '}
                      {formatUsd(m.noPoolUsd, 0)} NO)
                    </span>
                    <span className="text-zinc-600">{m.participantCount} traders</span>
                    {m.viewerPosition && (
                      <span className="rounded-full bg-violet-500/20 px-2 py-0.5 text-xs text-violet-200">
                        You: {formatUsd(m.viewerPosition.amountUsd, 0)} {m.viewerPosition.side}
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
                </article>
              ))}
            </div>
          )}
        </section>
      </div>
    </main>
  );
}
