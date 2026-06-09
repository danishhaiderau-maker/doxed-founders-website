'use client';

import Link from 'next/link';
import { useEffect, useMemo, useState } from 'react';
import {
  BITFINEX_RECOMMEND_BANNER,
  EXCHANGE_API_GUIDES,
  EXCHANGE_PROVIDER_LABELS,
  EXCHANGE_PROVIDERS,
  type ExchangeProvider,
} from '@dcf/utils';
import { ExchangeApiGuideDrawer } from '@/components/agent-hub/exchange-api-guide-drawer';
import { ExchangeProviderOption, fetchExchangeProviders } from '@/lib/api';

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
}: {
  slug: string;
  signedIn: boolean;
  costWeek: number;
}) {
  const [exchange, setExchange] = useState<ExchangeProvider>('bitfinex');
  const [providers, setProviders] = useState<ExchangeProviderOption[]>(FALLBACK_PROVIDERS);
  const [guideOpen, setGuideOpen] = useState(false);

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
    sorted.find((p) => p.id === exchange)?.label ?? EXCHANGE_PROVIDER_LABELS[exchange];
  const hireHref = signedIn
    ? `/agent-hub/${slug}/hire?exchange=${exchange}`
    : `/login?callbackUrl=${encodeURIComponent(`/agent-hub/${slug}/hire?exchange=${exchange}`)}`;

  const steps = [
    { n: 1, label: 'Choose exchange', detail: `${selectedLabel}${exchange === 'bitfinex' ? ' (Recommended)' : ''}` },
    { n: 2, label: 'Connect exchange API', detail: 'Read + trade only — no withdraw' },
    { n: 3, label: 'Admin DeepSeek copy', detail: 'No AI key needed' },
    { n: 4, label: 'Risk acknowledgement', detail: 'Max $500 allocation' },
    { n: 5, label: 'Activate agent', detail: `${costWeek.toLocaleString()} DDollar / week` },
  ];

  return (
    <>
      <div className="rounded-2xl border border-emerald-500/30 bg-emerald-950/15 p-5">
        <p className="text-xs font-bold uppercase text-emerald-400">Showcase tested on Bitfinex</p>
        <p className="mt-2 text-xs text-emerald-100/75">{BITFINEX_RECOMMEND_BANNER}</p>
        <p className="mt-2 text-[11px] text-zinc-400">
          For live hire you can connect any supported exchange below — Bitfinex is recommended, not required.
        </p>
      </div>

      <div className="rounded-2xl border border-zinc-800 bg-zinc-950/60 p-5">
        <p className="font-semibold text-white">Hire this agent</p>
        <p className="mt-2 rounded-lg border border-violet-500/30 bg-violet-950/20 px-3 py-2 text-xs text-violet-100">
          Hiring fee:{' '}
          <strong className="text-white">{costWeek.toLocaleString()} DDollar</strong> for 1 week of live copy
          trading
        </p>
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

        <label className="mt-4 block text-xs text-zinc-400">
          Your exchange
          <select
            value={exchange}
            onChange={(e) => setExchange(e.target.value as ExchangeProvider)}
            className="mt-1 w-full rounded-lg border border-zinc-700 bg-zinc-900 px-3 py-2 text-sm text-zinc-200"
            aria-label="Exchange"
          >
            {sorted.map((p) => (
              <option key={p.id} value={p.id} disabled={!p.available}>
                {p.label}
                {p.id === 'bitfinex' ? ' — Recommended' : ''}
                {!p.available ? ' (Coming soon)' : ''}
              </option>
            ))}
          </select>
        </label>

        <Link
          href={hireHref}
          className="mt-4 block rounded-lg bg-violet-600 py-2.5 text-center text-sm font-semibold hover:bg-violet-500"
        >
          Start setup on {selectedLabel}
        </Link>
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
