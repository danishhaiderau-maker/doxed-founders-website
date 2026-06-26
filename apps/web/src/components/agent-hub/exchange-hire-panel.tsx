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
import { ShowcaseSyncPanel } from '@/components/agent-hub/showcase-sync-panel';
import type { ShowcaseSyncScoreInput } from '@dcf/utils';
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
  rentalExpired,
  onStopRelay,
  onStartRelay,
  relayBusy,
  syncInput,
  onSyncProtectionBreach,
  syncProtectionBusy,
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
  rentalExpired?: boolean;
  onStopRelay?: () => void;
  onStartRelay?: () => void;
  relayBusy?: boolean;
  syncInput?: ShowcaseSyncScoreInput;
  onSyncProtectionBreach?: (opts?: { flatten?: boolean }) => void;
  syncProtectionBusy?: boolean;
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

  const isLiveHired = hired && instanceMode === 'live';

  const relayState =
    isLiveHired
      ? rentalExpired
        ? ('paused' as const)
        : instanceStatus === 'PAUSED'
          ? ('paused' as const)
          : ('active' as const)
      : ('idle' as const);

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
            <p className="font-semibold text-white">Your {selectedLabel} live copy relay</p>
            {rentalExpired ? (
              <p className="mt-3 rounded-lg border border-red-500/40 bg-red-950/30 px-3 py-2 text-xs font-semibold text-red-100">
                Rental expired — renew above before starting real trading.
              </p>
            ) : (
              <p className="mt-3 rounded-lg border border-emerald-500/30 bg-emerald-950/20 px-3 py-2 text-xs text-emerald-100">
                Derivatives balance:{' '}
                <strong className="text-white">{formatUsd(exchangeBalanceUsd ?? 0, 2)}</strong>
              </p>
            )}
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
              Connect API keys — live copy requires an active weekly rental. Use Relay Sim tab to
              test sync first without spending USDT.
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
              rentalExpired={rentalExpired}
              showSelector={!isLiveHired}
              providers={sorted}
              onExchangeChange={(id) => setExchange(id as ExchangeProvider)}
            />
          </div>
        </div>

        {isLiveHired && syncInput ? (
          <div className="mt-4">
            <ShowcaseSyncPanel
              input={syncInput}
              mode="live"
              liveActive={relayState === 'active'}
              onAutoStop={onSyncProtectionBreach}
              autoStopBusy={syncProtectionBusy}
            />
          </div>
        ) : null}

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
