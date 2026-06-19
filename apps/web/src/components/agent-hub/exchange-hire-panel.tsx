'use client';

import Link from 'next/link';
import { useEffect, useMemo, useState } from 'react';
import {
  BITFINEX_RECOMMEND_BANNER,
  DEFAULT_SUBSCRIBER_MAX_MARGIN_USD,
  EXCHANGE_API_GUIDES,
  EXCHANGE_PROVIDER_LABELS,
  EXCHANGE_PROVIDERS,
  type CopyRelaySimState,
  type ExchangeProvider,
} from '@dcf/utils';
import { ExchangeApiGuideDrawer } from '@/components/agent-hub/exchange-api-guide-drawer';
import { ExchangeRelayControl } from '@/components/agent-hub/exchange-relay-control';
import { AgentRentalCountdown } from '@/components/agent-hub/agent-rental-countdown';
import { BitfinexDerivativesFundingGuide } from '@/components/agent-hub/bitfinex-derivatives-funding-guide';
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
  copyRelaySim,
  onStartRelaySim,
  onStopRelaySim,
  relaySimBusy,
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
  copyRelaySim?: CopyRelaySimState | null;
  onStartRelaySim?: () => void;
  onStopRelaySim?: () => void;
  relaySimBusy?: boolean;
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

  const simActive = Boolean(copyRelaySim?.active);
  const isLiveHired = hired && instanceMode === 'live';

  const relayState =
    isLiveHired
      ? simActive
        ? ('paused' as const)
        : instanceStatus === 'PAUSED'
          ? ('paused' as const)
          : ('active' as const)
      : ('idle' as const);

  const copyModes = [
    {
      id: 'live',
      title: 'Live relay',
      detail: 'Real orders on your exchange when showcase signals fire.',
      active: isLiveHired && !simActive && instanceStatus !== 'PAUSED',
    },
    {
      id: 'sim',
      title: 'Relay simulation',
      detail: 'Paper book, real BTC prices — test reconcile before live.',
      active: simActive,
    },
    {
      id: 'observe',
      title: 'Showcase observe',
      detail: 'Admin research bot only — not your copy session.',
      active: !isLiveHired,
    },
  ];

  return (
    <>
      <div className="rounded-2xl border border-emerald-500/30 bg-emerald-950/15 p-5">
        <p className="text-xs font-bold uppercase text-emerald-400">Connect {selectedLabel} API</p>
        <p className="mt-2 text-xs text-emerald-100/75">{BITFINEX_RECOMMEND_BANNER}</p>
        <p className="mt-2 text-[11px] text-zinc-400">
          Authentic copy trading uses your exchange — not a dummy DDollar session. Platform enforces $
          {DEFAULT_SUBSCRIBER_MAX_MARGIN_USD} max margin per virtual lot at 100x leverage.
        </p>
      </div>

      <div className="rounded-2xl border border-zinc-800 bg-zinc-950/60 p-5">
        {isLiveHired ? (
          <>
            <p className="font-semibold text-white">Your {selectedLabel} copy relay</p>
            {rentalExpiresAt && (
              <div className="mt-3">
                <AgentRentalCountdown expiresAt={rentalExpiresAt} compact />
              </div>
            )}
            <p className="mt-3 rounded-lg border border-emerald-500/30 bg-emerald-950/20 px-3 py-2 text-xs text-emerald-100">
              Derivatives balance:{' '}
              <strong className="text-white">{formatUsd(exchangeBalanceUsd ?? 0, 2)}</strong>
            </p>
            <div className="mt-3 space-y-2">
              {copyModes.slice(0, 2).map((m) => (
                <div
                  key={m.id}
                  className={`rounded-lg border px-3 py-2 text-xs ${
                    m.active
                      ? 'border-emerald-500/40 bg-emerald-950/25 text-emerald-100'
                      : 'border-zinc-800 text-zinc-500'
                  }`}
                >
                  <span className="font-semibold text-zinc-200">{m.title}</span>
                  <span className="mt-0.5 block">{m.detail}</span>
                </div>
              ))}
            </div>
            {exchange === 'bitfinex' && simActive && onStopRelaySim ? (
              <button
                type="button"
                disabled={relaySimBusy}
                onClick={onStopRelaySim}
                className="mt-3 w-full rounded-lg border border-red-500/50 bg-red-950/40 py-2 text-sm font-semibold text-red-200 disabled:opacity-50"
              >
                {relaySimBusy ? '…' : 'Stop relay simulation'}
              </button>
            ) : exchange === 'bitfinex' && onStartRelaySim ? (
              <button
                type="button"
                disabled={relaySimBusy || simActive}
                onClick={onStartRelaySim}
                className="mt-3 w-full rounded-lg border border-sky-500/50 bg-sky-950/40 py-2 text-sm font-semibold text-sky-200 disabled:opacity-50"
              >
                {relaySimBusy ? '…' : 'Start relay simulation'}
              </button>
            ) : null}
            {exchange === 'bitfinex' && (
              <div className="mt-3">
                <BitfinexDerivativesFundingGuide compact />
              </div>
            )}
          </>
        ) : (
          <>
            <p className="font-semibold text-white">Start real copy trading</p>
            <p className="mt-2 text-xs text-zinc-500">
              Connect API keys once — choose live relay or simulation from the main hub.
            </p>
            <p className="mt-2 rounded-lg border border-violet-500/30 bg-violet-950/20 px-3 py-2 text-xs text-violet-100">
              Hiring fee:{' '}
              <strong className="text-white">{costWeek.toLocaleString()} DDollar</strong> / week
            </p>
            <Link
              href={hireHref}
              className="mt-4 block rounded-lg bg-emerald-600 py-2.5 text-center text-sm font-bold text-white hover:bg-emerald-500"
            >
              Connect {selectedLabel} API
            </Link>
          </>
        )}

        <div className="mt-4 rounded-xl border border-zinc-800 bg-black/25 p-4">
          <p className="text-[10px] font-bold uppercase tracking-widest text-zinc-500">Relay control</p>
          <div className="mt-3">
            <ExchangeRelayControl
              slug={slug}
              signedIn={signedIn}
              exchange={exchange}
              exchangeLabel={selectedLabel}
              exchangeConnected={exchangeConnected ?? isLiveHired}
              relayState={relayState}
              busy={relayBusy}
              onStop={onStopRelay}
              onStart={onStartRelay}
              showSelector={!isLiveHired}
              providers={sorted}
              onExchangeChange={(id) => setExchange(id as ExchangeProvider)}
            />
          </div>
        </div>

        {!isLiveHired && exchange === 'bitfinex' && (
          <div className="mt-4">
            <BitfinexDerivativesFundingGuide />
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
