'use client';

import Link from 'next/link';
import type { AiTeamAgentCard } from '@/lib/copilot-ai-stack';
import { AI_STACK_HREF } from '@/lib/copilot-ai-stack';

const STATUS_STYLES: Record<
  AiTeamAgentCard['status'],
  { dot: string; text: string; border: string }
> = {
  ready: { dot: 'bg-emerald-400', text: 'text-emerald-300', border: 'border-emerald-500/25' },
  working: { dot: 'bg-amber-400 animate-pulse', text: 'text-amber-200', border: 'border-amber-500/25' },
  offline: { dot: 'bg-zinc-500', text: 'text-zinc-500', border: 'border-zinc-700/60' },
  needs_setup: { dot: 'bg-amber-500/80', text: 'text-amber-200/90', border: 'border-amber-500/20' },
};

export function FounderAiTeamStrip({ agents }: { agents: AiTeamAgentCard[] }) {
  const needsAny = agents.some((a) => a.status === 'needs_setup');

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-[10px] font-semibold uppercase tracking-wider text-violet-300/90">
          Founder AI Team
        </p>
        {needsAny && (
          <Link href={AI_STACK_HREF} className="text-[10px] text-violet-400 hover:underline">
            Connect in Settings →
          </Link>
        )}
      </div>
      <div className="grid gap-2 sm:grid-cols-3">
        {agents.map((agent) => {
          const s = STATUS_STYLES[agent.status];
          return (
            <div
              key={agent.id}
              className={`rounded-lg border bg-zinc-950/50 px-3 py-2.5 ${s.border}`}
            >
              <div className="flex items-center justify-between gap-2">
                <p className="text-xs font-semibold text-white">{agent.label}</p>
                <span className={`inline-flex items-center gap-1 text-[10px] font-medium ${s.text}`}>
                  <span className={`h-1.5 w-1.5 rounded-full ${s.dot}`} aria-hidden />
                  {agent.statusLabel}
                </span>
              </div>
              <p className="mt-1 text-[10px] text-zinc-500">{agent.role}</p>
              {agent.providerLabel && (
                <p className="mt-0.5 text-[10px] text-zinc-600">{agent.providerLabel}</p>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
