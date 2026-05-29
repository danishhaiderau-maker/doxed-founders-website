'use client';

import Link from 'next/link';
import { AGENT_CATEGORY_LABELS } from '@dcf/utils';
import type { FounderAgentSummary } from '@/lib/api';

export function AgentCard({ agent }: { agent: FounderAgentSummary }) {
  return (
    <Link
      href={`/agents/${agent.slug}`}
      className="block rounded-xl border border-zinc-800 bg-zinc-900/50 p-4 transition hover:border-purple-500/40"
    >
      <div className="flex items-start justify-between gap-2">
        <p className="font-semibold text-white">{agent.name}</p>
        <span className="rounded bg-zinc-800 px-2 py-0.5 text-[10px] uppercase text-zinc-400">
          {AGENT_CATEGORY_LABELS[agent.category] ?? agent.category}
        </span>
      </div>
      <p className="mt-2 line-clamp-2 text-xs text-zinc-500">{agent.description}</p>
      <div className="mt-3 flex flex-wrap gap-3 text-[10px] text-zinc-500">
        <span>{agent.followerCount} followers</span>
        <span>{agent.usageCount} runs</span>
        <span>★ {agent.rating || '—'}</span>
      </div>
      <p className="mt-2 text-xs text-purple-300">
        {agent.founder.name}
        {agent.project ? ` → ${agent.project.name}` : ''}
      </p>
    </Link>
  );
}
