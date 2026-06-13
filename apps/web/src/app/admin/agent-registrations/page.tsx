'use client';

import Link from 'next/link';
import { useSession } from 'next-auth/react';
import { useRouter } from 'next/navigation';
import { FormEvent, useCallback, useEffect, useState } from 'react';
import { SiteNav } from '@/components/site-nav';
import {
  AgentRegistryOverview,
  fetchAgentRegistryOverview,
  fetchPlatformEconomy,
  recordAgentRegistry,
  updatePlatformTreasury,
} from '@/lib/api';

const SLUG = 'conservative-btc';

export default function AdminAgentRegistrationsPage() {
  const { data: session, status } = useSession();
  const router = useRouter();
  const token = session?.accessToken;
  const isAdmin = session?.user?.role === 'ADMIN';

  const [overview, setOverview] = useState<AgentRegistryOverview | null>(null);
  const [solanaTreasury, setSolanaTreasury] = useState('');
  const [evmTreasury, setEvmTreasury] = useState('');
  const [msg, setMsg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    if (!token) return;
    const [reg, economy] = await Promise.all([
      fetchAgentRegistryOverview(SLUG, token),
      fetchPlatformEconomy().catch(() => null),
    ]);
    setOverview(reg);
    setSolanaTreasury(reg.feeCollection.solanaTreasury ?? economy?.treasury.solana ?? '');
    setEvmTreasury(reg.feeCollection.evmTreasury ?? economy?.treasury.evm ?? '');
  }, [token]);

  useEffect(() => {
    if (status === 'loading') return;
    if (status === 'unauthenticated') {
      router.replace('/login?callbackUrl=/admin/agent-registrations');
      return;
    }
    if (!isAdmin) router.replace('/');
  }, [status, isAdmin, router]);

  useEffect(() => {
    if (!token || !isAdmin) return;
    load().catch((e) => setError(e instanceof Error ? e.message : 'Load failed'));
  }, [token, isAdmin, load]);

  async function saveTreasury(e: FormEvent) {
    e.preventDefault();
    if (!token) return;
    setBusy(true);
    setError(null);
    setMsg(null);
    try {
      await updatePlatformTreasury(
        {
          solanaTreasuryAddress: solanaTreasury.trim() || undefined,
          evmTreasuryAddress: evmTreasury.trim() || undefined,
        },
        token,
      );
      setMsg('Fee treasury saved — signal success fees will route to your Solana wallet.');
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Save failed');
    } finally {
      setBusy(false);
    }
  }

  async function markRegistered(registry: string, txSignature?: string) {
    if (!token) return;
    setBusy(true);
    setError(null);
    try {
      await recordAgentRegistry(
        SLUG,
        {
          registry,
          status: 'REGISTERED',
          txSignature: txSignature?.trim() || undefined,
        },
        token,
      );
      setMsg(`${registry} marked registered.`);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Update failed');
    } finally {
      setBusy(false);
    }
  }

  if (!isAdmin) return null;

  return (
    <div className="min-h-screen bg-[#050508] text-white">
      <header className="border-b border-zinc-800">
        <div className="mx-auto flex max-w-5xl items-center justify-between px-6 py-4">
          <div>
            <Link href="/admin/platform" className="text-xs text-zinc-500 hover:text-white">
              ← Platform treasury
            </Link>
            <h1 className="text-xl font-bold">Agent registrations & fee wallet</h1>
            <p className="text-sm text-zinc-500">Conservative BTC Agent — SAID, The Spawn, ERC-8004</p>
          </div>
          <SiteNav />
        </div>
      </header>

      <main className="mx-auto max-w-5xl space-y-8 px-6 py-8">
        {msg && (
          <p className="rounded-lg border border-emerald-500/30 bg-emerald-950/20 px-4 py-3 text-sm text-emerald-200">
            {msg}
          </p>
        )}
        {error && (
          <p className="rounded-lg border border-red-500/30 bg-red-950/20 px-4 py-3 text-sm text-red-300">{error}</p>
        )}

        <section className="rounded-xl border border-amber-500/30 bg-amber-950/15 p-6">
          <h2 className="font-semibold text-amber-100">Admin-owned showcase agent</h2>
          <p className="mt-2 text-sm text-zinc-400">
            Only your admin account controls this bot, fee wallets, and directory registration. Users may
            observe or hire a private instance (2,000 DDollar / 7 days) — they never run your showcase.
          </p>
          <Link href="/admin/control" className="mt-3 inline-block text-sm text-violet-400 hover:underline">
            Admin Control (bot keys, pause/start) →
          </Link>
        </section>

        <section className="rounded-xl border border-zinc-800 bg-zinc-950/60 p-6">
          <h2 className="font-semibold">1. Connect Phantom (admin fee wallet)</h2>
          <p className="mt-2 text-sm text-zinc-400">
            Link your Phantom wallet on{' '}
            <Link href="/account?tab=security" className="text-violet-300 underline">
              Account → Security
            </Link>
            {' '}(profile menu → Security, not Admin Control).
            , then paste the same address below as the Solana treasury. All signal success fees (USDC) land here.
          </p>
          {overview && (
            <div className="mt-4 rounded-lg border border-zinc-800 bg-zinc-900/50 p-4 text-sm">
              <p className={overview.feeCollection.ready ? 'text-emerald-300' : 'text-amber-300'}>
                {overview.feeCollection.message}
              </p>
              {overview.feeCollection.adminLinkedSolana && (
                <p className="mt-2 font-mono text-xs text-zinc-400">
                  Linked admin wallet: {overview.feeCollection.adminLinkedSolana}
                </p>
              )}
            </div>
          )}
          <form onSubmit={saveTreasury} className="mt-4 space-y-3">
            <label className="block text-sm">
              <span className="text-zinc-400">Solana treasury (Phantom pubkey)</span>
              <input
                value={solanaTreasury}
                onChange={(e) => setSolanaTreasury(e.target.value)}
                className="mt-1 w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 font-mono text-sm"
                placeholder="Your Phantom address for USDC fees"
              />
            </label>
            <label className="block text-sm">
              <span className="text-zinc-400">EVM treasury (Base / Spawn owner)</span>
              <input
                value={evmTreasury}
                onChange={(e) => setEvmTreasury(e.target.value)}
                className="mt-1 w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 font-mono text-sm"
                placeholder="MetaMask address for ERC-8004 mint"
              />
            </label>
            <button
              type="submit"
              disabled={busy}
              className="rounded-lg bg-violet-600 px-4 py-2 text-sm font-medium disabled:opacity-50"
            >
              Save fee wallets
            </button>
          </form>
        </section>

        {overview && (
          <>
            <section className="rounded-xl border border-zinc-800 bg-zinc-950/60 p-6">
              <h2 className="font-semibold">2. Public metadata (already hosted)</h2>
              <ul className="mt-3 space-y-2 text-sm text-zinc-300">
                <li>
                  AgentCard (SAID):{' '}
                  <a href={overview.metadata.urls.agentCard} className="text-violet-300 underline" target="_blank" rel="noreferrer">
                    {overview.metadata.urls.agentCard}
                  </a>
                </li>
                <li>
                  ERC-8004 JSON (Spawn):{' '}
                  <a href={overview.metadata.urls.agentJson} className="text-violet-300 underline" target="_blank" rel="noreferrer">
                    {overview.metadata.urls.agentJson}
                  </a>
                </li>
              </ul>
            </section>

            <section className="rounded-xl border border-zinc-800 bg-zinc-950/60 p-6">
              <h2 className="font-semibold">3. Register on each directory</h2>
              <p className="mt-1 text-sm text-zinc-500">
                Sign transactions from Phantom (SAID) or MetaMask on Base (Spawn). After each mint, click Mark registered.
              </p>
              <div className="mt-6 space-y-6">
                {overview.checklist.map((item) => (
                  <div key={item.registry} className="rounded-lg border border-zinc-800 p-4">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <h3 className="font-medium">{item.label}</h3>
                      <span
                        className={`rounded-full px-2 py-0.5 text-xs ${
                          item.status === 'REGISTERED' || item.status === 'VERIFIED'
                            ? 'bg-emerald-950 text-emerald-300'
                            : 'bg-zinc-800 text-zinc-400'
                        }`}
                      >
                        {item.status}
                      </span>
                    </div>
                    <ol className="mt-3 list-decimal space-y-1 pl-5 text-sm text-zinc-400">
                      {item.instructions.map((line) => (
                        <li key={line}>{line}</li>
                      ))}
                    </ol>
                    {item.registry === 'SAID' && (
                      <pre className="mt-3 overflow-x-auto rounded bg-zinc-900 p-3 text-xs text-zinc-300">
                        {(overview.said as { registerCommand?: string }).registerCommand}
                      </pre>
                    )}
                    {item.registry === 'SPAWN' && (
                      <pre className="mt-3 overflow-x-auto rounded bg-zinc-900 p-3 text-xs text-zinc-300">
                        {JSON.stringify((overview.spawn as { samplePayload?: object }).samplePayload, null, 2)}
                      </pre>
                    )}
                    {item.registry !== 'AGENT_CARD' && item.registry !== 'ERC8004_SCAN' && (
                      <button
                        type="button"
                        disabled={busy}
                        onClick={() => markRegistered(item.registry)}
                        className="mt-3 rounded-lg border border-zinc-600 px-3 py-1.5 text-xs hover:bg-zinc-800 disabled:opacity-50"
                      >
                        Mark registered (after you signed)
                      </button>
                    )}
                  </div>
                ))}
              </div>
            </section>
          </>
        )}
      </main>
    </div>
  );
}
