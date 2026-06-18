'use client';

import Link from 'next/link';
import { useEffect, useMemo, useState } from 'react';
import {
  BITFINEX_RECOMMEND_BANNER,
  DEFAULT_SUBSCRIBER_MAX_MARGIN_USD,
  EXCHANGE_API_GUIDES,
  EXCHANGE_PROVIDER_LABELS,
  EXCHANGE_PROVIDERS,
  type ExchangeProvider,
} from '@dcf/utils';
import { ExchangeApiGuideDrawer } from '@/components/agent-hub/exchange-api-guide-drawer';
import { ExchangeRelayControl } from '@/components/agent-hub/exchange-relay-control';
import { AgentRentalCountdown } from '@/components/agent-hub/agent-rental-countdown';
import { ExchangeProviderOption, fetchExchangeProviders } from '@/lib/api';
import { formatUsd } from '@dcf/utils';

function sortProviders(list: ExchangeProviderOption[]): ExchangeProviderOption[] {
  return [...list].sort((a, b) => {
    if (a.id === 'bitfinex') return -1;
    if (b.id === 'bitfinex') return 1;
    if (a.available !== b.available) return a.available ? -1 : 1;
    return a.label.localeCompare(b.label);
  });
}

const FALLBACK_PROVIDERS: ExchangeProviderOption[] = EXCHANGE_PROVIDERS.map((id) => ({
  id,
  label: EXCHANGE_PROVIDER_LABELS[id],
  available: true,
}));

export function ExchangeHirePanel({
  slug,
  signedIn,
  costWeek,
  hired,
  instanceMode,
  instanceStatus,
  exchangeProvider,
  exchangeLabel,
  exchangeConnected,
  exchangeBalanceUsd,
  rentalExpiresAt,
  onStopRelay,
  onStartRelay,
  relayBusy,
}: {
  slug: string;
  signedIn: boolean;
  costWeek: number;
  hired?: boolean;
  instanceMode?: 'copy' | 'live' | null;
  instanceStatus?: string | null;
  exchangeProvider?: string | null;
  exchangeLabel?: string | null;
  exchangeConnected?: boolean;
  exchangeBalanceUsd?: number | null;
  rentalExpiresAt?: string | null;
  onStopRelay?: () => void;
  onStartRelay?: () => void;
  relayBusy?: boolean;
}) {
  const [exchange, setExchange] = useState<ExchangeProvider>(
    (exchangeProvider as ExchangeProvider) || 'bitfinex',
  );
  const [providers, setProviders] = useState<ExchangeProviderOption[]>(FALLBACK_PROVIDERS);
  const [guideOpen, setGuideOpen] = useState(false);

  useEffect(() => {
    if (exchangeProvider) setExchange(exchangeProvider as ExchangeProvider);
  }, [exchangeProvider]);

  useEffect(() => {
    fetchExchangeProviders()
      .then((rows) => {
        if (rows.length) setProviders(sortProviders(rows));
      })
      .catch(() => {
        /* keep static fallback */
      });
  }, []);

  const sorted = useMemo(() => sortProviders(providers), [providers]);
  const guide = EXCHANGE_API_GUIDES[exchange];
  const selectedLabel =
    exchangeLabel ??
    sorted.find((p) => p.id === exchange)?.label ??
    EXCHANGE_PROVIDER_LABELS[exchange];
  const hireHref = signedIn
    ? `/agent-hub/${slug}/hire?exchange=${exchange}`
    : `/login?callbackUrl=${encodeURIComponent(`/agent-hub/${slug}/hire?exchange=${exchange}`)}`;

  const relayState =
    hired && instanceMode === 'live'
      ? instanceStatus === 'PAUSED'
        ? ('paused' as const)
        : ('active' as const)
      : hired && instanceMode === 'copy'
        ? ('copy' as const)
        : ('idle' as const);

  const steps = [
    { n: 1, label: 'Choose exchange', detail: `${selectedLabel}${exchange === 'bitfinex' ? ' (Recommended)' : ''}` },
    { n: 2, label: 'Connect exchange API', detail: 'Read + trade only — no withdraw' },
    { n: 3, label: 'Admin DeepSeek copy', detail: 'No AI key needed' },
    { n: 4, label: 'Risk acknowledgement', detail: `Max $${DEFAULT_SUBSCRIBER_MAX_MARGIN_USD} margin per trade (platform-enforced)` },
    { n: 5, label: 'Activate agent', detail: `${costWeek.toLocaleString()} DDollar / week · Bitfinex auto-copy` },
  ];

  return (
    <>
      <div className="rounded-2xl border border-emerald-500/30 bg-emerald-950/15 p-5">
        <p className="text-xs font-bold uppercase text-emerald-400">Showcase tested on Bitfinex</p>
        <p className="mt-2 text-xs text-emerald-100/75">{BITFINEX_RECOMMEND_BANNER}</p>
        <p className="mt-2 text-[11px] text-zinc-400">
          Live hire uses Bitfinex (zero fees). Platform enforces ${DEFAULT_SUBSCRIBER_MAX_MARGIN_USD} max margin per
          trade — exchange balance cannot override this cap.
        </p>
      </div>

      <div className="rounded-2xl border border-zinc-800 bg-zinc-950/60 p-5">
        {relayState === 'active' || relayState === 'paused' ? (
          <>
            <p className="font-semibold text-white">Live copy on {selectedLabel}</p>
            {rentalExpiresAt && (
              <div className="mt-3">
                <AgentRentalCountdown expiresAt={rentalExpiresAt} compact />
              </div>
            )}
            <p className="mt-3 rounded-lg border border-emerald-500/30 bg-emerald-950/20 px-3 py-2 text-xs text-emerald-100">
              Your {selectedLabel} balance:{' '}
              <strong className="text-white">{formatUsd(exchangeBalanceUsd ?? 0, 2)}</strong>
              {' '}available · platform caps each trade at ${DEFAULT_SUBSCRIBER_MAX_MARGIN_USD} margin.
            </p>
          </>
        ) : (
          <>
            <p className="font-semibold text-white">Hire this agent</p>
            <p className="mt-2 rounded-lg border border-violet-500/30 bg-violet-950/20 px-3 py-2 text-xs text-violet-100">
              Hiring fee:{' '}
              <strong className="text-white">{costWeek.toLocaleString()} DDollar</strong> for 1 week of live copy
              trading
            </p>
          </>
        )}

        <div className="mt-4 rounded-xl border border-zinc-800 bg-black/25 p-4">
          <p className="text-[10px] font-bold uppercase tracking-widest text-zinc-500">Exchange & relay control</p>
          <div className="mt-3">
            <ExchangeRelayControl
              slug={slug}
              signedIn={signedIn}
              exchange={exchange}
              exchangeLabel={selectedLabel}
              exchangeConnected={exchangeConnected ?? (hired && instanceMode === 'live')}
              relayState={relayState}
              busy={relayBusy}
              onStop={onStopRelay}
              onStart={onStartRelay}
              showSelector={!hired || instanceMode !== 'live'}
              providers={sorted}
              onExchangeChange={(id) => setExchange(id as ExchangeProvider)}
            />
          </div>
        </div>

        {relayState === 'idle' && (
          <>
            <ol className="mt-4 space-y-3">
              {steps.map((s) => (
                <li key={s.n} className="flex gap-3 text-xs">
                  <span
                    className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-[10px] font-bold ${
                      s.n === 1 ? 'bg-violet-600 text-white' : 'bg-zinc-800 text-zinc-500'
                    }`}
                  >
                    {s.n}
                  </span>
                  <div>
                    <p className={s.n === 1 ? 'font-semibold text-zinc-200' : 'text-zinc-500'}>{s.label}</p>
                    {s.detail && <p className="text-zinc-500">{s.detail}</p>}
                  </div>
                </li>
              ))}
            </ol>

            <Link
              href={hireHref}
              className="mt-4 block rounded-lg bg-violet-600 py-2.5 text-center text-sm font-semibold hover:bg-violet-500"
            >
              Start setup on {selectedLabel}
            </Link>
          </>
        )}

        {relayState === 'copy' && signedIn && (onStopRelay || onStartRelay) && (
          <div className="mt-4 flex gap-2">
            {instanceStatus === 'PAUSED' ? (
              <button
                type="button"
                disabled={relayBusy}
                onClick={onStartRelay}
                className="flex-1 rounded-lg border border-emerald-500/50 bg-emerald-600 py-2 text-sm font-semibold text-white hover:bg-emerald-500 disabled:opacity-50"
              >
                {relayBusy ? '…' : 'Resume paper copy'}
              </button>
            ) : (
              <button
                type="button"
                disabled={relayBusy}
                onClick={onStopRelay}
                className="flex-1 rounded-lg border border-amber-500/50 bg-amber-900/40 py-2 text-sm font-semibold text-amber-100 hover:bg-amber-900/60 disabled:opacity-50"
              >
                {relayBusy ? '…' : 'Pause paper copy'}
              </button>
            )}
          </div>
        )}
      </div>

      <div className="rounded-2xl border border-zinc-800 bg-zinc-950/60 p-5">
        <p className="text-sm font-semibold text-white">
          How to connect {selectedLabel} API
        </p>
        {guide.credentialHint && (
          <p className="mt-2 rounded-lg border border-zinc-800 bg-black/30 px-3 py-2 text-[11px] text-zinc-400">
            {guide.credentialHint}
          </p>
        )}
        <ol className="mt-3 list-decimal space-y-1 pl-4 text-xs text-zinc-400">
          {guide.steps.slice(0, 5).map((step, i) => (
            <li key={i}>{step}</li>
          ))}
        </ol>
        <div className="mt-4 grid gap-3 sm:grid-cols-2 text-[10px]">
          <div>
            <p className="font-bold uppercase tracking-wide text-emerald-400">Enable</p>
            <ul className="mt-1 space-y-0.5 text-zinc-500">
              {guide.requiredPermissions.map((p) => (
                <li key={p}>✓ {p}</li>
              ))}
            </ul>
          </div>
          <div>
            <p className="font-bold uppercase tracking-wide text-red-400">Never enable</p>
            <ul className="mt-1 space-y-0.5 text-zinc-500">
              {guide.forbiddenPermissions.map((p) => (
                <li key={p}>✗ {p}</li>
              ))}
            </ul>
          </div>
        </div>
        <button
          type="button"
          onClick={() => setGuideOpen(true)}
          className="mt-3 text-xs font-semibold text-violet-300 hover:text-violet-200"
        >
          Full {selectedLabel} API guide →
        </button>
        <a
          href={guide.docsUrl}
          target="_blank"
          rel="noopener noreferrer"
          className="mt-2 block text-[10px] text-zinc-500 hover:text-violet-300"
        >
          Official {selectedLabel} documentation ↗
        </a>
      </div>

      <ExchangeApiGuideDrawer provider={exchange} open={guideOpen} onClose={() => setGuideOpen(false)} />
    </>
  );
}
