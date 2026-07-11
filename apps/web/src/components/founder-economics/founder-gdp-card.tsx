'use client';

import { formatDdollarCompact } from '@dcf/utils';

export type FounderGdp = {
  aiValueCreated: number;
  knowledgeShared: { nodes: number; contributions: number; impactDdollar: number };
  productsShipped: number;
  companiesLaunched: number;
  revenueVerified: number;
  verifiedMilestoneCount: number;
  totalDdollarSupply: number;
  activeFounders: number;
  computedAt: string;
};

export function FounderGdpCard({ gdp, loading }: { gdp: FounderGdp | null; loading: boolean }) {
  const cards: { label: string; value: string; hint?: string }[] = [
    { label: 'AI value created', value: formatDdollarCompact(gdp?.aiValueCreated ?? 0), hint: 'Build posts + knowledge' },
    { label: 'Knowledge shared', value: String(gdp?.knowledgeShared.nodes ?? 0), hint: `${gdp?.knowledgeShared.contributions ?? 0} contributions` },
    { label: 'Products shipped', value: String(gdp?.productsShipped ?? 0), hint: 'Via Raise Room' },
    { label: 'Companies launched', value: String(gdp?.companiesLaunched ?? 0), hint: 'Verified milestones' },
    { label: 'Revenue verified', value: `$${(gdp?.revenueVerified ?? 0).toLocaleString()}`, hint: `${gdp?.verifiedMilestoneCount ?? 0} proofs` },
    { label: 'Total DDollar supply', value: formatDdollarCompact(gdp?.totalDdollarSupply ?? 0), hint: `${gdp?.activeFounders ?? 0} active founders` },
  ];

  return (
    <section className="space-y-3">
      <h3 className="text-sm font-semibold uppercase tracking-widest text-zinc-400">Founder GDP</h3>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6">
        {cards.map((c) => (
          <div
            key={c.label}
            className="rounded-xl border border-zinc-800 bg-zinc-900/40 px-4 py-3"
          >
            <p className="text-[11px] uppercase tracking-wider text-zinc-500">{c.label}</p>
            <p className="mt-1 text-xl font-semibold text-white">
              {loading && !gdp ? '—' : c.value}
            </p>
            {c.hint && <p className="mt-1 text-[11px] text-zinc-600">{c.hint}</p>}
          </div>
        ))}
      </div>
    </section>
  );
}
