'use client';

import Link from 'next/link';
import { useState } from 'react';
import { formatDdollarCompact } from '@dcf/utils';
import { apiUrl } from '@/lib/api-base';

export type ClaimableEpoch = {
  epochId: string;
  epochNumber: number;
  amount: number;
  walletAddress: string;
  merkleRoot: string;
  merkleProof: string[];
  claimWindowOpen: boolean;
};

export function ClaimTokens({
  claimable,
  signedIn,
  loading,
}: {
  claimable: ClaimableEpoch[];
  signedIn: boolean;
  loading: boolean;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [proof, setProof] = useState<Record<string, unknown> | null>(null);
  const [error, setError] = useState<string | null>(null);

  if (!signedIn) {
    return (
      <section className="rounded-xl border border-zinc-800 bg-zinc-900/40 p-5">
        <h3 className="text-sm font-semibold uppercase tracking-widest text-zinc-400">Claim epoch tokens</h3>
        <p className="mt-3 text-sm text-zinc-400">
          <Link href="/login?callbackUrl=/founder-economics" className="text-emerald-400 hover:underline">
            Sign in
          </Link>{' '}
          to claim your epoch token allocation.
        </p>
      </section>
    );
  }

  const total = claimable.reduce((sum, c) => sum + c.amount, 0);

  async function claim(epoch: ClaimableEpoch) {
    setBusy(epoch.epochId);
    setError(null);
    setProof(null);
    try {
      const token = sessionStorage.getItem('dcf-nextauth-access-token') ?? '';
      const res = await fetch(apiUrl('/founder-economics/claim'), {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify({ epochId: epoch.epochId, walletAddress: epoch.walletAddress }),
      });
      if (!res.ok) throw new Error(`${res.status}`);
      const data = (await res.json()) as Record<string, unknown>;
      setProof(data);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(null);
    }
  }

  return (
    <section className="rounded-xl border border-zinc-800 bg-zinc-900/40 p-5">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold uppercase tracking-widest text-zinc-400">Claim epoch tokens</h3>
        <span className="text-xs text-zinc-500">{formatDdollarCompact(total)} claimable</span>
      </div>

      {loading && claimable.length === 0 ? (
        <p className="mt-3 text-sm text-zinc-500">Loading…</p>
      ) : claimable.length === 0 ? (
        <p className="mt-3 text-sm text-zinc-500">
          No claimable allocations yet. Epochs settle on a schedule; your share
          will appear here once a Merkle root is published.
        </p>
      ) : (
        <ul className="mt-3 space-y-2">
          {claimable.map((c) => (
            <li
              key={c.epochId}
              className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-zinc-800 bg-zinc-950/40 px-3 py-2"
            >
              <div>
                <p className="text-sm text-white">Epoch #{c.epochNumber}</p>
                <p className="text-[11px] text-zinc-500">
                  {formatDdollarCompact(c.amount)} · window {c.claimWindowOpen ? 'open' : 'closed'}
                </p>
              </div>
              <button
                disabled={busy === c.epochId || !c.claimWindowOpen}
                onClick={() => void claim(c)}
                className="rounded-md border border-emerald-500/40 bg-emerald-950/30 px-3 py-1 text-xs text-emerald-200 hover:border-emerald-400 disabled:opacity-50"
              >
                {busy === c.epochId ? 'Preparing…' : 'Claim'}
              </button>
            </li>
          ))}
        </ul>
      )}

      {error && <p className="mt-3 text-xs text-amber-300">Claim failed: {error}</p>}

      {proof && (
        <pre className="mt-3 max-h-48 overflow-auto rounded-lg border border-zinc-800 bg-black/40 p-3 text-[10px] text-emerald-200">
          {JSON.stringify(proof, null, 2)}
        </pre>
      )}

      <p className="mt-3 text-[11px] text-zinc-600">
        Your wallet calls <code className="text-zinc-400">EpochDistributor.claim(epoch, account, amount, proof)</code>{' '}
        on-chain with the proof returned here.
      </p>
    </section>
  );
}
