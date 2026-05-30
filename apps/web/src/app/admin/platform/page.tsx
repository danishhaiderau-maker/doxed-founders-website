'use client';

import Link from 'next/link';
import { useSession } from 'next-auth/react';
import { useRouter } from 'next/navigation';
import { FormEvent, useEffect, useState } from 'react';
import { formatUsd } from '@dcf/utils';
import { SiteNav } from '@/components/site-nav';
import {
  fetchPlatformEconomy,
  fetchTopUpPayments,
  TopUpPaymentRecord,
  updatePlatformTreasury,
} from '@/lib/api';

export default function AdminPlatformPage() {
  const { data: session, status } = useSession();
  const router = useRouter();
  const token = session?.accessToken;
  const isAdmin = session?.user?.role === 'ADMIN';

  const [solana, setSolana] = useState('');
  const [evm, setEvm] = useState('');
  const [payments, setPayments] = useState<TopUpPaymentRecord[]>([]);
  const [msg, setMsg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (status === 'loading') return;
    if (status === 'unauthenticated') {
      router.replace('/login?callbackUrl=/admin/platform');
      return;
    }
    if (!isAdmin) {
      router.replace('/');
    }
  }, [status, isAdmin, router]);

  useEffect(() => {
    if (!token || !isAdmin) return;
    fetchPlatformEconomy()
      .then((e) => {
        setSolana(e.treasury.solana ?? '');
        setEvm(e.treasury.evm ?? '');
      })
      .catch(() => {});
    fetchTopUpPayments(token)
      .then(setPayments)
      .catch(() => setPayments([]));
  }, [token, isAdmin]);

  async function handleSave(e: FormEvent) {
    e.preventDefault();
    if (!token) return;
    setSaving(true);
    setError(null);
    setMsg(null);
    try {
      await updatePlatformTreasury(
        {
          solanaTreasuryAddress: solana.trim() || undefined,
          evmTreasuryAddress: evm.trim() || undefined,
        },
        token,
      );
      setMsg('Treasury addresses saved.');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Save failed');
    } finally {
      setSaving(false);
    }
  }

  if (!isAdmin) return null;

  return (
    <div className="min-h-screen bg-[#050508] text-white">
      <header className="border-b border-zinc-800">
        <div className="mx-auto flex max-w-5xl items-center justify-between px-6 py-4">
          <div>
            <Link href="/admin/applications" className="text-xs text-zinc-500 hover:text-white">
              ← Admin
            </Link>
            <h1 className="text-xl font-bold">Platform treasury & top-ups</h1>
          </div>
          <SiteNav />
        </div>
      </header>

      <main className="mx-auto max-w-5xl space-y-8 px-6 py-8">
        {msg && <p className="rounded-lg border border-emerald-500/30 bg-emerald-950/20 px-4 py-3 text-sm text-emerald-200">{msg}</p>}
        {error && <p className="rounded-lg border border-red-500/30 bg-red-950/20 px-4 py-3 text-sm text-red-300">{error}</p>}

        <section className="rounded-xl border border-zinc-800 bg-zinc-950/60 p-6">
          <h2 className="font-semibold">On-chain treasury</h2>
          <p className="mt-1 text-sm text-zinc-500">
            USDC/SOL paper-trading top-ups land here. Users pay from their linked Solana wallet; we match by wallet + payment reference.
          </p>
          <form onSubmit={handleSave} className="mt-4 space-y-3">
            <label className="block text-sm">
              <span className="text-zinc-400">Solana treasury address</span>
              <input
                value={solana}
                onChange={(e) => setSolana(e.target.value)}
                className="mt-1 w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 font-mono text-sm"
                placeholder="Admin wallet for USDC top-ups"
              />
            </label>
            <label className="block text-sm">
              <span className="text-zinc-400">EVM treasury address (optional)</span>
              <input
                value={evm}
                onChange={(e) => setEvm(e.target.value)}
                className="mt-1 w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 font-mono text-sm"
              />
            </label>
            <button
              type="submit"
              disabled={saving}
              className="rounded-lg bg-violet-600 px-4 py-2 text-sm font-medium disabled:opacity-50"
            >
              {saving ? 'Saving…' : 'Save treasury'}
            </button>
          </form>
        </section>

        <section className="rounded-xl border border-zinc-800 bg-zinc-950/60 p-6">
          <h2 className="font-semibold">Recent top-up payments</h2>
          <p className="mt-1 text-sm text-zinc-500">Who paid $25 for paper cash — wallet, reference, and tx signature.</p>
          {payments.length === 0 ? (
            <p className="mt-4 text-sm text-zinc-600">No top-ups recorded yet.</p>
          ) : (
            <div className="mt-4 overflow-x-auto">
              <table className="w-full text-left text-sm">
                <thead className="text-xs uppercase text-zinc-500">
                  <tr>
                    <th className="pb-2 pr-4">When</th>
                    <th className="pb-2 pr-4">User</th>
                    <th className="pb-2 pr-4">Amount</th>
                    <th className="pb-2 pr-4">Status</th>
                    <th className="pb-2 pr-4">Payer wallet</th>
                    <th className="pb-2">Tx</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-zinc-800">
                  {payments.map((p) => (
                    <tr key={p.id}>
                      <td className="py-2 pr-4 text-zinc-400">{new Date(p.createdAt).toLocaleString()}</td>
                      <td className="py-2 pr-4">
                        <span className="font-medium">{p.userName ?? p.userEmail}</span>
                        <span className="block text-xs text-zinc-600">{p.reference}</span>
                      </td>
                      <td className="py-2 pr-4">{formatUsd(p.amountUsd)} {p.asset}</td>
                      <td className="py-2 pr-4">{p.status}</td>
                      <td className="max-w-[140px] truncate py-2 pr-4 font-mono text-xs">{p.payerAddress ?? '—'}</td>
                      <td className="max-w-[120px] truncate py-2 font-mono text-xs">{p.txSignature ?? '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      </main>
    </div>
  );
}
