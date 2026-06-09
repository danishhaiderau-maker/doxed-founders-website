'use client';

import { useMemo, useState } from 'react';
import { formatPercent, formatUsd } from '@dcf/utils';
import type { TradingAgentActivityEntry } from '@/lib/api';
import { ShareOnXButton } from '@/components/share-on-x-button';

type JourneyNode = {
  id: string;
  label: string;
  subtitle?: string;
  type: 'buy' | 'add' | 'reduce' | 'exit' | 'wait' | 'skip';
};

function isExecutedTrade(item: TradingAgentActivityEntry): boolean {
  const t = item.type.toUpperCase();
  const title = item.title.toUpperCase();
  if (t === 'NO_TRADE' || t === 'AI_REJECTED' || t === 'AI_APPROVED') return false;
  if (title.includes('NO TRADE') || title.includes('AI REJECTED')) return false;
  return (
    t.includes('POSITION') ||
    t.includes('TRADE') ||
    t.includes('OPEN') ||
    t.includes('CLOSE') ||
    item.profitPct != null ||
    item.entryPrice != null
  );
}

function activityToNodes(items: TradingAgentActivityEntry[]): JourneyNode[] {
  return items.filter(isExecutedTrade).slice(0, 8).map((item) => {
    const t = item.type.toLowerCase();
    let type: JourneyNode['type'] = 'exit';
    if (t.includes('open') || t.includes('buy') || t.includes('long') || t.includes('short')) {
      type = 'buy';
    } else if (t.includes('add')) type = 'add';
    else if (t.includes('reduce') || t.includes('partial')) type = 'reduce';
    else if (t.includes('close') || t.includes('exit') || t.includes('position')) type = 'exit';
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

function PnlBar({ pct }: { pct: number }) {
  const width = Math.min(100, Math.abs(pct) * 8);
  const positive = pct >= 0;
  return (
    <div className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-zinc-800">
      <div
        className={`h-full rounded-full transition-all ${positive ? 'bg-emerald-400' : 'bg-red-400'}`}
        style={{ width: `${Math.max(width, 8)}%` }}
      />
    </div>
  );
}

export function AgentTradeJourney({
  activity,
  layout = 'vertical',
  showBalance = true,
}: {
  activity: TradingAgentActivityEntry[];
  layout?: 'vertical' | 'horizontal';
  showBalance?: boolean;
}) {
  const executed = useMemo(() => activity.filter(isExecutedTrade), [activity]);
  const nodes = activityToNodes(executed);
  const [selected, setSelected] = useState<TradingAgentActivityEntry | null>(
    executed[0] ?? null,
  );

  if (nodes.length === 0) {
    return (
      <section className="rounded-2xl border border-zinc-800 bg-zinc-900/30 p-6">
        <h2 className="text-sm font-bold uppercase tracking-widest text-emerald-400/90">
          Trade journey · last 30 days
        </h2>
        <p className="mt-4 text-sm text-zinc-500">
          No completed trades yet — only executed trades appear here (no AI rejections or skips).
        </p>
      </section>
    );
  }

  const isHorizontal = layout === 'horizontal';

  return (
    <section className="rounded-2xl border border-zinc-800 bg-zinc-900/30 p-6">
      <h2 className="text-sm font-bold uppercase tracking-widest text-emerald-400/90">
        Trade journey · last 30 days
      </h2>
      <p className="mt-1 text-xs text-zinc-500">Executed trades only · P/L and balance after each close.</p>

      <div
        className={`mt-6 flex gap-3 ${
          isHorizontal
            ? 'flex-row overflow-x-auto pb-2'
            : 'flex-col items-center sm:flex-row sm:flex-wrap sm:justify-center'
        }`}
      >
        {nodes.map((node) => {
          const item = executed.find((a) => a.id === node.id);
          if (!item) return null;
          const active = selected?.id === node.id;
          const dateStr = new Date(item.createdAt).toLocaleDateString(undefined, {
            month: 'short',
            day: 'numeric',
          });
          const pct = item.profitPct ?? 0;
          const entry = item.entryPrice;
          const exit = item.exitPrice;

          if (isHorizontal) {
            return (
              <button
                key={node.id}
                type="button"
                onClick={() => setSelected(item)}
                className={`min-w-[160px] shrink-0 rounded-xl border-2 px-4 py-3 text-left transition ${
                  NODE_COLORS[node.type]
                } ${active ? 'ring-2 ring-white/30' : 'opacity-90 hover:opacity-100'}`}
              >
                <p className="text-[10px] font-bold uppercase leading-tight">{node.label}</p>
                <p className="mt-1 text-xs text-zinc-400">{dateStr}</p>
                {entry != null && (
                  <p className="mt-1 font-mono text-xs text-zinc-300">
                    ${entry.toLocaleString(undefined, { maximumFractionDigits: 0 })}
                    {exit != null ? ` → $${exit.toLocaleString(undefined, { maximumFractionDigits: 0 })}` : ''}
                  </p>
                )}
                <p className={`mt-1 text-sm font-bold ${pct >= 0 ? 'text-emerald-300' : 'text-red-300'}`}>
                  {formatPercent(pct)}
                  {item.netPnlUsd != null ? ` · ${formatUsd(item.netPnlUsd, 2)}` : ''}
                </p>
                <PnlBar pct={pct} />
                {showBalance && item.balanceUsd != null && (
                  <p className="mt-2 text-[10px] text-zinc-400">
                    Balance: {formatUsd(item.balanceUsd, 0)}
                  </p>
                )}
              </button>
            );
          }

          return (
            <div key={node.id} className="flex flex-col items-center sm:flex-row">
              <button
                type="button"
                onClick={() => setSelected(item)}
                className={`min-w-[120px] rounded-xl border-2 px-4 py-3 text-center text-xs font-bold uppercase transition ${
                  NODE_COLORS[node.type]
                } ${active ? 'scale-105 ring-2 ring-white/30' : 'opacity-90 hover:opacity-100'}`}
              >
                {node.label}
              </button>
            </div>
          );
        })}
      </div>

      {selected && !isHorizontal && (
        <div className="mt-8 rounded-xl border border-zinc-700/80 bg-black/30 p-5">
          <p className="text-[10px] text-zinc-500">{new Date(selected.createdAt).toLocaleString()}</p>
          <p className="mt-1 text-lg font-semibold text-white">{selected.title}</p>
          {selected.entryPrice != null && (
            <p className="mt-2 font-mono text-sm text-zinc-300">
              Entry ${selected.entryPrice.toLocaleString()}
              {selected.exitPrice != null ? ` → Exit $${selected.exitPrice.toLocaleString()}` : ''}
            </p>
          )}
          {selected.profitPct != null && (
            <>
              <p
                className={`mt-2 text-lg font-bold ${selected.profitPct >= 0 ? 'text-emerald-400' : 'text-red-400'}`}
              >
                P/L: {formatPercent(selected.profitPct)}
                {selected.netPnlUsd != null ? ` (${formatUsd(selected.netPnlUsd, 2)})` : ''}
              </p>
              <PnlBar pct={selected.profitPct} />
            </>
          )}
          {showBalance && selected.balanceUsd != null && (
            <p className="mt-3 text-sm text-zinc-400">
              Balance after trade: <span className="font-semibold text-white">{formatUsd(selected.balanceUsd, 0)}</span>
            </p>
          )}
          {selected.reason && (
            <p className="mt-3 text-sm text-zinc-400">{selected.reason}</p>
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
