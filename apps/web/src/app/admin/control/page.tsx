'use client';

import Link from 'next/link';
import { useSession } from 'next-auth/react';
import { useRouter } from 'next/navigation';
import { FormEvent, useCallback, useEffect, useState } from 'react';
import { PLATFORM_X_SHARE_FOOTER, formatPercent, formatUsd, TRADING_AGENT_AI_PROVIDERS, TRADING_AGENT_AI_PROVIDER_LABELS, EXCHANGE_PROVIDERS, EXCHANGE_PROVIDER_LABELS } from '@dcf/utils';
import { SiteNav } from '@/components/site-nav';
import { useShareFooterActions } from '@/components/share-footer-provider';
import {
  AdminControlOverview,
  fetchAdminControlOverview,
  fetchGlobalShareFooter,
  pauseTradingAgent,
  resumeTradingAgent,
  restartTradingAgent,
  resetShowcaseSimulation,
  updateShowcaseConfig,
  updateGlobalShareFooter,
} from '@/lib/api';

const SECTIONS = [
  { id: 'agent', label: 'Agent Control' },
  { id: 'social', label: 'Social Messaging' },
  { id: 'platform', label: 'Platform & Treasury' },
  { id: 'moderation', label: 'Moderation' },
] as const;

type SectionId = (typeof SECTIONS)[number]['id'];

export default function AdminControlPage() {
  const { data: session, status } = useSession();
  const router = useRouter();
  const token = session?.accessToken;
  const isAdmin = session?.user?.role === 'ADMIN';
  const { reload: reloadShareFooter } = useShareFooterActions();

  const [section, setSection] = useState<SectionId>('agent');
  const [overview, setOverview] = useState<AdminControlOverview | null>(null);
  const [footer, setFooter] = useState(PLATFORM_X_SHARE_FOOTER);
  const [msg, setMsg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!token) return;
    try {
      const [ov, foot] = await Promise.all([
        fetchAdminControlOverview(token),
        fetchGlobalShareFooter(),
      ]);
      setOverview(ov);
      setFooter(foot.footer);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load admin control');
    }
  }, [token]);

  useEffect(() => {
    if (status === 'loading') return;
    if (status === 'unauthenticated') {
      router.replace('/login?callbackUrl=/admin/control');
      return;
    }
    if (!isAdmin) {
      router.replace('/');
      return;
    }
    load();
  }, [status, isAdmin, router, load]);

  async function handleSaveFooter(e: FormEvent) {
    e.preventDefault();
    if (!token) return;
    setBusy('footer');
    setMsg(null);
    setError(null);
    try {
      await updateGlobalShareFooter(footer, token);
      reloadShareFooter();
      setMsg('Global share footer saved — all X share buttons will use this copy.');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Save failed');
    } finally {
      setBusy(null);
    }
  }

  async function handleAgentAction(action: 'pause' | 'resume' | 'restart' | 'reset') {
    if (!token) return;
    setBusy(action);
    setError(null);
    try {
      if (action === 'pause') {
        const res = await pauseTradingAgent(token);
        if (!res.ok) setError(typeof res.error === 'string' ? res.error : 'Pause failed');
        else setMsg('Trading paused on showcase runtime.');
      } else if (action === 'resume') {
        const res = await resumeTradingAgent(token);
        if (!res.ok) setError(typeof res.error === 'string' ? res.error : 'Resume failed');
        else setMsg('Trading resumed on showcase runtime.');
      } else if (action === 'restart') {
        const res = await restartTradingAgent(token);
        if (!res.ok) setError(typeof res.error === 'string' ? res.error : 'Restart failed');
        else setMsg('Showcase runtime restart requested.');
      } else {
        const res = await resetShowcaseSimulation(token);
        setMsg(res.message ?? (res.ok ? 'Simulation reset.' : 'Reset not wired yet.'));
      }
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Action failed');
    } finally {
      setBusy(null);
    }
  }

  async function handleShowcaseConfig(field: 'exchangeProvider' | 'aiProvider', value: string) {
    if (!token) return;
    setBusy(field);
    setError(null);
    try {
      const ov = await updateShowcaseConfig({ [field]: value }, token);
      setOverview(ov);
      setMsg('Showcase configuration saved.');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Save failed');
    } finally {
      setBusy(null);
    }
  }

  if (!isAdmin) return null;

  const runtime = overview?.runtime;
  const agent = overview?.agent;
  const infra = overview?.infrastructure;
  const showcase = overview?.showcase;
  const adapters = overview?.adapters;

  return (
    <div className="min-h-screen bg-[#050508] text-white">
      <header className="border-b border-zinc-800">
        <div className="mx-auto flex max-w-5xl flex-wrap items-center justify-between gap-4 px-6 py-4">
          <div>
            <Link href="/account" className="text-xs text-zinc-500 hover:text-white">
              ← Profile
            </Link>
            <h1 className="text-xl font-bold">Admin Control</h1>
            <p className="text-sm text-zinc-500">Platform operations — not visible to regular users</p>
          </div>
          <SiteNav />
        </div>
      </header>

      <main className="mx-auto flex max-w-5xl flex-col gap-6 px-6 py-8 lg:flex-row">
        <aside className="lg:w-48 lg:shrink-0">
          <nav className="flex flex-wrap gap-1 lg:flex-col">
            {SECTIONS.map((item) => (
              <button
                key={item.id}
                type="button"
                onClick={() => setSection(item.id)}
                className={`rounded-lg px-3 py-2 text-left text-sm transition ${
                  section === item.id
                    ? 'bg-amber-500/20 font-semibold text-amber-100 ring-1 ring-amber-500/40'
                    : 'text-zinc-400 hover:bg-zinc-900 hover:text-white'
                }`}
              >
                {item.label}
              </button>
            ))}
          </nav>
        </aside>

        <div className="min-w-0 flex-1 space-y-4">
          {msg && (
            <p className="rounded-lg border border-emerald-500/30 bg-emerald-950/20 px-4 py-3 text-sm text-emerald-200">
              {msg}
            </p>
          )}
          {error && (
            <p className="rounded-lg border border-red-500/30 bg-red-950/20 px-4 py-3 text-sm text-red-300">
              {error}
            </p>
          )}

          {section === 'agent' && (
            <section className="rounded-xl border border-zinc-800 bg-zinc-950/60 p-6">
              <h2 className="text-lg font-semibold">BTC Conservative Agent — Public Showcase</h2>
              <p className="mt-1 text-sm text-zinc-500">
                Admin-owned proof-of-skill dashboard. Your exchange + AI keys power this only — never user instances.
              </p>

              {!overview ? (
                <p className="mt-4 text-sm text-zinc-500">Loading runtime…</p>
              ) : (
                <>
                  <div className="mt-6 grid gap-4 lg:grid-cols-2">
                    <div className="rounded-lg border border-zinc-800 bg-black/20 p-4">
                      <p className="text-xs font-semibold uppercase tracking-wider text-zinc-500">Showcase exchange</p>
                      <div className="mt-3 flex flex-wrap gap-2">
                        {EXCHANGE_PROVIDERS.map((id) => (
                          <button
                            key={id}
                            type="button"
                            disabled={busy != null}
                            onClick={() => void handleShowcaseConfig('exchangeProvider', id)}
                            className={`rounded-full px-3 py-1 text-xs ${
                              showcase?.exchangeProvider === id
                                ? 'bg-amber-500/20 text-amber-100 ring-1 ring-amber-500/40'
                                : 'border border-zinc-700 text-zinc-400 hover:text-white'
                            }`}
                          >
                            {EXCHANGE_PROVIDER_LABELS[id]}
                          </button>
                        ))}
                      </div>
                    </div>
                    <div className="rounded-lg border border-zinc-800 bg-black/20 p-4">
                      <p className="text-xs font-semibold uppercase tracking-wider text-zinc-500">Showcase AI</p>
                      <div className="mt-3 flex flex-wrap gap-2">
                        {TRADING_AGENT_AI_PROVIDERS.map((id) => (
                          <button
                            key={id}
                            type="button"
                            disabled={busy != null}
                            onClick={() => void handleShowcaseConfig('aiProvider', id)}
                            className={`rounded-full px-3 py-1 text-xs ${
                              showcase?.aiProvider === id
                                ? 'bg-violet-500/20 text-violet-100 ring-1 ring-violet-500/40'
                                : 'border border-zinc-700 text-zinc-400 hover:text-white'
                            }`}
                          >
                            {TRADING_AGENT_AI_PROVIDER_LABELS[id]}
                          </button>
                        ))}
                      </div>
                    </div>
                  </div>

                  <div className="mt-6 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                    <Stat label="Exchange API" value={adapters?.exchangeStatus ?? '—'} />
                    <Stat label="Market data" value={adapters?.marketDataStatus ?? '—'} />
                    <Stat label="AI" value={adapters?.aiStatus ?? '—'} />
                    <Stat label="Simulation" value={adapters?.simulationStatus ?? '—'} />
                    <Stat label="Last decision" value={adapters?.lastDecision ?? '—'} />
                    <Stat label="Last AI opinion" value={adapters?.lastAiOpinion ?? '—'} />
                  </div>

                  <div className="mt-6 grid gap-3 sm:grid-cols-2">
                    <Stat label="Public status" value={runtime?.publicStatus ?? 'offline'} />
                    <Stat label="Railway runtime" value={infra?.botReachable ? 'Connected' : 'Not reachable'} />
                    <Stat label="DeepSeek" value={runtime?.deepSeekConnected ? 'Connected' : 'Unknown / offline'} />
                    <Stat label="Websocket" value={String(runtime?.wsHealth ?? '—')} />
                    <Stat label="Version" value={String(runtime?.deployVersion ?? '—')} />
                    <Stat label="Balance" value={agent ? formatUsd(agent.balanceUsd) : '—'} />
                    <Stat label="PnL" value={agent ? formatPercent(agent.netReturnPct) : '—'} />
                    <Stat label="Followers" value={agent ? String(agent.followerCount) : '—'} />
                  </div>

                  {runtime?.executionPaused && runtime.executionReason && (
                    <p className="mt-4 text-sm text-amber-200/90">
                      Paused: {runtime.executionReason}
                    </p>
                  )}

                  <div className="mt-6 flex flex-wrap gap-2">
                    <button
                      type="button"
                      disabled={busy != null}
                      onClick={() => void handleAgentAction('resume')}
                      className="rounded-lg bg-emerald-600 px-4 py-2 text-sm font-medium hover:bg-emerald-500 disabled:opacity-50"
                    >
                      Resume agent
                    </button>
                    <button
                      type="button"
                      disabled={busy != null}
                      onClick={() => void handleAgentAction('pause')}
                      className="rounded-lg border border-amber-500/40 px-4 py-2 text-sm text-amber-200 hover:bg-amber-950/30 disabled:opacity-50"
                    >
                      Pause agent
                    </button>
                    <button
                      type="button"
                      disabled={busy != null}
                      onClick={() => void handleAgentAction('restart')}
                      className="rounded-lg border border-zinc-700 px-4 py-2 text-sm text-zinc-300 hover:text-white disabled:opacity-50"
                    >
                      Restart runtime
                    </button>
                    <button
                      type="button"
                      disabled={busy != null}
                      onClick={() => void handleAgentAction('reset')}
                      className="rounded-lg border border-zinc-700 px-4 py-2 text-sm text-zinc-300 hover:text-white disabled:opacity-50"
                    >
                      Reset simulation
                    </button>
                    <Link
                      href="/agent-hub/conservative-btc"
                      className="rounded-lg border border-violet-500/40 bg-violet-950/20 px-4 py-2 text-sm text-violet-200 hover:text-white"
                    >
                      Open public dashboard →
                    </Link>
                  </div>

                  <div className="mt-6 rounded-lg border border-zinc-800 bg-black/20 p-4 text-xs text-zinc-400">
                    <p className="font-semibold text-zinc-300">Deployments</p>
                    <ol className="mt-2 list-decimal space-y-1 pl-4">
                      <li>Push bot code to Railway bot service</li>
                      <li>Redeploy container — fresh runtime, historical trades preserved in DB</li>
                      <li>Verify status here and on Agent Hub</li>
                    </ol>
                    <p className="mt-2">
                      Runtime host: {infra?.runtimeHost ?? 'not configured'} · Bridge:{' '}
                      {infra?.botConfigured ? 'configured' : 'missing env'}
                    </p>
                    {adapters?.lastMarketUpdate && (
                      <p className="mt-1">Last market update: {new Date(adapters.lastMarketUpdate).toLocaleString()}</p>
                    )}
                  </div>
                </>
              )}
            </section>
          )}

          {section === 'social' && (
            <section className="rounded-xl border border-zinc-800 bg-zinc-950/60 p-6">
              <h2 className="text-lg font-semibold">Social Messaging</h2>
              <p className="mt-1 text-sm text-zinc-500">
                One footer appended to every Share on X button — portfolio, agents, listings, feed, trust center.
              </p>
              <form onSubmit={handleSaveFooter} className="mt-4 space-y-3">
                <textarea
                  value={footer}
                  onChange={(e) => setFooter(e.target.value)}
                  rows={12}
                  className="w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 font-mono text-sm text-zinc-200"
                />
                <div className="flex flex-wrap gap-2">
                  <button
                    type="submit"
                    disabled={busy === 'footer'}
                    className="rounded-lg bg-violet-600 px-4 py-2 text-sm font-medium disabled:opacity-50"
                  >
                    {busy === 'footer' ? 'Saving…' : 'Save global footer'}
                  </button>
                  <button
                    type="button"
                    onClick={() => setFooter(PLATFORM_X_SHARE_FOOTER)}
                    className="rounded-lg border border-zinc-700 px-4 py-2 text-sm text-zinc-400"
                  >
                    Reset to default
                  </button>
                </div>
              </form>
            </section>
          )}

          {section === 'platform' && (
            <section className="space-y-4">
              <div className="rounded-xl border border-zinc-800 bg-zinc-950/60 p-6">
                <h2 className="font-semibold">Treasury & top-ups</h2>
                <Link href="/admin/platform" className="mt-2 inline-block text-sm text-violet-400 hover:underline">
                  Open treasury admin →
                </Link>
              </div>
              <div className="rounded-xl border border-zinc-800 bg-zinc-950/60 p-6">
                <h2 className="font-semibold">Listing inbox</h2>
                <Link href="/admin/applications" className="mt-2 inline-block text-sm text-violet-400 hover:underline">
                  Review applications →
                </Link>
              </div>
              <div className="rounded-xl border border-zinc-800 bg-zinc-950/60 p-6">
                <h2 className="font-semibold">Platform announcements</h2>
                <Link href="/feed?view=announcements" className="mt-2 inline-block text-sm text-violet-400 hover:underline">
                  Town Hall → Feed announcements →
                </Link>
              </div>
            </section>
          )}

          {section === 'moderation' && (
            <section className="rounded-xl border border-zinc-800 bg-zinc-950/60 p-6">
              <h2 className="font-semibold">Moderation</h2>
              <p className="mt-2 text-sm text-zinc-500">
                Trust Center investigations, listing decisions, and delist requests.
              </p>
              <Link href="/trust-center" className="mt-4 inline-block text-sm text-violet-400 hover:underline">
                Open Trust Center →
              </Link>
            </section>
          )}
        </div>
      </main>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-zinc-800/80 bg-black/20 px-3 py-2">
      <p className="text-[10px] uppercase tracking-wider text-zinc-500">{label}</p>
      <p className="mt-1 text-sm font-semibold capitalize text-white">{value}</p>
    </div>
  );
}
