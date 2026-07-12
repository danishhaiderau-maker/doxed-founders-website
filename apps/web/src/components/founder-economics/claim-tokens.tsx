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

/**
 * Claim UI for Founder Economics MVP.
 *
 * Production path (counsel-gated): wallet signs EpochDistributor.claim on-chain.
 * MVP path: API returns a Merkle proof stub; Connect wallet CTA is always visible
 * so founders know the real claim surface even when no epochs are ready.
 */
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
  const [walletHint, setWalletHint] = useState<string | null>(null);

  function onConnectWallet() {
    setWalletHint(
      'Wallet connect ships with the production EpochDistributor deploy (counsel-gated). ' +
        'Until then, sign in below to fetch off-chain Merkle proofs for test epochs.',
    );
  }

  if (!signedIn) {
    return (
      <section className="rounded-xl border border-zinc-800 bg-zinc-900/40 p-5">
        <h3 className="text-sm font-semibold uppercase tracking-widest text-zinc-400">Claim epoch tokens</h3>
        <p className="mt-3 text-sm text-zinc-400">
          <Link href="/login?callbackUrl=/founder-economics" className="text-emerald-400 hover:underline">
            Sign in
          </Link>{' '}
          to see claimable epochs, then connect a wallet for on-chain claims when production contracts go live.
        </p>
        <button
          type="button"
          onClick={onConnectWallet}
          className="mt-4 rounded-md border border-violet-500/40 bg-violet-950/30 px-3 py-1.5 text-xs font-medium text-violet-100 hover:border-violet-400"
        >
          Connect wallet
        </button>
        {walletHint && <p className="mt-2 text-[11px] text-zinc-500">{walletHint}</p>}
        <p className="mt-3 text-[11px] text-zinc-600">
          MVP = off-chain DDollar + Merkle proofs. Production = audited Solidity + live claim window.
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
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-semibold uppercase tracking-widest text-zinc-400">Claim epoch tokens</h3>
        <div className="flex items-center gap-2">
          <span className="text-xs text-zinc-500">{formatDdollarCompact(total)} claimable</span>
          <button
            type="button"
            onClick={onConnectWallet}
            className="rounded-md border border-violet-500/40 bg-violet-950/30 px-3 py-1 text-xs font-medium text-violet-100 hover:border-violet-400"
          >
            Connect wallet
          </button>
        </div>
      </div>

      {walletHint && (
        <p className="mt-2 rounded-md border border-zinc-800 bg-black/30 px-3 py-2 text-[11px] text-zinc-400">
          {walletHint}
        </p>
      )}

      {loading && claimable.length === 0 ? (
        <p className="mt-3 text-sm text-zinc-500">Loading…</p>
      ) : claimable.length === 0 ? (
        <p className="mt-3 text-sm text-zinc-500">
          No claimable allocations yet. Epochs settle on a schedule; your share will appear here once a Merkle
          root is published. Connect wallet stays available for the production claim path.
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
                {busy === c.epochId ? 'Preparing…' : 'Get Merkle proof (MVP)'}
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
        Production: your wallet calls{' '}
        <code className="text-zinc-400">EpochDistributor.claim(epoch, account, amount, proof)</code> on-chain.
        MVP returns the proof only — no fake deploy.
      </p>
    </section>
  );
}
