'use client';

import Link from 'next/link';
import { useSession } from 'next-auth/react';
import { useRouter } from 'next/navigation';
import { FormEvent, useCallback, useEffect, useState } from 'react';
import { PLATFORM_X_SHARE_FOOTER, formatPercent, formatUsd, TRADING_AGENT_AI_PROVIDERS, TRADING_AGENT_AI_PROVIDER_LABELS, EXCHANGE_PROVIDERS, EXCHANGE_PROVIDER_LABELS, EXCHANGE_CREDENTIAL_CONFIG, type ExchangeProvider } from '@dcf/utils';
import { SiteNav } from '@/components/site-nav';
import { ResearchBotDetailDashboard } from '@/components/agent-hub/research-bot-detail-dashboard';
import { useShareFooterActions } from '@/components/share-footer-provider';
import {
  AdminControlOverview,
  fetchAdminControlOverview,
  fetchAdminResearchDashboard,
  fetchGlobalShareFooter,
  pauseTradingAgent,
  resumeTradingAgent,
  restartTradingAgent,
  resetShowcaseSimulation,
  updateShowcaseConfig,
  updateGlobalShareFooter,
  saveShowcaseCredentials,
  pushShowcaseRuntime,
} from '@/lib/api';

const SECTIONS = [
  { id: 'agent', label: 'Agent Control' },
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
  const isAdmin = session?.user?.role === 'ADMIN';
  const { reload: reloadShareFooter } = useShareFooterActions();

  const [section, setSection] = useState<SectionId>('agent');
  const [overview, setOverview] = useState<AdminControlOverview | null>(null);
  const [footer, setFooter] = useState(PLATFORM_X_SHARE_FOOTER);
  const [msg, setMsg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [exchangeApiKey, setExchangeApiKey] = useState('');
  const [exchangeApiSecret, setExchangeApiSecret] = useState('');
  const [exchangePassphrase, setExchangePassphrase] = useState('');
  const [aiApiKey, setAiApiKey] = useState('');
  const [botPublicUrl, setBotPublicUrl] = useState('');
  const [showcaseTestnet, setShowcaseTestnet] = useState(false);
  const [defaultSettings, setDefaultSettings] = useState('');
  const [researchRaw, setResearchRaw] = useState<Record<string, unknown> | null>(null);
  const [researchVersion, setResearchVersion] = useState<string | null>(null);
  const [researchUpdated, setResearchUpdated] = useState<string>('');
  const [researchAutoRefresh, setResearchAutoRefresh] = useState(false);

  const load = useCallback(async () => {
    if (!token) return;
    try {
      const [ov, foot] = await Promise.all([
        fetchAdminControlOverview(token),
        fetchGlobalShareFooter(),
      ]);
      setOverview(ov);
      setFooter(foot.footer);
      if (ov.showcase?.botPublicUrl) setBotPublicUrl(ov.showcase.botPublicUrl);
      setError(null);
      try {
        const research = await fetchAdminResearchDashboard(token);
        setResearchRaw(research.rawBotState);
        setResearchVersion(research.botVersion);
        setResearchUpdated(research.updatedAt);
      } catch {
        setResearchRaw(null);
      }
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
        if (!res.ok) setError(typeof res.error === 'string' ? res.error : 'Kill failed');
        else setMsg(typeof res.message === 'string' ? res.message : 'Showcase bot killed on Railway.');
      } else if (action === 'resume') {
        const res = await resumeTradingAgent(token);
        if (!res.ok) setError(typeof res.error === 'string' ? res.error : 'Start failed');
        else setMsg(typeof res.message === 'string' ? res.message : 'Showcase bot starting on Railway.');
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

  async function handleSaveShowcaseCredentials(e: FormEvent) {
    e.preventDefault();
    if (!token) return;
    setBusy('credentials');
    setError(null);
    try {
      const ov = await saveShowcaseCredentials(
        {
          exchangeProvider: showcase?.exchangeProvider,
          aiProvider: showcase?.aiProvider,
          exchangeApiKey: exchangeApiKey.trim() || undefined,
          exchangeApiSecret: exchangeApiSecret.trim() || undefined,
          exchangePassphrase: exchangePassphrase.trim() || undefined,
          aiApiKey: aiApiKey.trim() || undefined,
          botPublicUrl: botPublicUrl.trim() || undefined,
          testnet: showcaseTestnet,
        },
        token,
      );
      setOverview(ov);
      setExchangeApiKey('');
      setExchangeApiSecret('');
      setExchangePassphrase('');
      setAiApiKey('');
      setMsg('Showcase API keys saved (encrypted at rest).');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Save failed');
    } finally {
      setBusy(null);
    }
  }

  async function handlePushRuntime() {
    if (!token) return;
    setBusy('push-runtime');
    setError(null);
    try {
      const res = await pushShowcaseRuntime(token);
      if (!res.ok) setError(res.message);
      else setMsg(res.message);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Push failed');
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

  async function handleSaveDefaultSettings(e: FormEvent) {
    e.preventDefault();
    if (!token) return;
    setBusy('default-settings');
    setError(null);
    try {
      const ov = await updateShowcaseConfig({ agentShowcaseDefaultSettings: defaultSettings }, token);
      setOverview(ov);
      setMsg('Public default settings message saved — shown on agent hire sidebar.');
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
  }, [overview?.showcase?.agentShowcaseDefaultSettings]);

  useEffect(() => {
    if (section !== 'research' || !researchAutoRefresh || !token) return;
    const id = setInterval(() => void refreshResearch(), 5000);
    return () => clearInterval(id);
  }, [section, researchAutoRefresh, token, refreshResearch]);

  if (!isAdmin) return null;

  const runtime = overview?.runtime;
  const agent = overview?.agent;
  const infra = overview?.infrastructure;
  const showcase = overview?.showcase;
  const adapters = overview?.adapters;
  const exchangeProvider = (showcase?.exchangeProvider ?? 'bybit') as ExchangeProvider;
  const exchangeFields = EXCHANGE_CREDENTIAL_CONFIG[exchangeProvider] ?? EXCHANGE_CREDENTIAL_CONFIG.bybit;

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

                  <form
                    onSubmit={handleSaveShowcaseCredentials}
                    className="mt-6 space-y-4 rounded-lg border border-amber-500/20 bg-amber-950/10 p-4"
                  >
                    <p className="text-sm font-semibold text-amber-100">Showcase API keys (admin only)</p>
                    <p className="text-xs text-zinc-500">
                      Visitors see live performance from these keys. After hire, users connect their own exchange
                      and optionally their own AI (DeepSeek recommended — same stack as the public proof).
                    </p>
                    {(showcase?.botRuntimeNote || showcase?.aiRuntimeNote) && (
                      <p className="text-xs text-amber-200/80">
                        {showcase.botRuntimeNote}
                        {showcase.botRuntimeNote && showcase.aiRuntimeNote ? ' ' : ''}
                        {showcase.aiRuntimeNote}
                      </p>
                    )}
                    <div className="grid gap-4 lg:grid-cols-2">
                      <label className="block text-sm">
                        <span className="text-zinc-400">
                          {exchangeFields.apiKeyLabel}
                          {showcase?.exchangeConfigured ? (
                            <span className="ml-2 text-emerald-400">· saved</span>
                          ) : null}
                        </span>
                        <input
                          type="password"
                          autoComplete="off"
                          value={exchangeApiKey}
                          onChange={(e) => setExchangeApiKey(e.target.value)}
                          placeholder={exchangeFields.apiKeyPlaceholder ?? (showcase?.exchangeConfigured ? 'Leave blank to keep' : 'Required')}
                          className="mt-1 w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 font-mono text-sm"
                        />
                      </label>
                      <label className="block text-sm">
                        <span className="text-zinc-400">{exchangeFields.apiSecretLabel}</span>
                        <input
                          type="password"
                          autoComplete="off"
                          value={exchangeApiSecret}
                          onChange={(e) => setExchangeApiSecret(e.target.value)}
                          placeholder={exchangeFields.apiSecretPlaceholder}
                          className="mt-1 w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 font-mono text-sm"
                        />
                      </label>
                      {exchangeFields.passphraseLabel ? (
                        <label className="block text-sm lg:col-span-2">
                          <span className="text-zinc-400">
                            {exchangeFields.passphraseLabel}
                            {exchangeFields.passphraseRequired ? ' (required)' : ''}
                          </span>
                          <input
                            type="password"
                            autoComplete="off"
                            value={exchangePassphrase}
                            onChange={(e) => setExchangePassphrase(e.target.value)}
                            placeholder={exchangeFields.passphrasePlaceholder ?? (showcase?.exchangeConfigured ? 'Leave blank to keep' : undefined)}
                            className="mt-1 w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 font-mono text-sm"
                          />
                        </label>
                      ) : null}
                      {exchangeFields.helpText ? (
                        <p className="text-xs text-zinc-500 lg:col-span-2">{exchangeFields.helpText}</p>
                      ) : null}
                      <label className="block text-sm lg:col-span-2">
                        <span className="text-zinc-400">
                          {showcase?.aiLabel ?? 'AI'} API key
                          {showcase?.aiConfigured ? (
                            <span className="ml-2 text-emerald-400">· saved</span>
                          ) : null}
                        </span>
                        <input
                          type="password"
                          autoComplete="off"
                          value={aiApiKey}
                          onChange={(e) => setAiApiKey(e.target.value)}
                          placeholder={showcase?.aiConfigured ? 'Leave blank to keep' : 'Required for live AI'}
                          className="mt-1 w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 font-mono text-sm"
                        />
                      </label>
                      <label className="block text-sm lg:col-span-2">
                        <span className="text-zinc-400">Bot public URL (Railway)</span>
                        <input
                          type="url"
                          value={botPublicUrl}
                          onChange={(e) => setBotPublicUrl(e.target.value)}
                          placeholder="https://your-btc-bot.up.railway.app"
                          className="mt-1 w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 font-mono text-sm"
                        />
                        <span className="mt-1 block text-xs text-zinc-500">
                          Sets TRADING_AGENT_BOT_URL on the API service when you push runtime.
                        </span>
                      </label>
                    </div>
                    <label className="flex items-center gap-2 text-sm text-zinc-400">
                      <input
                        type="checkbox"
                        checked={showcaseTestnet}
                        onChange={(e) => setShowcaseTestnet(e.target.checked)}
                      />
                      Testnet exchange credentials
                    </label>
                    <div className="flex flex-wrap gap-2">
                      <button
                        type="submit"
                        disabled={busy != null}
                        className="rounded-lg bg-amber-600 px-4 py-2 text-sm font-medium hover:bg-amber-500 disabled:opacity-50"
                      >
                        {busy === 'credentials' ? 'Saving…' : 'Save showcase keys'}
                      </button>
                      <button
                        type="button"
                        disabled={busy != null || !showcase?.exchangeConfigured}
                        onClick={() => void handlePushRuntime()}
                        className="rounded-lg border border-violet-500/40 bg-violet-950/30 px-4 py-2 text-sm text-violet-200 hover:text-white disabled:opacity-50"
                      >
                        {busy === 'push-runtime' ? 'Pushing…' : 'Apply to Railway runtime'}
                      </button>
                    </div>
                    {showcase?.credentialsUpdatedAt && (
                      <p className="text-xs text-zinc-500">
                        Keys updated {new Date(showcase.credentialsUpdatedAt).toLocaleString()}
                        {showcase.runtimePushedAt
                          ? ` · Runtime pushed ${new Date(showcase.runtimePushedAt).toLocaleString()}`
                          : ''}
                      </p>
                    )}
                  </form>

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

                  <form onSubmit={handleSaveDefaultSettings} className="mt-6 rounded-lg border border-amber-500/25 bg-amber-950/10 p-4">
                    <p className="text-sm font-semibold text-amber-200">Public default settings message</p>
                    <p className="mt-1 text-xs text-zinc-500">
                      Shown on the agent hire sidebar. Users may override with experimental settings at their own risk.
                    </p>
                    <textarea
                      value={defaultSettings}
                      onChange={(e) => setDefaultSettings(e.target.value)}
                      rows={4}
                      className="mt-3 w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm text-zinc-200"
                      placeholder="Conservative edge threshold, RESEARCH mode, max $500 allocation…"
                    />
                    <button
                      type="submit"
                      disabled={busy === 'default-settings'}
                      className="mt-3 rounded-lg bg-amber-600 px-4 py-2 text-sm font-medium hover:bg-amber-500 disabled:opacity-50"
                    >
                      {busy === 'default-settings' ? 'Saving…' : 'Save default settings message'}
                    </button>
                  </form>

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
                      className="rounded-lg border border-red-500/40 bg-red-950/30 px-4 py-2 text-sm text-red-200 hover:bg-red-950/50 disabled:opacity-50"
                    >
                      Kill showcase bot
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
                    disabled={busy != null}
                    onClick={() => void handleAgentAction('pause')}
                    className="rounded-lg border border-red-500/40 bg-red-950/30 px-4 py-2 text-sm text-red-200 hover:bg-red-950/50 disabled:opacity-50"
                  >
                    Kill showcase bot
                  </button>
                  <button
                    type="button"
                    disabled={busy != null}
                    onClick={() => void handleAgentAction('resume')}
                    className="rounded-lg bg-emerald-600 px-4 py-2 text-sm font-medium hover:bg-emerald-500 disabled:opacity-50"
                  >
                    Start showcase bot
                  </button>
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
