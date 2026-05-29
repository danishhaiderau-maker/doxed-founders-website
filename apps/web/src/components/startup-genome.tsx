'use client';

import type { StartupGenome } from '@dcf/utils';

const BARS: { key: keyof Omit<StartupGenome, 'overall'>; label: string }[] = [
  { key: 'execution', label: 'Execution' },
  { key: 'demand', label: 'Demand' },
  { key: 'community', label: 'Community' },
  { key: 'transparency', label: 'Transparency' },
  { key: 'launchReady', label: 'Launch ready' },
];

export function StartupGenomePanel({ genome }: { genome: StartupGenome }) {
  return (
    <div className="rounded-2xl border border-zinc-800 bg-zinc-900/40 p-6">
      <div className="flex items-center justify-between">
        <p className="text-xs font-medium uppercase tracking-widest text-zinc-500">Startup genome</p>
        <span className="rounded-full bg-emerald-950 px-3 py-1 text-sm font-semibold text-emerald-300">
          {genome.overall}/100
        </span>
      </div>
      <div className="mt-5 space-y-4">
        {BARS.map(({ key, label }) => {
          const value = genome[key];
          return (
            <div key={key}>
              <div className="mb-1 flex justify-between text-xs">
                <span className="text-zinc-400">{label}</span>
                <span className="font-medium text-zinc-200">{value}</span>
              </div>
              <div className="h-2 overflow-hidden rounded-full bg-zinc-800">
                <div
                  className="h-full rounded-full bg-gradient-to-r from-emerald-700 to-emerald-400 transition-all"
                  style={{ width: `${Math.min(100, value)}%` }}
                />
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
