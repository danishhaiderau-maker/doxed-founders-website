'use client';

import Link from 'next/link';
import { formatUsd, formatTokenPrice } from '@dcf/utils';
import { FounderBadges } from '@/components/founder-badges';
import type { ProjectSummary } from '@/lib/api';

function formatMc(value: number | null | undefined) {
  if (value == null) return '—';
  if (value >= 1_000_000_000) return `$${(value / 1_000_000_000).toFixed(2)}B`;
  if (value >= 1_000_000) return `$${(value / 1_000_000).toFixed(2)}M`;
  if (value >= 1_000) return `$${(value / 1_000).toFixed(1)}K`;
  return formatUsd(value, 0);
}

function priceChangeClass(value: number | null | undefined) {
  if (value == null) return 'text-[var(--color-muted)]';
  if (value > 0) return 'text-emerald-400';
  if (value < 0) return 'text-red-400';
  return 'text-[var(--color-muted)]';
}

interface ProjectCardProps {
  project: ProjectSummary;
}

export function ProjectCard({ project }: ProjectCardProps) {
  const change = project.metrics?.priceChange24h;

  return (
    <Link
      href={`/project/${project.slug}`}
      className="group block rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] p-5 transition hover:border-[var(--color-accent)]/50 hover:bg-[var(--color-card)]/80"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 items-start gap-3">
          <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-lg bg-[var(--color-background)] text-sm font-bold text-[var(--color-accent)]">
            {project.logoUrl ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img
                src={project.logoUrl}
                alt=""
                className="h-11 w-11 rounded-lg object-cover"
              />
            ) : (
              project.ticker.slice(0, 2)
            )}
          </div>
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <h3 className="truncate font-semibold group-hover:text-[var(--color-accent)]">
                {project.name}
              </h3>
              <span className="text-xs text-[var(--color-muted)]">{project.ticker}</span>
              {project.featured && (
                <span className="rounded bg-amber-950/50 px-1.5 py-0.5 text-[10px] font-semibold uppercase text-amber-300">
                  Featured
                </span>
              )}
            </div>
            {project.category && (
              <p className="mt-0.5 text-xs text-[var(--color-muted)]">
                {project.category.name} · {project.chain.name}
              </p>
            )}
          </div>
        </div>
        <div className="shrink-0 text-right">
          <p className="font-medium">
            {project.metrics?.priceUsd != null
              ? formatTokenPrice(project.metrics.priceUsd)
              : '—'}
          </p>
          {change != null && (
            <p className={`text-xs ${priceChangeClass(change)}`}>
              {change > 0 ? '+' : ''}
              {change.toFixed(2)}%
            </p>
          )}
        </div>
      </div>

      {project.summary && (
        <p className="mt-3 line-clamp-2 text-sm text-[var(--color-muted)]">{project.summary}</p>
      )}

      <div className="mt-4 flex flex-wrap items-center justify-between gap-2">
        <div className="flex gap-4 text-xs text-[var(--color-muted)]">
          <span>MC {formatMc(project.metrics?.marketCap)}</span>
          <span>Vol {formatMc(project.metrics?.volume24h)}</span>
        </div>
        {project.founder && (
          <span className="text-xs text-[var(--color-muted)]">
            {project.founder.name}
          </span>
        )}
      </div>

      {project.founder && (
        <FounderBadges verifications={project.founder.verifications} compact />
      )}
    </Link>
  );
}

export function ProjectMetricsGrid({
  metrics,
}: {
  metrics: ProjectSummary['metrics'];
}) {
  if (!metrics) {
    return (
      <p className="text-sm text-[var(--color-muted)]">Market data not available yet.</p>
    );
  }

  const items = [
    { label: 'Price', value: metrics.priceUsd != null ? formatTokenPrice(metrics.priceUsd) : '—' },
    { label: 'Market cap', value: formatMc(metrics.marketCap) },
    { label: 'FDV', value: formatMc(metrics.fdv) },
    { label: '24h volume', value: formatMc(metrics.volume24h) },
    { label: 'Liquidity', value: formatMc(metrics.liquidity) },
    { label: 'Holders', value: metrics.holders?.toLocaleString() ?? '—' },
    {
      label: '24h change',
      value:
        metrics.priceChange24h != null
          ? `${metrics.priceChange24h > 0 ? '+' : ''}${metrics.priceChange24h.toFixed(2)}%`
          : '—',
      valueClass: priceChangeClass(metrics.priceChange24h),
    },
  ];

  return (
    <div className="space-y-2">
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {items.map((item) => (
          <div
            key={item.label}
            className="rounded-lg border border-[var(--color-border)] bg-[var(--color-background)] px-4 py-3"
          >
            <p className="text-xs text-[var(--color-muted)]">{item.label}</p>
            <p className={`mt-1 font-semibold ${item.valueClass ?? ''}`}>{item.value}</p>
          </div>
        ))}
      </div>
      {metrics.updatedAt && (
        <p className="text-xs text-emerald-400/80">
          Live market data · updated{' '}
          {formatMetricsAge(metrics.updatedAt)}
        </p>
      )}
    </div>
  );
}

function formatMetricsAge(iso: string) {
  const mins = Math.floor((Date.now() - new Date(iso).getTime()) / 60000);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.floor(hrs / 24)}d ago`;
}
