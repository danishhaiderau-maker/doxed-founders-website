'use client';

import Link from 'next/link';
import { cn } from '@dcf/utils';

export type PredictionPlatformTab = 'rules' | 'markets' | 'oracle' | 'traders';

const TABS: { id: PredictionPlatformTab; label: string; hint: string }[] = [
  { id: 'rules', label: 'Constitution', hint: 'How markets work' },
  { id: 'markets', label: 'Live markets', hint: 'Stake DDollar' },
  { id: 'oracle', label: 'Oracle rank', hint: 'Forecasting score' },
  { id: 'traders', label: 'Trading rank', hint: 'Paper P&L score' },
];

export function PredictionPlatformNav({
  active,
  className,
}: {
  active: PredictionPlatformTab;
  className?: string;
}) {
  return (
    <nav
      className={cn(
        'flex flex-wrap gap-2 rounded-xl border border-indigo-500/25 bg-indigo-950/20 p-2',
        className,
      )}
      aria-label="Prediction platform sections"
    >
      {TABS.map((tab) => {
        const isActive = tab.id === active;
        return (
          <Link
            key={tab.id}
            href={tab.id === 'rules' ? '/predict?tab=rules' : `/predict?tab=${tab.id}`}
            className={cn(
              'min-w-[7.5rem] flex-1 rounded-lg px-3 py-2.5 text-center transition sm:flex-none sm:min-w-0',
              isActive
                ? 'bg-indigo-600 font-semibold text-white shadow-md shadow-indigo-900/40'
                : 'border border-transparent text-indigo-200/80 hover:border-indigo-500/30 hover:bg-indigo-950/50 hover:text-white',
            )}
          >
            <span className="block text-sm">{tab.label}</span>
            <span className="mt-0.5 block text-[10px] font-normal opacity-80">{tab.hint}</span>
          </Link>
        );
      })}
    </nav>
  );
}
