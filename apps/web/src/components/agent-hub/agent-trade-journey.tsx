'use client';

import { useState } from 'react';
import { formatPercent } from '@dcf/utils';
import type { TradingAgentActivityEntry } from '@/lib/api';
import { ShareOnXButton } from '@/components/share-on-x-button';

type JourneyNode = {
  id: string;
  label: string;
  subtitle?: string;
  type: 'buy' | 'add' | 'reduce' | 'exit' | 'wait' | 'skip';
};

function activityToNodes(items: TradingAgentActivityEntry[]): JourneyNode[] {
  return items.slice(0, 8).map((item) => {
    const t = item.type.toLowerCase();
    let type: JourneyNode['type'] = 'wait';
    if (t.includes('open') || t.includes('buy') || t.includes('long') || t.includes('short')) type = 'buy';
    else if (t.includes('add')) type = 'add';
    else if (t.includes('reduce') || t.includes('partial')) type = 'reduce';
    else if (t.includes('close') || t.includes('exit')) type = 'exit';
    else if (t.includes('skip') || t.includes('reject')) type = 'skip';
    return {
      id: item.id,
      label: item.title,
      subtitle: item.reason ?? item.outcome ?? undefined,
      type,
    };
  });
}

const NODE_COLORS: Record<JourneyNode['type'], string> = {
  buy: 'border-emerald-500 bg-emerald-950/50 text-emerald-200',
  add: 'border-blue-500 bg-blue-950/50 text-blue-200',
  reduce: 'border-amber-500 bg-amber-950/50 text-amber-200',
  exit: 'border-violet-500 bg-violet-950/50 text-violet-200',
  wait: 'border-zinc-600 bg-zinc-900/50 text-zinc-300',
  skip: 'border-zinc-700 bg-zinc-950/50 text-zinc-500',
};

export function AgentTradeJourney({
  activity,
}: {
  activity: TradingAgentActivityEntry[];
}) {
  const nodes = activityToNodes(activity);
  const [selected, setSelected] = useState<TradingAgentActivityEntry | null>(
    activity[0] ?? null,
  );

  if (nodes.length === 0) {
    return (
      <section className="rounded-2xl border border-zinc-800 bg-zinc-900/30 p-6">
        <h2 className="text-sm font-bold uppercase tracking-widest text-zinc-500">Trade journey</h2>
        <p className="mt-4 text-sm text-zinc-500">No trades recorded yet — agent is watching the market.</p>
      </section>
    );
  }

  return (
    <section className="rounded-2xl border border-zinc-800 bg-zinc-900/30 p-6">
      <h2 className="text-sm font-bold uppercase tracking-widest text-emerald-400/90">Trade journey</h2>
      <p className="mt-1 text-xs text-zinc-500">Click any step for reasoning, confidence, and share.</p>

      <div className="mt-6 flex flex-col items-center gap-0 sm:flex-row sm:flex-wrap sm:justify-center sm:gap-2">
        {nodes.map((node, i) => {
          const item = activity.find((a) => a.id === node.id);
          const active = selected?.id === node.id;
          return (
            <div key={node.id} className="flex flex-col items-center sm:flex-row">
              <button
                type="button"
                onClick={() => item && setSelected(item)}
                className={`min-w-[120px] rounded-xl border-2 px-4 py-3 text-center text-xs font-bold uppercase transition ${
                  NODE_COLORS[node.type]
                } ${active ? 'ring-2 ring-white/30 scale-105' : 'opacity-90 hover:opacity-100'}`}
              >
                {node.label}
              </button>
              {i < nodes.length - 1 && (
                <span className="my-1 text-zinc-600 sm:mx-1 sm:my-0" aria-hidden>
                  ↓
                </span>
              )}
            </div>
          );
        })}
      </div>

      {selected && (
        <div className="mt-8 rounded-xl border border-zinc-700/80 bg-black/30 p-5">
          <p className="text-[10px] text-zinc-500">
            {new Date(selected.createdAt).toLocaleString()}
          </p>
          <p className="mt-1 text-lg font-semibold text-white">{selected.title}</p>
          {selected.reason && (
            <div className="mt-3">
              <p className="text-[10px] uppercase tracking-widest text-zinc-500">Reason</p>
              <p className="mt-1 text-sm leading-relaxed text-zinc-300">{selected.reason}</p>
            </div>
          )}
          {selected.outcome && (
            <p className="mt-2 text-sm text-zinc-400">Outcome: {selected.outcome}</p>
          )}
          {selected.profitPct != null && (
            <p className={`mt-2 text-sm font-bold ${selected.profitPct >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
              PnL: {formatPercent(selected.profitPct)}
            </p>
          )}
          {selected.edgeScore != null && (
            <p className="mt-1 text-xs text-zinc-500">
              Edge {selected.edgeScore}/{selected.edgeRequired ?? '—'}
              {selected.marketRegime ? ` · ${selected.marketRegime}` : ''}
            </p>
          )}
          {selected.shareText && (
            <div className="mt-4">
              <ShareOnXButton text={selected.shareText} label="Share to X" />
            </div>
          )}
        </div>
      )}
    </section>
  );
}
