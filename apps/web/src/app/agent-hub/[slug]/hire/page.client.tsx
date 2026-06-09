'use client';

import Link from 'next/link';
import { useSession } from 'next-auth/react';
import { useRouter } from 'next/navigation';
import { FormEvent, useCallback, useEffect, useMemo, useState } from 'react';
import { SiteBrand, SiteNav } from '@/components/site-nav';
import { ExchangeApiGuideDrawer } from '@/components/agent-hub/exchange-api-guide-drawer';
import {
  AGENT_BETA_RISK_COPY,
  BITFINEX_RECOMMEND_BANNER,
  EXCHANGE_API_GUIDES,
  TRADING_AGENT_AI_PROVIDERS,
  TRADING_AGENT_AI_PROVIDER_LABELS,
  EXCHANGE_CREDENTIAL_CONFIG,
  exchangeRequiresPassphrase,
  type ExchangeProvider,
} from '@dcf/utils';
import {
  ExchangeProviderOption,
  fetchExchangeProviders,
  fetchTradingAgent,
  hireTradingAgent,
} from '@/lib/api';

type Step = 'exchange' | 'credentials' | 'ai' | 'risk';

export default function AgentHireClient({ slug }: { slug: string }) {
  const { data: session, status } = useSession();
  const router = useRouter();
  const token = session?.accessToken;

  const [step, setStep] = useState<Step>('exchange');
  const [providers, setProviders] = useState<ExchangeProviderOption[]>([]);
  const [agentName, setAgentName] = useState('');
  const [agentId, setAgentId] = useState('');
  const [costDay, setCostDay] = useState(0);
  const [exchange, setExchange] = useState('bitfinex');
  const [apiKey, setApiKey] = useState('');
  const [apiSecret, setApiSecret] = useState('');
  const [passphrase, setPassphrase] = useState('');
  const [testnet, setTestnet] = useState(false);
  const [aiProvider, setAiProvider] = useState('deepseek');
  const [aiApiKey, setAiApiKey] = useState('');
  const [riskAccepted, setRiskAccepted] = useState(false);
  const [guideOpen, setGuideOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [agent, ex] = await Promise.all([
        fetchTradingAgent(slug, token),
        fetchExchangeProviders(),
      ]);
      setAgentName(agent.name);
      setAgentId(agent.id);
      setCostDay(agent.costDdollarDay);
      setProviders(ex);
      if (agent.hired) {
        router.replace(`/agent-hub/${slug}/my-dashboard`);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load agent');
    }
  }, [slug, token, router]);

  useEffect(() => {
    if (status === 'loading') return;
    if (status === 'unauthenticated') {
      router.replace(`/login?callbackUrl=/agent-hub/${slug}/hire`);
      return;
    }
    load();
  }, [status, router, slug, load]);

  async function handleActivate(e: FormEvent) {
    e.preventDefault();
    if (!token || !exchange || !apiKey || !apiSecret || !riskAccepted) return;
    if (exchangeRequiresPassphrase(exchange as ExchangeProvider) && !passphrase.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const result = await hireTradingAgent(
        agentId,
        {
          exchangeProvider: exchange,
          apiKey,
          apiSecret,
          passphrase: passphrase.trim() || undefined,
          testnet,
          aiMode: 'own',
          aiProvider,
          aiApiKey,
        },
        token,
      );
      router.push(result.dashboardUrl);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Activation failed');
    } finally {
      setBusy(false);
    }
  }

  const sortedProviders = useMemo(() => {
    const copy = [...providers];
    copy.sort((a, b) => {
      if (a.id === 'bitfinex') return -1;
      if (b.id === 'bitfinex') return 1;
      if (a.available !== b.available) return a.available ? -1 : 1;
      return a.label.localeCompare(b.label);
    });
    return copy;
  }, [providers]);

  const selectedProvider = providers.find((p) => p.id === exchange);
  const exchangeFields = exchange
    ? EXCHANGE_CREDENTIAL_CONFIG[exchange as ExchangeProvider]
    : null;
  const exchangeGuide = exchange ? EXCHANGE_API_GUIDES[exchange as ExchangeProvider] : null;

  const steps: Step[] = ['exchange', 'credentials', 'ai', 'risk'];

  return (
    <main className="min-h-screen bg-[#050508] text-white">
      <header className="border-b border-zinc-800">
        <div className="mx-auto flex max-w-3xl flex-wrap items-center justify-between gap-4 px-6 py-4">
          <div>
            <SiteBrand className="text-sm" />
            <Link href={`/agent-hub/${slug}`} className="mt-1 block text-xs text-violet-400 hover:text-violet-300">
              ← Observe live showcase
            </Link>
          </div>
          <SiteNav />
        </div>
      </header>

      <div className="mx-auto max-w-3xl px-6 py-10">
        <p className="text-[10px] font-bold uppercase tracking-[0.2em] text-emerald-400">Hire agent</p>
        <h1 className="mt-1 text-2xl font-bold">{agentName || 'Agent'}</h1>
        <p className="mt-2 text-sm text-zinc-400">
          Your exchange + your AI keys. Admin showcase stays separate — you never share our credentials.
        </p>

        {error && (
          <p className="mt-4 rounded-lg border border-red-500/30 bg-red-950/30 px-4 py-2 text-sm text-red-200">
            {error}
          </p>
        )}

        <ol className="mt-8 flex flex-wrap gap-2 text-xs">
          {steps.map((s, i) => (
            <li
              key={s}
              className={`rounded-full px-3 py-1 ${
                step === s ? 'bg-emerald-600 text-white' : 'bg-zinc-900 text-zinc-500'
              }`}
            >
              {i + 1}.{' '}
              {s === 'exchange'
                ? 'Exchange'
                : s === 'credentials'
                  ? 'API keys'
                  : s === 'ai'
                    ? 'AI'
                    : 'Risk & activate'}
            </li>
          ))}
        </ol>

        {step === 'exchange' && (
          <section className="mt-8 space-y-4">
            <div className="rounded-xl border border-emerald-500/30 bg-emerald-950/20 px-4 py-3 text-sm text-emerald-100">
              {BITFINEX_RECOMMEND_BANNER}
            </div>
            <h2 className="font-semibold">Choose exchange</h2>
            <p className="text-xs text-zinc-500">
              Bitfinex is tested end-to-end. Other exchanges are listed for future support.
            </p>
            <div className="grid gap-2 sm:grid-cols-2">
              {sortedProviders.map((p) => {
                const recommended = p.id === 'bitfinex';
                return (
                  <button
                    key={p.id}
                    type="button"
                    disabled={!p.available}
                    onClick={() => {
                      setExchange(p.id);
                      setPassphrase('');
                      setStep('credentials');
                    }}
                    className={`rounded-xl border px-4 py-3 text-left text-sm transition ${
                      p.available
                        ? recommended
                          ? 'border-emerald-500/50 bg-emerald-950/20 hover:border-emerald-400'
                          : 'border-zinc-700 hover:border-violet-500/50 hover:bg-violet-950/20'
                        : 'cursor-not-allowed border-zinc-800 opacity-50'
                    }`}
                  >
                    <span className="font-medium">{p.label}</span>
                    {recommended && (
                      <span className="ml-2 rounded bg-emerald-600/30 px-1.5 py-0.5 text-[10px] font-bold text-emerald-200">
                        Recommended
                      </span>
                    )}
                    {!p.available && (
                      <span className="mt-1 block text-xs text-zinc-500">Coming soon</span>
                    )}
                  </button>
                );
              })}
            </div>
          </section>
        )}

        {step === 'credentials' && (
          <form
            className="mt-8 space-y-4"
            onSubmit={(e) => {
              e.preventDefault();
              if (!apiKey || !apiSecret) return;
              if (exchangeRequiresPassphrase(exchange as ExchangeProvider) && !passphrase.trim()) return;
              setStep('ai');
            }}
          >
            <h2 className="font-semibold">Connect {selectedProvider?.label ?? exchange}</h2>
            <div className="rounded-xl border border-zinc-800 bg-zinc-950/60 px-4 py-3 text-xs text-zinc-400">
              <strong className="text-zinc-200">Security:</strong> Founder OS encrypts credentials. You own your funds.
              Never enable withdraw permissions.
            </div>
            <button
              type="button"
              onClick={() => setGuideOpen(true)}
              className="text-sm text-violet-400 hover:text-violet-300"
            >
              How to create {selectedProvider?.label} API keys →
            </button>
            <label className="block text-sm">
              {exchangeFields?.apiKeyLabel ?? 'API Key'}
              <input
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
                placeholder={exchangeFields?.apiKeyPlaceholder}
                className="mt-1 w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 font-mono text-sm"
                required
              />
            </label>
            <label className="block text-sm">
              {exchangeFields?.apiSecretLabel ?? 'API Secret'}
              <input
                type="password"
                value={apiSecret}
                onChange={(e) => setApiSecret(e.target.value)}
                placeholder={exchangeFields?.apiSecretPlaceholder}
                className="mt-1 w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 font-mono text-sm"
                required
              />
            </label>
            {exchangeFields?.passphraseLabel ? (
              <label className="block text-sm">
                {exchangeFields.passphraseLabel}
                <input
                  type="password"
                  value={passphrase}
                  onChange={(e) => setPassphrase(e.target.value)}
                  className="mt-1 w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 font-mono text-sm"
                  required={exchangeFields.passphraseRequired}
                />
              </label>
            ) : null}
            {exchangeGuide && (
              <div className="grid gap-4 sm:grid-cols-2 text-xs">
                <div>
                  <p className="font-bold text-emerald-400">Required permissions</p>
                  <ul className="mt-1 space-y-0.5 text-zinc-400">
                    {exchangeGuide.requiredPermissions.map((p) => (
                      <li key={p}>✓ {p}</li>
                    ))}
                  </ul>
                </div>
                <div>
                  <p className="font-bold text-red-400">Do not enable</p>
                  <ul className="mt-1 space-y-0.5 text-zinc-500">
                    {exchangeGuide.forbiddenPermissions.map((p) => (
                      <li key={p}>✗ {p}</li>
                    ))}
                  </ul>
                </div>
              </div>
            )}
            <label className="flex items-center gap-2 text-sm text-zinc-400">
              <input type="checkbox" checked={testnet} onChange={(e) => setTestnet(e.target.checked)} />
              Use testnet credentials
            </label>
            <div className="flex gap-2">
              <button type="button" onClick={() => setStep('exchange')} className="rounded-lg border border-zinc-700 px-4 py-2 text-sm">
                Back
              </button>
              <button type="submit" className="rounded-lg bg-emerald-600 px-4 py-2 text-sm font-medium hover:bg-emerald-500">
                Continue
              </button>
            </div>
          </form>
        )}

        {step === 'ai' && (
          <form
            className="mt-8 space-y-4"
            onSubmit={(e) => {
              e.preventDefault();
              if (!aiApiKey.trim()) return;
              setStep('risk');
            }}
          >
            <h2 className="font-semibold">Connect AI</h2>
            <p className="text-sm text-zinc-500">
              BYOAI — the agent uses your provider for decisions. DeepSeek matches the public showcase.
            </p>
            <div className="flex flex-wrap gap-2">
              {TRADING_AGENT_AI_PROVIDERS.map((id) => (
                <button
                  key={id}
                  type="button"
                  onClick={() => setAiProvider(id)}
                  className={`rounded-full px-3 py-1 text-xs ${
                    aiProvider === id ? 'bg-violet-600 text-white' : 'border border-zinc-700 text-zinc-400'
                  }`}
                >
                  {TRADING_AGENT_AI_PROVIDER_LABELS[id]}
                  {id === 'deepseek' ? ' ★' : ''}
                </button>
              ))}
            </div>
            <label className="block text-sm">
              {TRADING_AGENT_AI_PROVIDER_LABELS[aiProvider as keyof typeof TRADING_AGENT_AI_PROVIDER_LABELS]} API key
              <input
                type="password"
                value={aiApiKey}
                onChange={(e) => setAiApiKey(e.target.value)}
                required
                className="mt-1 w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 font-mono text-sm"
              />
            </label>
            <div className="flex gap-2">
              <button type="button" onClick={() => setStep('credentials')} className="rounded-lg border border-zinc-700 px-4 py-2 text-sm">
                Back
              </button>
              <button type="submit" className="rounded-lg bg-emerald-600 px-4 py-2 text-sm font-medium hover:bg-emerald-500">
                Continue
              </button>
            </div>
          </form>
        )}

        {step === 'risk' && (
          <form className="mt-8 space-y-4" onSubmit={handleActivate}>
            <h2 className="font-semibold">{AGENT_BETA_RISK_COPY.title}</h2>
            <div className="rounded-xl border border-amber-500/40 bg-amber-950/30 p-4">
              <ul className="space-y-2 text-sm text-amber-100/90">
                {AGENT_BETA_RISK_COPY.bullets.map((b) => (
                  <li key={b}>• {b}</li>
                ))}
              </ul>
            </div>
            <div className="rounded-xl border border-zinc-800 bg-zinc-950/60 p-4 text-sm space-y-2">
              <p><span className="text-zinc-500">Agent:</span> {agentName}</p>
              <p><span className="text-zinc-500">Exchange:</span> {selectedProvider?.label ?? exchange}</p>
              <p><span className="text-zinc-500">AI:</span> {TRADING_AGENT_AI_PROVIDER_LABELS[aiProvider as keyof typeof TRADING_AGENT_AI_PROVIDER_LABELS]}</p>
              <p><span className="text-zinc-500">Rental:</span> {costDay.toLocaleString()} DDollar/day</p>
            </div>
            <label className="flex items-start gap-3 text-sm text-zinc-300">
              <input
                type="checkbox"
                checked={riskAccepted}
                onChange={(e) => setRiskAccepted(e.target.checked)}
                className="mt-1"
                required
              />
              {AGENT_BETA_RISK_COPY.checkboxLabel}
            </label>
            <div className="flex gap-2">
              <button type="button" onClick={() => setStep('ai')} className="rounded-lg border border-zinc-700 px-4 py-2 text-sm">
                Back
              </button>
              <button
                type="submit"
                disabled={busy || !riskAccepted}
                className="rounded-lg bg-emerald-600 px-4 py-2 text-sm font-semibold hover:bg-emerald-500 disabled:opacity-50"
              >
                {busy ? 'Activating…' : 'Activate on my account'}
              </button>
            </div>
          </form>
        )}
      </div>

      {exchange && (
        <ExchangeApiGuideDrawer
          provider={exchange as ExchangeProvider}
          open={guideOpen}
          onClose={() => setGuideOpen(false)}
        />
      )}
    </main>
  );
}
