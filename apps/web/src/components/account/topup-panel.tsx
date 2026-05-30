'use client';

import { useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import {
  confirmCryptoTopUp,
  createCryptoTopUpIntent,
  fetchResetInfo,
  type CryptoTopUpIntent,
} from '@/lib/api';
import { formatUsd, STARTING_CASH_USD, TOP_UP_FEE_USD } from '@dcf/utils';

type TopUpPanelProps = {
  accessToken: string;
};

export function TopUpPanel({ accessToken }: TopUpPanelProps) {
  const [resetFeeUsd, setResetFeeUsd] = useState(TOP_UP_FEE_USD);
  const [cryptoEnabled, setCryptoEnabled] = useState(false);
  const [intent, setIntent] = useState<CryptoTopUpIntent | null>(null);
  const [txSignature, setTxSignature] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const info = await fetchResetInfo();
      setResetFeeUsd(info.resetFeeUsd);
      setCryptoEnabled(Boolean(info.cryptoEnabled));
    } catch {
      setCryptoEnabled(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  async function startCrypto() {
    setError(null);
    setSuccess(null);
    setBusy(true);
    try {
      const created = await createCryptoTopUpIntent(accessToken, 'USDC');
      setIntent(created);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not start crypto top-up');
    } finally {
      setBusy(false);
    }
  }

  async function confirmCrypto() {
    if (!intent || !txSignature.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const result = await confirmCryptoTopUp(intent.paymentId, txSignature.trim(), accessToken);
      setSuccess(result.message);
      setIntent(null);
      setTxSignature('');
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Confirmation failed');
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-6">
      <div className="rounded-2xl border border-amber-500/30 bg-amber-950/15 p-6">
        <h2 className="text-lg font-semibold text-amber-200">Top up paper cash</h2>
        <p className="mt-2 text-sm text-zinc-400">
          Add {formatUsd(STARTING_CASH_USD, 0)} Ddollar virtual cash for{' '}
          <strong className="text-white">{formatUsd(resetFeeUsd)} USDC</strong> — stake predictions,
          paper trade, and join Raise Room allocations.
        </p>
      </div>

      {cryptoEnabled ? (
        <div className="rounded-2xl border border-zinc-800 bg-zinc-900/40 p-6">
          <h3 className="font-semibold text-white">Pay with USDC (Solana)</h3>
          <p className="mt-1 text-sm text-zinc-500">
            Connect a Solana wallet in{' '}
            <Link href="/account?tab=security" className="text-violet-400 hover:underline">
              Security
            </Link>{' '}
            first, then send USDC to the platform treasury.
          </p>
          {!intent ? (
            <button
              type="button"
              disabled={busy}
              onClick={startCrypto}
              className="mt-4 rounded-lg bg-amber-600 px-5 py-2.5 text-sm font-semibold text-white disabled:opacity-50"
            >
              {busy ? 'Starting…' : `Get payment address (${formatUsd(resetFeeUsd)} USDC)`}
            </button>
          ) : (
            <div className="mt-4 space-y-3 text-sm">
              <p className="text-zinc-300">
                Send <strong>{formatUsd(intent.amountUsd)} USDC</strong> to:
              </p>
              <code className="block break-all rounded-lg bg-black/40 p-3 text-xs text-emerald-300">
                {intent.treasuryAddress}
              </code>
              <input
                value={txSignature}
                onChange={(e) => setTxSignature(e.target.value)}
                placeholder="Paste Solana transaction signature"
                className="w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm"
              />
              <button
                type="button"
                disabled={busy || !txSignature.trim()}
                onClick={confirmCrypto}
                className="rounded-lg bg-emerald-600 px-5 py-2 text-sm font-semibold text-white disabled:opacity-50"
              >
                {busy ? 'Confirming…' : 'Confirm payment'}
              </button>
            </div>
          )}
        </div>
      ) : (
        <p className="rounded-xl border border-zinc-800 bg-zinc-900/40 p-4 text-sm text-zinc-500">
          Crypto top-up is not enabled on this deployment yet. An admin can configure the Solana
          treasury at <Link href="/admin/platform" className="text-violet-400 hover:underline">Treasury & top-ups</Link>.
        </p>
      )}

      {error && (
        <p className="rounded-lg border border-red-500/30 bg-red-950/20 px-4 py-3 text-sm text-red-300">
          {error}
        </p>
      )}
      {success && (
        <p className="rounded-lg border border-emerald-500/30 bg-emerald-950/20 px-4 py-3 text-sm text-emerald-200">
          {success}
        </p>
      )}
    </div>
  );
}
