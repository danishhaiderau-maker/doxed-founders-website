'use client';

import Link from 'next/link';
import type { FeedHubResponse } from '@/lib/api';
type Sections = NonNullable<FeedHubResponse['sections']>;

export function FeedMoneySections({ sections }: { sections: Sections }) {
  const { topMovers, predictions, listings, smartMoney } = sections;

  return (
    <div className="space-y-6">
      <section className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <MoverPanel title="Most bought" items={topMovers.mostBought} accent="emerald" />
        <MoverPanel title="Most sold" items={topMovers.mostSold} accent="orange" />
        <MoverList
          title="Watchlist surges"
          items={topMovers.mostWatchlisted.map((w) => ({
            label: w.ticker,
            sub: w.detail,
            href: w.slug ? `/project/${w.slug}` : undefined,
          }))}
        />
        <MoverList
          title="Discussed"
          items={topMovers.mostDiscussed.map((d) => ({
            label: d.ticker,
            sub: d.trader ?? '',
          }))}
        />
      </section>

      {predictions.length > 0 && (
        <section>
          <h3 className="mb-2 text-xs font-semibold uppercase tracking-wider text-violet-300">
            Trending markets
          </h3>
          <div className="grid gap-2 sm:grid-cols-2">
            {predictions.slice(0, 4).map((p) => (
              <Link
                key={p.id}
                href={p.link ?? '/predict'}
                className="rounded-lg border border-violet-500/25 bg-violet-950/20 px-3 py-2 text-sm hover:border-violet-400/40"
              >
                <span className="text-violet-200">{p.emoji} {p.headline}</span>
                {p.detail && <p className="mt-0.5 text-xs text-zinc-500">{p.detail}</p>}
              </Link>
            ))}
          </div>
        </section>
      )}

      {listings.length > 0 && (
        <section>
          <h3 className="mb-2 text-xs font-semibold uppercase tracking-wider text-emerald-300">
            New listings today
          </h3>
          <ul className="space-y-2">
            {listings.map((l) => (
              <li key={l.id}>
                <Link
                  href={l.link ?? '/discover'}
                  className="block rounded-lg border border-emerald-500/30 bg-emerald-950/15 px-3 py-2 text-sm text-emerald-100 hover:bg-emerald-950/25"
                >
                  🚀 {l.headline}
                </Link>
              </li>
            ))}
          </ul>
        </section>
      )}

      {smartMoney.length > 0 && (
        <section>
          <h3 className="mb-2 text-xs font-semibold uppercase tracking-wider text-amber-200">
            Smart money (24h exits)
          </h3>
          <ul className="flex flex-wrap gap-2">
            {smartMoney.map((t) => (
              <li key={t.userId}>
                <Link
                  href={`/portfolio/${t.userId}`}
                  className="rounded-full border border-amber-500/35 bg-amber-950/20 px-3 py-1 text-xs text-amber-100"
                >
                  {t.name} · ${Math.round(t.pnlUsd).toLocaleString()}
                </Link>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}

function MoverPanel({
  title,
  items,
  accent,
}: {
  title: string;
  items: { ticker: string; usd: number }[];
  accent: 'emerald' | 'orange';
}) {
  const color = accent === 'emerald' ? 'text-emerald-400' : 'text-orange-400';
  return (
    <div className="rounded-lg border border-zinc-800 bg-zinc-950/60 px-3 py-2">
      <p className="text-[10px] font-semibold uppercase tracking-wider text-zinc-500">{title}</p>
      {items.length === 0 ? (
        <p className="mt-1 text-xs text-zinc-600">—</p>
      ) : (
        <ul className="mt-1 space-y-0.5">
          {items.map((i) => (
            <li key={i.ticker} className={`text-sm font-semibold ${color}`}>
              {i.ticker}{' '}
              <span className="font-normal text-zinc-500">${Math.round(i.usd).toLocaleString()}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function MoverList({
  title,
  items,
}: {
  title: string;
  items: { label: string; sub: string; href?: string }[];
}) {
  return (
    <div className="rounded-lg border border-zinc-800 bg-zinc-950/60 px-3 py-2">
      <p className="text-[10px] font-semibold uppercase tracking-wider text-zinc-500">{title}</p>
      {items.length === 0 ? (
        <p className="mt-1 text-xs text-zinc-600">—</p>
      ) : (
        <ul className="mt-1 space-y-1">
          {items.map((i, idx) => (
            <li key={`${i.label}-${idx}`} className="text-xs">
              {i.href ? (
                <Link href={i.href} className="font-semibold text-violet-300 hover:underline">
                  {i.label}
                </Link>
              ) : (
                <span className="font-semibold text-zinc-200">{i.label}</span>
              )}
              {i.sub && <span className="block text-zinc-600">{i.sub}</span>}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
