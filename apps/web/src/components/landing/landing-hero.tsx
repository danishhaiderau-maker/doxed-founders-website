'use client';

import Link from 'next/link';
import { formatPercent, formatUsd } from '@dcf/utils';
import type { TradingAgentSummary } from '@/lib/api';
import { LandingQuickActions } from '@/components/landing/landing-quick-actions';

type LandingHeroProps = {
  scoutPending?: number;
  topAgent: TradingAgentSummary | null;
};

function AgentPnlTease({ agent }: { agent: TradingAgentSummary }) {
  const pnl = agent.equityUsd - agent.startingBalance;
  const positive = pnl >= 0;

  return (
    <Link
      href={`/agent-hub/${agent.slug}`}
      className="group block rounded-2xl border border-emerald-500/30 bg-gradient-to-br from-emerald-950/40 via-zinc-950/90 to-black p-4 transition hover:border-emerald-400/50 hover:shadow-[0_0_40px_rgba(16,185,129,0.15)]"
    >
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-[10px] font-bold uppercase tracking-[0.2em] text-emerald-400">Showcase agent</p>
          <p className="mt-1 text-lg font-bold text-white group-hover:text-emerald-50">{agent.name}</p>
          <p className="mt-0.5 text-[11px] text-zinc-500">{agent.assetSymbol} · {agent.tradeCount} trades · live</p>
        </div>
        <span className="inline-flex items-center gap-1.5 rounded-full border border-emerald-500/40 bg-emerald-500/10 px-2 py-0.5 text-[9px] font-bold uppercase text-emerald-300">
          <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-emerald-400" />
          Live
        </span>
      </div>
      <dl className="mt-4 grid grid-cols-3 gap-2">
        <div className="rounded-lg border border-zinc-800/80 bg-black/40 px-2.5 py-2">
          <dt className="text-[9px] uppercase tracking-wide text-zinc-500">Net return</dt>
          <dd className={`text-base font-bold ${agent.netReturnPct >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
            {formatPercent(agent.netReturnPct)}
          </dd>
        </div>
        <div className="rounded-lg border border-zinc-800/80 bg-black/40 px-2.5 py-2">
          <dt className="text-[9px] uppercase tracking-wide text-zinc-500">Equity</dt>
          <dd className="text-base font-bold text-white">{formatUsd(agent.equityUsd)}</dd>
        </div>
        <div className="rounded-lg border border-zinc-800/80 bg-black/40 px-2.5 py-2">
          <dt className="text-[9px] uppercase tracking-wide text-zinc-500">Session PnL</dt>
          <dd className={`text-base font-bold ${positive ? 'text-emerald-400' : 'text-red-400'}`}>
            {agent.sessionPnlUsd != null ? formatUsd(agent.sessionPnlUsd) : formatUsd(pnl)}
          </dd>
        </div>
      </dl>
      <p className="mt-3 text-xs font-semibold text-emerald-300/90 group-hover:text-emerald-200">
        Hire or copy trade →
      </p>
    </Link>
  );
}

function FounderOsTease() {
  return (
    <Link
      href="/founder-den?onboard=sovereign"
      className="group block rounded-2xl border border-violet-500/30 bg-gradient-to-br from-violet-950/40 via-zinc-950/90 to-black p-4 transition hover:border-violet-400/50 hover:shadow-[0_0_40px_rgba(139,92,246,0.15)]"
    >
      <p className="text-[10px] font-bold uppercase tracking-[0.2em] text-violet-400">Founder OS</p>
      <p className="mt-1 text-lg font-bold text-white group-hover:text-violet-50">Your laptop is the compute</p>
      <p className="mt-2 text-[11px] leading-relaxed text-zinc-400">
        Control plane on the web. Memory in Founder Vault. Inference via Ollama on your PC —{' '}
        <span className="text-violet-300">$0 cloud AI bill</span>. Cloud is optional glue for DB, auth, and sync.
      </p>
      <ul className="mt-4 space-y-1.5 text-[11px] text-zinc-300">
        {[
          'Start building without upfront cloud spend',
          'Founder Node + Ollama — you own the bill',
          'Five paths: Sovereign, BYO Cloud, Migrate, Starter, Founder Cloud',
        ].map((line) => (
          <li key={line} className="flex gap-2">
            <span className="text-violet-400">✓</span>
            {line}
          </li>
        ))}
      </ul>
      <p className="mt-3 text-xs font-semibold text-violet-300/90 group-hover:text-violet-200">
        Pick your infrastructure path →
      </p>
    </Link>
  );
}

export function LandingHero({ scoutPending = 0, topAgent }: LandingHeroProps) {
  return (
    <section className="relative overflow-hidden rounded-2xl border border-zinc-800/90 bg-[#07070c] shadow-[inset_0_1px_0_rgba(255,255,255,0.04)]">
      <div
        className="pointer-events-none absolute inset-0 bg-[radial-gradient(ellipse_80%_60%_at_50%_-20%,rgba(16,185,129,0.12),transparent),radial-gradient(ellipse_60%_50%_at_100%_50%,rgba(139,92,246,0.1),transparent)]"
        aria-hidden
      />
      <div className="relative px-4 py-6 sm:px-6 sm:py-8 lg:px-8">
        <div className="max-w-3xl">
          <p className="text-[10px] font-bold uppercase tracking-[0.28em] text-zinc-500">Doxxed Crypto</p>
          <h1 className="mt-2 text-2xl font-bold leading-tight tracking-tight text-white sm:text-3xl lg:text-4xl">
            Trade verified agents.{' '}
            <span className="bg-gradient-to-r from-emerald-300 to-violet-300 bg-clip-text text-transparent">
              Ship on Founder OS.
            </span>
          </h1>
          <p className="mt-3 max-w-2xl text-sm leading-relaxed text-zinc-400 sm:text-base">
            Founder OS is the control plane. Your laptop is the compute. Cloud is optional glue — DB, auth, sync.
            We back doxxed founders who show their face, not anonymous pump cycles.
          </p>
        </div>

        <div className="mt-5">
          <LandingQuickActions scoutPending={scoutPending} />
        </div>

        <div className="mt-6 grid gap-3 lg:grid-cols-2">
          {topAgent ? (
            <AgentPnlTease agent={topAgent} />
          ) : (
            <Link
              href="/agent-hub"
              className="flex min-h-[12rem] flex-col items-center justify-center rounded-2xl border border-dashed border-emerald-500/30 bg-emerald-950/20 p-6 text-center"
            >
              <p className="text-sm font-semibold text-emerald-300">BTC trading agent</p>
              <p className="mt-1 text-xs text-zinc-500">Live PnL · copy trade · hire with DDollar</p>
            </Link>
          )}
          <FounderOsTease />
        </div>
      </div>
    </section>
  );
}
