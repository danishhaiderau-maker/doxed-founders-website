'use client';

import Link from 'next/link';
import { useState } from 'react';
import {
  confirmCryptoTopUp,
  createCryptoTopUpIntent,
  type CryptoTopUpIntent,
} from '@/lib/api';
import { formatUsd, RESTRICTED_CASH_THRESHOLD_USD, STARTING_CASH_USD } from '@dcf/utils';

interface ModalShellProps {
  open: boolean;
  onClose: () => void;
  children: React.ReactNode;
}

function ModalShell({ open, onClose, children }: ModalShellProps) {
  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <button
        type="button"
        aria-label="Close"
        className="absolute inset-0 bg-black/70"
        onClick={onClose}
      />
      <div className="relative max-h-[90vh] w-full max-w-md overflow-y-auto rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] p-6 shadow-xl">
        {children}
      </div>
    </div>
  );
}

interface RiskDisclaimerModalProps {
  open: boolean;
  ticker: string;
  onCancel: () => void;
  onAccept: () => void;
}

export function RiskDisclaimerModal({
  open,
  ticker,
  onCancel,
  onAccept,
}: RiskDisclaimerModalProps) {
  return (
    <ModalShell open={open} onClose={onCancel}>
      <div className="text-3xl">⚠️</div>
      <h2 className="mt-3 text-lg font-bold">Non-doxxed = high risk</h2>
      <p className="mt-3 text-sm leading-relaxed text-[var(--color-muted)]">
        <strong className="text-amber-300">{ticker}</strong> is not a verified, doxxed-founder
        project. Anonymous teams are often scams — if things go wrong, the first move is usually
        to run.
      </p>
      <p className="mt-3 text-sm leading-relaxed text-[var(--color-muted)]">
        Doxxed crypto exists to protect retail: buy doxxed founders with a solid track
        record. Only proceed if you accept the risk.
      </p>
      <div className="mt-6 flex gap-3">
        <button
          type="button"
          onClick={onCancel}
          className="flex-1 rounded-lg border border-[var(--color-border)] py-2.5 text-sm text-[var(--color-muted)] hover:text-white"
        >
          Cancel
        </button>
        <button
          type="button"
          onClick={onAccept}
          className="flex-1 rounded-lg bg-[var(--color-accent)] py-2.5 text-sm font-medium text-white hover:bg-[var(--color-accent-hover)]"
        >
          I accept the risk
        </button>
      </div>
    </ModalShell>
  );
}

interface BustPenaltyModalProps {
  open: boolean;
  resetFeeUsd: number;
  stripeEnabled: boolean;
  cryptoEnabled?: boolean;
  accessToken?: string;
  loading: boolean;
  onClose: () => void;
  onPayReset: () => void;
  onCryptoSuccess?: (message: string) => void;
}

export function BustPenaltyModal({
  open,
  resetFeeUsd,
  stripeEnabled,
  cryptoEnabled = false,
  accessToken,
  loading,
  onClose,
  onPayReset,
  onCryptoSuccess,
}: BustPenaltyModalProps) {
  const [mode, setMode] = useState<'choose' | 'crypto'>('choose');
  const [intent, setIntent] = useState<CryptoTopUpIntent | null>(null);
  const [txSignature, setTxSignature] = useState('');
  const [cryptoError, setCryptoError] = useState<string | null>(null);
  const [cryptoLoading, setCryptoLoading] = useState(false);

  async function startCryptoIntent() {
    if (!accessToken) {
      setCryptoError('Sign in and connect a Solana wallet in Account → Security first.');
      return;
    }
    setCryptoError(null);
    setCryptoLoading(true);
    try {
      const created = await createCryptoTopUpIntent(accessToken, 'USDC');
      setIntent(created);
      setMode('crypto');
    } catch (err) {
      setCryptoError(err instanceof Error ? err.message : 'Could not start on-chain payment');
    } finally {
      setCryptoLoading(false);
    }
  }

  async function submitCryptoConfirm() {
    if (!accessToken || !intent || !txSignature.trim()) return;
    setCryptoError(null);
    setCryptoLoading(true);
    try {
      const result = await confirmCryptoTopUp(intent.paymentId, txSignature.trim(), accessToken);
      onCryptoSuccess?.(result.message);
      onClose();
    } catch (err) {
      setCryptoError(err instanceof Error ? err.message : 'Payment verification failed');
    } finally {
      setCryptoLoading(false);
    }
  }

  const busy = loading || cryptoLoading;

  return (
    <ModalShell open={open} onClose={onClose}>
      <div className="text-3xl">💀</div>
      <h2 className="mt-3 text-lg font-bold">You went bust</h2>
      <p className="mt-3 text-sm leading-relaxed text-[var(--color-muted)]">
        Cash below {formatUsd(RESTRICTED_CASH_THRESHOLD_USD, 0)} (with or without open positions). Pay the
        restart penalty to restore {formatUsd(STARTING_CASH_USD, 0)} paper cash and keep trading.
      </p>
      <div className="mt-4 rounded-lg border border-red-500/30 bg-red-950/30 px-4 py-3 text-center">
        <p className="text-xs uppercase tracking-widest text-red-300">Restart penalty</p>
        <p className="mt-1 text-2xl font-bold text-white">{formatUsd(resetFeeUsd, 0)}</p>
        <p className="mt-1 text-xs text-[var(--color-muted)]">
          → fresh {formatUsd(STARTING_CASH_USD, 0)} paper cash
        </p>
      </div>

      {mode === 'choose' && (
        <>
          <p className="mt-3 text-xs text-[var(--color-muted)]">
            {cryptoEnabled
              ? 'Pay via USDC on Solana (to admin treasury) or card via Stripe.'
              : stripeEnabled
                ? 'Secure checkout powered by Stripe.'
                : 'Stripe not configured — dev mode simulates payment for now.'}
          </p>
          <div className="mt-6 flex flex-col gap-2">
            {cryptoEnabled && (
              <button
                type="button"
                onClick={startCryptoIntent}
                disabled={busy}
                className="w-full rounded-lg bg-violet-600 py-2.5 text-sm font-medium text-white hover:bg-violet-500 disabled:opacity-50"
              >
                {cryptoLoading ? 'Preparing…' : `Pay ${formatUsd(resetFeeUsd, 0)} USDC on Solana`}
              </button>
            )}
            <button
              type="button"
              onClick={onPayReset}
              disabled={busy}
              className="w-full rounded-lg bg-red-600 py-2.5 text-sm font-medium text-white hover:bg-red-500 disabled:opacity-50"
            >
              {loading
                ? 'Processing…'
                : stripeEnabled
                  ? `Pay ${formatUsd(resetFeeUsd, 0)} via Stripe`
                  : `Simulate ${formatUsd(resetFeeUsd, 0)} & restart`}
            </button>
            <button
              type="button"
              onClick={onClose}
              className="w-full rounded-lg border border-[var(--color-border)] py-2.5 text-sm text-[var(--color-muted)] hover:text-white"
            >
              Not now
            </button>
          </div>
          {cryptoError && <p className="mt-3 text-xs text-red-300">{cryptoError}</p>}
          {cryptoEnabled && !accessToken && (
            <p className="mt-3 text-xs text-amber-300">
              <Link href="/account?tab=security" className="underline">
                Sign in & connect Solana wallet
              </Link>{' '}
              for on-chain top-up.
            </p>
          )}
        </>
      )}

      {mode === 'crypto' && intent && (
        <>
          <div className="mt-4 space-y-2 rounded-lg border border-violet-500/30 bg-violet-950/20 p-3 text-xs text-violet-100">
            <p>
              <span className="text-violet-300">From:</span>{' '}
              <code className="break-all">{intent.payerAddress}</code>
            </p>
            <p>
              <span className="text-violet-300">To treasury:</span>{' '}
              <code className="break-all">{intent.treasuryAddress}</code>
            </p>
            <p>
              <span className="text-violet-300">Amount:</span> ${intent.amountUsd} USDC
            </p>
            <p>
              <span className="text-violet-300">Reference:</span> {intent.memo}
            </p>
            <p className="text-violet-200/80">{intent.instructions}</p>
          </div>
          <p className="mt-3 text-xs text-[var(--color-muted)]">
            We match your payment to your account via your linked wallet + unique reference{' '}
            <strong>{intent.reference}</strong>. Paste the transaction signature after sending.
          </p>
          <input
            type="text"
            value={txSignature}
            onChange={(e) => setTxSignature(e.target.value)}
            placeholder="Transaction signature"
            className="mt-3 w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-background)] px-3 py-2 text-sm"
          />
          {cryptoError && <p className="mt-2 text-xs text-red-300">{cryptoError}</p>}
          <div className="mt-4 flex gap-2">
            <button
              type="button"
              onClick={() => {
                setMode('choose');
                setIntent(null);
                setTxSignature('');
                setCryptoError(null);
              }}
              className="flex-1 rounded-lg border border-[var(--color-border)] py-2.5 text-sm text-[var(--color-muted)]"
            >
              Back
            </button>
            <button
              type="button"
              onClick={submitCryptoConfirm}
              disabled={busy || !txSignature.trim()}
              className="flex-1 rounded-lg bg-violet-600 py-2.5 text-sm font-medium text-white disabled:opacity-50"
            >
              {cryptoLoading ? 'Verifying…' : 'Confirm payment'}
            </button>
          </div>
        </>
      )}
    </ModalShell>
  );
}
