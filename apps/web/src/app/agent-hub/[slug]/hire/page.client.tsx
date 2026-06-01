'use client';

import Link from 'next/link';
import { useSession } from 'next-auth/react';
import { useRouter } from 'next/navigation';
import { FormEvent, useCallback, useEffect, useState } from 'react';
import { SiteBrand, SiteNav } from '@/components/site-nav';
import {
  ExchangeProviderOption,
  fetchExchangeProviders,
  fetchTradingAgent,
  hireTradingAgent,
} from '@/lib/api';

type Step = 'exchange' | 'credentials' | 'confirm';

export default function AgentHireClient({ slug }: { slug: string }) {
  const { data: session, status } = useSession();
  const router = useRouter();
  const token = session?.accessToken;

  const [step, setStep] = useState<Step>('exchange');
  const [providers, setProviders] = useState<ExchangeProviderOption[]>([]);
  const [agentName, setAgentName] = useState('');
  const [agentId, setAgentId] = useState('');
  const [costDay, setCostDay] = useState(0);
  const [exchange, setExchange] = useState('');
  const [apiKey, setApiKey] = useState('');
  const [apiSecret, setApiSecret] = useState('');
  const [testnet, setTestnet] = useState(false);
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
    if (!token || !exchange || !apiKey || !apiSecret) return;
    setBusy(true);
    setError(null);
    try {
      const result = await hireTradingAgent(
        agentId,
        { exchangeProvider: exchange, apiKey, apiSecret, testnet },
        token,
      );
      router.push(result.dashboardUrl);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Activation failed');
    } finally {
      setBusy(false);
    }
  }

  const selectedProvider = providers.find((p) => p.id === exchange);

  return (
    <main className="min-h-screen bg-[#050508] text-white">
      <header className="border-b border-zinc-800">
        <div className="mx-auto flex max-w-3xl flex-wrap items-center justify-between gap-4 px-6 py-4">
          <div>
            <SiteBrand className="text-sm" />
            <Link href={`/agent-hub/${slug}`} className="mt-1 block text-xs text-violet-400 hover:text-violet-300">
              ← Public showcase
            </Link>
          </div>
          <SiteNav />
        </div>
      </header>

      <div className="mx-auto max-w-3xl px-6 py-10">
        <h1 className="text-2xl font-bold">Hire {agentName || 'Agent'}</h1>
        <p className="mt-2 text-sm text-zinc-400">
          Connect your exchange API only. Platform AI is included — no AI key required.
        </p>

        {error && (
          <p className="mt-4 rounded-lg border border-red-500/30 bg-red-950/30 px-4 py-2 text-sm text-red-200">
            {error}
          </p>
        )}

        <ol className="mt-8 flex gap-2 text-xs">
          {(['exchange', 'credentials', 'confirm'] as Step[]).map((s, i) => (
            <li
              key={s}
              className={`rounded-full px-3 py-1 ${
                step === s ? 'bg-violet-600 text-white' : 'bg-zinc-900 text-zinc-500'
              }`}
            >
              {i + 1}. {s === 'exchange' ? 'Exchange' : s === 'credentials' ? 'API Keys' : 'Activate'}
            </li>
          ))}
        </ol>

        {step === 'exchange' && (
          <section className="mt-8 space-y-4">
            <h2 className="font-semibold">Choose exchange</h2>
            <div className="grid gap-2 sm:grid-cols-2">
              {providers.map((p) => (
                <button
                  key={p.id}
                  type="button"
                  disabled={!p.available}
                  onClick={() => {
                    setExchange(p.id);
                    setStep('credentials');
                  }}
                  className={`rounded-xl border px-4 py-3 text-left text-sm transition ${
                    p.available
                      ? 'border-zinc-700 hover:border-violet-500/50 hover:bg-violet-950/20'
                      : 'cursor-not-allowed border-zinc-800 opacity-50'
                  }`}
                >
                  <span className="font-medium">{p.label}</span>
                  {!p.available && (
                    <span className="mt-1 block text-xs text-zinc-500">Coming soon</span>
                  )}
                </button>
              ))}
            </div>
          </section>
        )}

        {step === 'credentials' && (
          <form
            className="mt-8 space-y-4"
            onSubmit={(e) => {
              e.preventDefault();
              if (apiKey && apiSecret) setStep('confirm');
            }}
          >
            <h2 className="font-semibold">
              Connect {selectedProvider?.label ?? exchange} API
            </h2>
            <p className="text-sm text-zinc-500">
              Your keys power your private instance only. Admin showcase keys are never used.
            </p>
            <label className="block text-sm">
              API Key
              <input
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
                className="mt-1 w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 font-mono text-sm"
                required
              />
            </label>
            <label className="block text-sm">
              API Secret
              <input
                type="password"
                value={apiSecret}
                onChange={(e) => setApiSecret(e.target.value)}
                className="mt-1 w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 font-mono text-sm"
                required
              />
            </label>
            <label className="flex items-center gap-2 text-sm text-zinc-400">
              <input type="checkbox" checked={testnet} onChange={(e) => setTestnet(e.target.checked)} />
              Use testnet credentials
            </label>
            <div className="flex gap-2">
              <button
                type="button"
                onClick={() => setStep('exchange')}
                className="rounded-lg border border-zinc-700 px-4 py-2 text-sm"
              >
                Back
              </button>
              <button
                type="submit"
                className="rounded-lg bg-violet-600 px-4 py-2 text-sm font-medium hover:bg-violet-500"
              >
                Continue
              </button>
            </div>
          </form>
        )}

        {step === 'confirm' && (
          <form className="mt-8 space-y-4" onSubmit={handleActivate}>
            <h2 className="font-semibold">Confirm activation</h2>
            <div className="rounded-xl border border-zinc-800 bg-zinc-950/60 p-4 text-sm space-y-2">
              <p>
                <span className="text-zinc-500">Agent:</span> {agentName}
              </p>
              <p>
                <span className="text-zinc-500">Exchange:</span> {selectedProvider?.label ?? exchange}
              </p>
              <p>
                <span className="text-zinc-500">AI:</span> Included by platform (DeepSeek)
              </p>
              <p>
                <span className="text-zinc-500">Rental:</span> {costDay.toLocaleString()} DDollar/day
              </p>
            </div>
            <p className="text-xs text-zinc-500">
              Permissions: read balances, read positions, place/cancel orders on your account only.
            </p>
            <div className="flex gap-2">
              <button
                type="button"
                onClick={() => setStep('credentials')}
                className="rounded-lg border border-zinc-700 px-4 py-2 text-sm"
              >
                Back
              </button>
              <button
                type="submit"
                disabled={busy}
                className="rounded-lg bg-emerald-600 px-4 py-2 text-sm font-semibold hover:bg-emerald-500 disabled:opacity-50"
              >
                {busy ? 'Activating…' : 'Activate private dashboard'}
              </button>
            </div>
          </form>
        )}
      </div>
    </main>
  );
}
