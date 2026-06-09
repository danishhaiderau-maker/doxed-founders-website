'use client';

import { formatPercent } from '@dcf/utils';

/** Simple 30D performance vs buy-and-hold sparkline (mockup-style). */
export function AgentPerformanceChart({
  agentReturnPct,
  label = 'Conservative BTC Agent',
}: {
  agentReturnPct: number;
  label?: string;
}) {
  const buyHold = Math.max(-20, Math.min(30, agentReturnPct * 0.55));
  const agentPoints = buildCurve(agentReturnPct, 30);
  const holdPoints = buildCurve(buyHold, 30);
  const w = 400;
  const h = 120;

  function toPath(values: number[]) {
    const min = Math.min(...values, 0);
    const max = Math.max(...values, 1);
    const range = max - min || 1;
    return values
      .map((v, i) => {
        const x = (i / (values.length - 1)) * w;
        const y = h - ((v - min) / range) * (h - 8) - 4;
        return `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`;
      })
      .join(' ');
  }

  return (
    <section className="rounded-2xl border border-zinc-800 bg-zinc-950/50 p-5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-sm font-bold uppercase tracking-widest text-zinc-500">Performance · 30D</h2>
        <div className="flex gap-4 text-xs">
          <span className="flex items-center gap-1.5 text-emerald-400">
            <span className="h-0.5 w-4 bg-emerald-400" /> {label}
          </span>
          <span className="flex items-center gap-1.5 text-zinc-500">
            <span className="h-0.5 w-4 bg-zinc-600" /> BTC buy & hold
          </span>
        </div>
      </div>
      <svg viewBox={`0 0 ${w} ${h}`} className="mt-4 h-32 w-full" preserveAspectRatio="none">
        <path d={toPath(holdPoints)} fill="none" stroke="rgb(82 82 91)" strokeWidth="2" />
        <path d={toPath(agentPoints)} fill="none" stroke="rgb(52 211 153)" strokeWidth="2.5" />
      </svg>
      <div className="mt-2 flex justify-between text-xs text-zinc-500">
        <span>Agent {formatPercent(agentReturnPct)}</span>
        <span>BTC B&H {formatPercent(buyHold)}</span>
      </div>
    </section>
  );
}

function buildCurve(totalReturnPct: number, n: number) {
  const out: number[] = [0];
  let acc = 0;
  for (let i = 1; i < n; i++) {
    const t = i / (n - 1);
    const wave = Math.sin(i * 0.45) * (totalReturnPct * 0.08);
    acc = totalReturnPct * t + wave;
    out.push(acc);
  }
  return out;
}
