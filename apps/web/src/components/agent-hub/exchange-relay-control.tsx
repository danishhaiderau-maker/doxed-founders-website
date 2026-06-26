'use client';

import Link from 'next/link';
import { EXCHANGE_PROVIDER_LABELS, type ExchangeProvider } from '@dcf/utils';

type RelayState = 'idle' | 'active' | 'paused' | 'copy' | 'sim';

function relayLabel(state: RelayState, exchangeLabel: string) {
  switch (state) {
    case 'active':
      return `Copying showcase signals on ${exchangeLabel}`;
    case 'sim':
      return `Relay simulation active — paper book on ${exchangeLabel}; live orders blocked`;
    case 'paused':
      return `Relay stopped — ${exchangeLabel} protected`;
    case 'copy':
      return 'Legacy paper track — connect exchange for real copy';
    default:
      return 'Not connected';
  }
}

export function ExchangeRelayControl({
  slug,
  signedIn,
  exchange,
  exchangeLabel,
  exchangeConnected,
  relayState,
  busy,
  onStop,
  onStart,
  showSelector = true,
  hideConnect = false,
  rentalExpired,
  providers,
  onExchangeChange,
}: {
  slug: string;
  signedIn: boolean;
  exchange: string;
  exchangeLabel: string;
  exchangeConnected?: boolean;
  relayState: RelayState;
  busy?: boolean;
  onStop?: () => void;
  onStart?: () => void;
  showSelector?: boolean;
  hideConnect?: boolean;
  rentalExpired?: boolean;
  providers?: { id: string; label: string; available: boolean }[];
  onExchangeChange?: (id: string) => void;
}) {
  const isLiveRelay = relayState === 'active' || relayState === 'paused';
  const canToggle =
    signedIn && isLiveRelay && (onStop || onStart) && !rentalExpired;
  const hireHref = signedIn
    ? `/agent-hub/${slug}/hire?exchange=${exchange}`
    : `/login?callbackUrl=${encodeURIComponent(`/agent-hub/${slug}/hire?exchange=${exchange}`)}`;

  return (
    <div className="space-y-3">
      {exchangeConnected && (
        <div className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-emerald-500/35 bg-emerald-950/25 px-3 py-2.5">
          <div className="flex items-center gap-2 text-sm">
            <span className="relative flex h-2.5 w-2.5">
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-40" />
              <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-emerald-400" />
            </span>
            <span className="font-semibold text-emerald-100">{exchangeLabel} connected</span>
          </div>
          <span
            className={`rounded-full px-2.5 py-0.5 text-[10px] font-bold uppercase tracking-wide ${
              relayState === 'active'
                ? 'bg-emerald-500/20 text-emerald-200'
                : relayState === 'sim'
                  ? 'bg-sky-500/20 text-sky-200'
                : relayState === 'paused'
                  ? 'bg-red-500/20 text-red-200'
                  : 'bg-zinc-800 text-zinc-400'
            }`}
          >
            {relayState === 'active'
              ? 'Relay on'
              : relayState === 'sim'
                ? 'Sim on'
              : relayState === 'paused'
                ? 'Relay off'
                : relayState}
          </span>
        </div>
      )}

      <p className="text-xs text-zinc-500">{relayLabel(relayState, exchangeLabel)}</p>

      <div className="flex flex-wrap items-end gap-2">
        {showSelector && providers && providers.length > 0 ? (
          <label className="min-w-[160px] flex-1 text-xs text-zinc-400">
            Exchange
            <select
              value={exchange}
              onChange={(e) => onExchangeChange?.(e.target.value)}
              disabled={isLiveRelay}
              className="mt-1 w-full rounded-lg border border-zinc-700 bg-zinc-900 px-3 py-2 text-sm text-zinc-200 disabled:opacity-60"
              aria-label="Exchange"
            >
              {providers.map((p) => (
                <option key={p.id} value={p.id} disabled={!p.available}>
                  {p.label}
                  {p.id === 'bitfinex' ? ' — Recommended' : ''}
                  {!p.available ? ' (Soon)' : ''}
                </option>
              ))}
            </select>
          </label>
        ) : (
          <div className="min-w-[160px] flex-1 rounded-lg border border-zinc-700 bg-zinc-900/80 px-3 py-2 text-sm text-zinc-200">
            {exchangeLabel}
            {exchange === 'bitfinex' && (
              <span className="ml-2 text-[10px] font-bold uppercase text-emerald-400">Recommended</span>
            )}
          </div>
        )}

        {canToggle ? (
          relayState === 'paused' ? (
            <button
              type="button"
              disabled={busy}
              onClick={onStart}
              className="shrink-0 rounded-lg border border-emerald-500/50 bg-emerald-600 px-4 py-2 text-sm font-semibold text-white hover:bg-emerald-500 disabled:opacity-50"
            >
              {busy ? '…' : 'Start real trading'}
            </button>
          ) : (
            <button
              type="button"
              disabled={busy}
              onClick={onStop}
              className="shrink-0 rounded-lg border border-red-500/50 bg-red-600/90 px-4 py-2 text-sm font-semibold text-white hover:bg-red-500 disabled:opacity-50"
            >
              {busy ? '…' : 'Stop'}
            </button>
          )
        ) : rentalExpired && isLiveRelay ? (
          <span className="shrink-0 rounded-lg border border-red-500/50 bg-red-950/40 px-4 py-2 text-sm font-semibold text-red-200">
            Renew rental to start
          </span>
        ) : relayState === 'idle' && !hideConnect ? (
          <Link
            href={hireHref}
            className="shrink-0 rounded-lg bg-violet-600 px-4 py-2 text-sm font-semibold text-white hover:bg-violet-500"
          >
            Connect
          </Link>
        ) : relayState === 'copy' && canToggle ? null : null}
      </div>

      {isLiveRelay && (
        <p className="text-[11px] leading-relaxed text-zinc-500">
          <strong className="text-zinc-400">Stop</strong> severs the showcase relay — no new trades from the admin bot
          will hit your {exchangeLabel} account. Open positions stay on the exchange.{' '}
          <strong className="text-zinc-400">Start real trading</strong> resumes copy from where you left off
          {rentalExpired ? ' after you renew your rental.' : '.'}
        </p>
      )}
    </div>
  );
}

export function exchangeLabelFor(id: string) {
  return EXCHANGE_PROVIDER_LABELS[id as ExchangeProvider] ?? id;
}
