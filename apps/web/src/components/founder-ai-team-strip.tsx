'use client';

import Link from 'next/link';
import type { AiTeamAgentCard } from '@/lib/copilot-ai-stack';

const STATUS_STYLES: Record<
  AiTeamAgentCard['status'],
  { dot: string; text: string; border: string }
> = {
  ready: { dot: 'bg-emerald-400', text: 'text-emerald-300', border: 'border-emerald-500/25' },
  working: { dot: 'bg-amber-400 animate-pulse', text: 'text-amber-200', border: 'border-amber-500/25' },
  offline: { dot: 'bg-zinc-500', text: 'text-zinc-500', border: 'border-zinc-700/60' },
  needs_setup: { dot: 'bg-amber-500/80', text: 'text-amber-200/90', border: 'border-amber-500/20' },
};

function AgentPill({
  agent,
  linkable,
}: {
  agent: AiTeamAgentCard;
  linkable?: boolean;
}) {
  const s = STATUS_STYLES[agent.status];
  const shortLabel = agent.label.replace(' Agent', '');
  const title = [agent.role, agent.providerLabel].filter(Boolean).join(' · ');
  const inner = (
    <>
      <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${s.dot}`} aria-hidden />
      <span className="font-medium">{shortLabel}</span>
      <span className="opacity-80">:</span>
      <span>{agent.statusLabel}</span>
      {agent.providerLabel && agent.status !== 'needs_setup' && (
        <span className="hidden text-zinc-500 sm:inline">({agent.providerLabel})</span>
      )}
    </>
  );

  const className = `inline-flex max-w-full items-center gap-1.5 rounded-full border px-2.5 py-1 text-[10px] ${s.border} ${s.text} ${
    linkable && agent.status === 'needs_setup'
      ? 'cursor-pointer transition hover:border-cyan-500/40 hover:bg-cyan-950/20'
      : ''
  }`;

  if (linkable && agent.connectHref && agent.status === 'needs_setup') {
    return (
      <Link href={agent.connectHref} className={className} title={`Connect ${shortLabel} — ${title}`}>
        {inner}
      </Link>
    );
  }

  if (linkable && agent.connectHref && agent.status !== 'needs_setup') {
    return (
      <Link
        href={agent.connectHref}
        className={`${className} hover:border-emerald-500/40`}
        title={title}
      >
        {inner}
      </Link>
    );
  }

  return (
    <span className={className} title={title}>
      {inner}
    </span>
  );
}

export function FounderAiTeamStrip({
  agents,
  compact = false,
  linkable = false,
}: {
  agents: AiTeamAgentCard[];
  compact?: boolean;
  linkable?: boolean;
}) {
  const needsAny = agents.some((a) => a.status === 'needs_setup');
  const connectHref = agents.find((a) => a.connectHref)?.connectHref ?? '/settings/builder';

  if (compact) {
    return (
      <div className="flex flex-wrap gap-2">
        {agents.map((agent) => (
          <AgentPill key={agent.id} agent={agent} linkable={linkable} />
        ))}
      </div>
    );
  }

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-[10px] font-semibold uppercase tracking-wider text-violet-300/90">
          Founder AI Team
        </p>
        {needsAny && (
          <Link href={connectHref} className="text-[10px] text-violet-400 hover:underline">
            Connect in Settings →
          </Link>
        )}
      </div>
      <div className="grid gap-2 sm:grid-cols-3">
        {agents.map((agent) => {
          const s = STATUS_STYLES[agent.status];
          const card = (
            <div className={`rounded-lg border bg-zinc-950/50 px-3 py-2.5 ${s.border}`}>
              <div className="flex items-center justify-between gap-2">
                <p className="text-xs font-semibold text-white">{agent.label}</p>
                <span className={`inline-flex items-center gap-1 text-[10px] font-medium ${s.text}`}>
                  <span className={`h-1.5 w-1.5 rounded-full ${s.dot}`} aria-hidden />
                  {agent.statusLabel}
                </span>
              </div>
              <p className="mt-1 text-[10px] text-zinc-500">{agent.role}</p>
              {agent.providerLabel && (
                <p className="mt-0.5 text-[10px] text-zinc-400">{agent.providerLabel}</p>
              )}
              {agent.status === 'needs_setup' && agent.connectHref && linkable && (
                <p className="mt-1.5 text-[10px] font-medium text-cyan-400">Tap to connect →</p>
              )}
            </div>
          );

          if (linkable && agent.connectHref) {
            return (
              <Link
                key={agent.id}
                href={agent.connectHref}
                className="block rounded-lg transition hover:opacity-95"
              >
                {card}
              </Link>
            );
          }

          return (
            <div key={agent.id}>{card}</div>
          );
        })}
      </div>
    </div>
  );
}
