'use client';

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
      <div className="relative max-w-md rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] p-6 shadow-xl">
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
        DoxedCryptoFounder exists to protect retail: buy doxxed founders with a solid track
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
  loading: boolean;
  onClose: () => void;
  onPayReset: () => void;
}

export function BustPenaltyModal({
  open,
  resetFeeUsd,
  stripeEnabled,
  loading,
  onClose,
  onPayReset,
}: BustPenaltyModalProps) {
  return (
    <ModalShell open={open} onClose={onClose}>
      <div className="text-3xl">💀</div>
      <h2 className="mt-3 text-lg font-bold">You went bust</h2>
      <p className="mt-3 text-sm leading-relaxed text-[var(--color-muted)]">
        Punishment for YOLO-ing risky, non-doxxed tokens until your balance hit zero. Learn the
        lesson — then pay the fine to get back in the game.
      </p>
      <div className="mt-4 rounded-lg border border-red-500/30 bg-red-950/30 px-4 py-3 text-center">
        <p className="text-xs uppercase tracking-widest text-red-300">Restart penalty</p>
        <p className="mt-1 text-2xl font-bold text-white">{formatUsd(resetFeeUsd)}</p>
        <p className="mt-1 text-xs text-[var(--color-muted)]">→ fresh $10,000 paper cash</p>
      </div>
      <p className="mt-3 text-xs text-[var(--color-muted)]">
        {stripeEnabled
          ? 'Secure checkout powered by Stripe. You will return here after payment.'
          : 'Stripe not configured — dev mode simulates payment for now.'}
      </p>
      <div className="mt-6 flex gap-3">
        <button
          type="button"
          onClick={onClose}
          className="flex-1 rounded-lg border border-[var(--color-border)] py-2.5 text-sm text-[var(--color-muted)] hover:text-white"
        >
          Not now
        </button>
        <button
          type="button"
          onClick={onPayReset}
          disabled={loading}
          className="flex-1 rounded-lg bg-red-600 py-2.5 text-sm font-medium text-white hover:bg-red-500 disabled:opacity-50"
        >
          {loading
            ? 'Processing…'
            : stripeEnabled
              ? `Pay ${formatUsd(resetFeeUsd)} via Stripe`
              : `Pay ${formatUsd(resetFeeUsd)} & restart`}
        </button>
      </div>
    </ModalShell>
  );
}

function formatUsd(value: number) {
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    maximumFractionDigits: 0,
  }).format(value);
}
