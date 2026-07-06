'use client';

import Link from 'next/link';
import { useSession } from 'next-auth/react';
import { useRouter } from 'next/navigation';
import { FormEvent, useCallback, useEffect, useState } from 'react';
import { PLATFORM_X_SHARE_FOOTER } from '@dcf/utils';
import { SiteNav } from '@/components/site-nav';
import { ResearchBotDetailDashboard } from '@/components/agent-hub/research-bot-detail-dashboard';
import { useShareFooterActions } from '@/components/share-footer-provider';
import { AdminFounderPromoPanel } from '@/components/account/admin-founder-promo-panel';
import { AdminAiKeysPanel } from '@/components/admin/admin-ai-keys-panel';
import { AdminBuilderBreakdownPanel } from '@/components/admin/admin-builder-breakdown-panel';
import {
  AdminControlOverview,
  fetchAccountOverview,
  fetchAdminControlOverview,
  fetchAdminResearchDashboard,
  fetchGlobalShareFooter,
  updateShowcaseConfig,
  updateGlobalShareFooter,
} from '@/lib/api';

const SECTIONS = [
  { id: 'ai-keys', label: 'AI Keys' },
  { id: 'builders', label: 'Builders' },
  { id: 'research', label: 'Research Dashboard' },
  { id: 'social', label: 'Social Messaging' },
  { id: 'platform', label: 'Platform & Treasury' },
  { id: 'moderation', label: 'Moderation' },
] as const;

type SectionId = (typeof SECTIONS)[number]['id'];

export default function AdminControlPage() {
  const { data: session, status } = useSession();
  const router = useRouter();
  const token = session?.accessToken;
  const sessionAdmin = session?.user?.role === 'ADMIN';
  const [accountAdmin, setAccountAdmin] = useState(false);
  const isAdmin = sessionAdmin || accountAdmin;
  const { reload: reloadShareFooter } = useShareFooterActions();

  const [section, setSection] = useState<SectionId>('platform');
  const [overview, setOverview] = useState<AdminControlOverview | null>(null);
  const [footer, setFooter] = useState(PLATFORM_X_SHARE_FOOTER);
  const [msg, setMsg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [defaultSettings, setDefaultSettings] = useState('');
  const [subscriberMaxMarginUsd, setSubscriberMaxMarginUsd] = useState(20);
  const [researchRaw, setResearchRaw] = useState<Record<string, unknown> | null>(null);
  const [researchVersion, setResearchVersion] = useState<string | null>(null);
  const [researchUpdated, setResearchUpdated] = useState<string>('');
  const [researchAutoRefresh, setResearchAutoRefresh] = useState(false);

  const load = useCallback(async () => {
    if (!token) return;
    try {
      const foot = await fetchGlobalShareFooter();
      setFooter(foot.footer);
    } catch {
      /* footer is optional — keep default */
    }
    try {
      const ov = await fetchAdminControlOverview(token);
      setOverview(ov);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load admin control');
    }
    try {
      const research = await fetchAdminResearchDashboard(token);
      setResearchRaw(research.rawBotState);
      setResearchVersion(research.botVersion);
      setResearchUpdated(research.updatedAt);
    } catch {
      setResearchRaw(null);
    }
  }, [token]);

  useEffect(() => {
    if (status === 'loading') return;
    if (status === 'unauthenticated') {
      router.replace('/login?callbackUrl=/admin/control');
      return;
    }
    if (token && !sessionAdmin) {
      fetchAccountOverview(token)
        .then((ov) => setAccountAdmin(ov.isAdmin))
        .catch(() => setAccountAdmin(false));
    }
  }, [status, token, sessionAdmin, router]);

  useEffect(() => {
    if (status === 'loading') return;
    if (status === 'unauthenticated') return;
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

  async function handleSaveDefaultSettings(e: FormEvent) {
    e.preventDefault();
    if (!token) return;
    setBusy('default-settings');
    setError(null);
    try {
      const ov = await updateShowcaseConfig(
        {
          agentShowcaseDefaultSettings: defaultSettings,
          subscriberMaxMarginUsd: Number(subscriberMaxMarginUsd),
        },
        token,
      );
      setOverview(ov);
      setMsg('Hire rules saved — max margin per trade and public message updated.');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Save failed');
    } finally {
      setBusy(null);
    }
  }

  const refreshResearch = useCallback(async () => {
    if (!token) return;
    try {
      const research = await fetchAdminResearchDashboard(token);
      setResearchRaw(research.rawBotState);
      setResearchVersion(research.botVersion ? String(research.botVersion) : null);
      setResearchUpdated(research.updatedAt);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Research dashboard unavailable');
    }
  }, [token]);

  useEffect(() => {
    const msg = overview?.showcase?.agentShowcaseDefaultSettings;
    if (msg != null) setDefaultSettings(msg);
    const margin = overview?.showcase?.subscriberMaxMarginUsd;
    if (margin != null && margin > 0) setSubscriberMaxMarginUsd(margin);
  }, [overview?.showcase?.agentShowcaseDefaultSettings, overview?.showcase?.subscriberMaxMarginUsd]);

  useEffect(() => {
    if (section !== 'research' || !researchAutoRefresh || !token) return;
    const id = setInterval(() => void refreshResearch(), 5000);
    return () => clearInterval(id);
  }, [section, researchAutoRefresh, token, refreshResearch]);

  if (status === 'loading') {
    return (
      <div className="flex min-h-screen items-center justify-center bg-[#050508] text-zinc-400">
        Loading admin control…
      </div>
    );
  }

  if (!isAdmin) return null;

  const runtime = overview?.runtime;

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

      <main className={`mx-auto flex flex-col gap-6 px-6 py-8 lg:flex-row ${section === 'research' ? 'max-w-[90rem]' : 'max-w-5xl'}`}>
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
          <div className="rounded-xl border border-amber-500/40 bg-amber-950/20 px-4 py-3 text-sm text-amber-100">
            <p className="font-semibold text-amber-50">Neon database compute</p>
            <p className="mt-1 text-xs text-amber-200/90">
              If Neon emailed that your monthly CU-hours are exhausted, admin panels and DB writes may fail until you
              upgrade to the{' '}
              <a
                href="https://console.neon.tech"
                target="_blank"
                rel="noopener noreferrer"
                className="font-semibold underline"
              >
                Neon Launch plan
              </a>{' '}
              or the quota resets next month. GitHub + Vercel deploys still work; Railway API needs Postgres for admin
              data.
            </p>
          </div>

          <div className="rounded-xl border border-violet-500/40 bg-violet-950/20 px-4 py-3 text-sm text-violet-100">
            <Link href="/agent-hub/conservative-btc" className="font-semibold text-violet-50 hover:underline">
              Showcase + live copy control → Agent Hub
            </Link>
          </div>

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

          {section === 'research' && (
            <section className="space-y-6">
              <div className="rounded-xl border border-red-500/30 bg-red-950/15 p-5">
                <h2 className="text-lg font-semibold text-red-100">Research dashboard (admin only)</h2>
                <p className="mt-1 text-sm text-zinc-400">
                  Full pipeline state, AI inputs, and debug data. Never exposed on public agent pages.
                </p>
                <div className="mt-4 flex flex-wrap items-center gap-3 text-sm">
                  <span className="rounded-full border border-zinc-700 px-3 py-1">
                    Bot script:{' '}
                    <strong className="text-white">
                      {researchVersion ??
                        (runtime?.deployVersion != null ? String(runtime.deployVersion) : '—')}
                    </strong>
                  </span>
                  <span className="rounded-full border border-zinc-700 px-3 py-1 capitalize">
                    Status: {runtime?.publicStatus ?? '—'}
                  </span>
                  {runtime?.executionPaused && (
                    <span className="rounded-full border border-amber-500/40 bg-amber-950/30 px-3 py-1 text-amber-200">
                      Paused ({runtime.executionReason ?? 'unknown'})
                    </span>
                  )}
                </div>
                <div className="mt-4 flex flex-wrap gap-2">
                  <button
                    type="button"
                    onClick={() => void refreshResearch()}
                    className="rounded-lg border border-zinc-700 px-4 py-2 text-sm text-zinc-300 hover:text-white"
                  >
                    Refresh snapshot
                  </button>
                  <label className="flex items-center gap-2 rounded-lg border border-zinc-800 px-3 py-2 text-xs text-zinc-400">
                    <input
                      type="checkbox"
                      checked={researchAutoRefresh}
                      onChange={(e) => setResearchAutoRefresh(e.target.checked)}
                    />
                    Auto-refresh (5s)
                  </label>
                </div>
              </div>

              {researchRaw ? (
                <ResearchBotDetailDashboard
                  raw={researchRaw}
                  updatedAt={researchUpdated || new Date().toISOString()}
                  onRefresh={() => void refreshResearch()}
                  autoRefresh={researchAutoRefresh}
                  onAutoRefreshChange={setResearchAutoRefresh}
                />
              ) : (
                <div className="rounded-xl border border-zinc-800 bg-zinc-950/60 p-8 text-center text-sm text-zinc-500">
                  Bot not connected — configure TRADING_AGENT_BOT_URL and ensure Railway runtime is online.
                </div>
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
              {token && <AdminFounderPromoPanel accessToken={token} hideKeyCards />}
              <form onSubmit={handleSaveDefaultSettings} className="rounded-xl border border-amber-500/25 bg-amber-950/10 p-6">
                <h2 className="font-semibold text-amber-200">Hire rules & public message</h2>
                <p className="mt-1 text-sm text-zinc-500">
                  Max margin per trade is enforced server-side on every Bitfinex copy order. Users cannot override
                  via API keys or balance.
                </p>
                <label className="mt-3 block text-sm">
                  <span className="text-zinc-400">Max margin per trade (USD)</span>
                  <input
                    type="number"
                    min={1}
                    max={500}
                    step={1}
                    value={subscriberMaxMarginUsd}
                    onChange={(e) => setSubscriberMaxMarginUsd(Number(e.target.value))}
                    className="mt-1 w-full max-w-[12rem] rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm"
                  />
                </label>
                <p className="mt-1 text-xs text-zinc-500">
                  Default $20 — matches showcase bot. Change here when you want to raise the platform cap.
                </p>
                <textarea
                  value={defaultSettings}
                  onChange={(e) => setDefaultSettings(e.target.value)}
                  rows={4}
                  className="mt-3 w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm text-zinc-200"
                  placeholder="Live Bitfinex copy — $20 max margin per trade enforced by platform…"
                />
                <button
                  type="submit"
                  disabled={busy === 'default-settings'}
                  className="mt-3 rounded-lg bg-amber-600 px-4 py-2 text-sm font-medium hover:bg-amber-500 disabled:opacity-50"
                >
                  {busy === 'default-settings' ? 'Saving…' : 'Save hire rules'}
                </button>
              </form>
              <div className="rounded-xl border border-zinc-800 bg-zinc-950/60 p-6">
                <h2 className="font-semibold">Treasury & top-ups</h2>
                <p className="mt-1 text-sm text-zinc-500">
                  Connect Phantom under Account → Security first, then save that address as the Solana treasury.
                </p>
                <div className="mt-3 flex flex-wrap gap-4">
                  <Link href="/account?tab=security" className="text-sm text-violet-400 hover:underline">
                    Connect Phantom (Account → Security) →
                  </Link>
                  <Link href="/admin/platform" className="text-sm text-violet-400 hover:underline">
                    Save treasury address →
                  </Link>
                  <Link href="/admin/agent-registrations" className="text-sm text-violet-400 hover:underline">
                    Agent registrations (SAID / Spawn) →
                  </Link>
                </div>
              </div>
              <div className="rounded-xl border border-zinc-800 bg-zinc-950/60 p-6">
                <h2 className="font-semibold">Demo Mode (E2E testing)</h2>
                <p className="mt-1 text-sm text-zinc-500">
                  Seed synthetic users, projects, Raise Room data, and run API smoke checks — admin only.
                </p>
                <Link href="/admin/demo" className="mt-2 inline-block text-sm text-violet-400 hover:underline">
                  Open Demo Control Panel →
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
          {section === 'ai-keys' && token && (
            <AdminAiKeysPanel
              token={token}
              overview={overview}
              onOverviewChange={setOverview}
            />
          )}
          {section === 'builders' && token && <AdminBuilderBreakdownPanel accessToken={token} />}
        </div>
      </main>
    </div>
  );
}
