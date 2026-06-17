'use client';

import { STARTER_PACKS, type StarterPackId } from '@dcf/utils';

type Props = {
  selectedPack: StarterPackId | null;
  onSelect: (pack: StarterPackId) => void;
  disabled?: boolean;
};

export function FounderStarterPackPicker({ selectedPack, onSelect, disabled }: Props) {
  return (
    <div className="space-y-3">
      <p className="text-xs text-zinc-500">
        Recommendations only — pick what fits. Render is suggested for beginners (one dashboard, no card on
        hobby tier).
      </p>
      <div className="grid gap-3 sm:grid-cols-2">
        {STARTER_PACKS.map((pack) => {
          const active = selectedPack === pack.id;
          return (
            <button
              key={pack.id}
              type="button"
              disabled={disabled}
              onClick={() => onSelect(pack.id)}
              className={`rounded-xl border p-4 text-left transition ${
                active
                  ? 'border-cyan-400/60 bg-cyan-950/25 ring-1 ring-cyan-400/30'
                  : 'border-zinc-800 bg-zinc-950/50 hover:border-zinc-600'
              } disabled:opacity-50`}
            >
              <div className="flex flex-wrap items-center gap-2">
                <p className="font-semibold text-white">{pack.label}</p>
                {pack.recommended && (
                  <span className="rounded-full bg-emerald-600/90 px-2 py-0.5 text-[10px] font-medium text-white">
                    Recommended
                  </span>
                )}
              </div>
              <p className="mt-1 text-xs text-zinc-400">{pack.description}</p>
              <p className="mt-2 text-[10px] text-amber-200/80">{pack.freeTierNote}</p>
              {active && (
                <ol className="mt-3 list-inside list-decimal space-y-1 border-t border-zinc-800/80 pt-3 text-[11px] text-zinc-400">
                  {pack.steps.map((step) => (
                    <li key={step}>{step}</li>
                  ))}
                </ol>
              )}
            </button>
          );
        })}
      </div>
    </div>
  );
}
