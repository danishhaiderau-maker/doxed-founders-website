'use client';

import { useState } from 'react';
import { cn, DEPLOYMENT_MODES, type DeploymentModeId } from '@dcf/utils';

/**
 * Three-card full-screen wizard step (doc §2). Appears once, at project
 * creation, right after naming the project. HYBRID carries the "⭐ Recommended"
 * badge. Every mode card is fully clickable — no grayed-out buttons.
 */
export function DeploymentModeSelector({
  projectName,
  onChoose,
  onCancel,
}: {
  projectName: string;
  onChoose: (mode: DeploymentModeId) => void | Promise<void>;
  onCancel?: () => void;
}) {
  const [submitting, setSubmitting] = useState<DeploymentModeId | null>(null);

  async function handleChoose(mode: DeploymentModeId) {
    setSubmitting(mode);
    try {
      await onChoose(mode);
    } finally {
      setSubmitting(null);
    }
  }

  const accentRing: Record<string, string> = {
    slate: 'hover:border-slate-400/60 hover:bg-slate-900/60',
    emerald: 'hover:border-emerald-400/60 hover:bg-emerald-950/30',
    violet: 'hover:border-violet-400/60 hover:bg-violet-950/30',
  };
  const accentButton: Record<string, string> = {
    slate: 'border-slate-500/50 text-slate-200 hover:bg-slate-800/60',
    emerald: 'border-emerald-500/50 text-emerald-200 hover:bg-emerald-900/40',
    violet: 'border-violet-500/50 text-violet-200 hover:bg-violet-900/40',
  };

  return (
    <div className="min-h-screen bg-zinc-950 px-6 py-12 text-white">
      <div className="mx-auto max-w-5xl">
        <p className="text-sm uppercase tracking-widest text-zinc-500">
          New Project · {projectName}
        </p>
        <h1 className="mt-3 text-3xl font-bold">Where should this project live?</h1>
        <p className="mt-2 text-zinc-400">
          You can change this at any time. Nothing is locked in.
        </p>

        <div className="mt-10 grid gap-5 md:grid-cols-3">
          {DEPLOYMENT_MODES.map((mode) => (
            <div
              key={mode.id}
              className={cn(
                'relative flex flex-col rounded-2xl border border-zinc-800 bg-zinc-900/40 p-6 transition',
                accentRing[mode.accent],
              )}
            >
              {mode.recommended && (
                <span className="absolute -top-3 left-6 rounded-full bg-violet-600 px-2.5 py-0.5 text-[11px] font-semibold text-white shadow">
                  ⭐ Recommended
                </span>
              )}

              <div className="text-3xl" aria-hidden>
                {mode.emoji}
              </div>
              <h2 className="mt-3 text-xl font-semibold">{mode.label}</h2>
              <p className="mt-2 flex-1 text-sm text-zinc-400">{mode.tagline}</p>

              <ul className="mt-4 space-y-1 text-xs text-zinc-500">
                {MODE_FEATURES[mode.id].map((feature) => (
                  <li key={feature} className="flex items-start gap-1.5">
                    <span className="mt-0.5 text-emerald-400">✓</span>
                    <span>{feature}</span>
                  </li>
                ))}
              </ul>

              <p className="mt-4 text-xs text-zinc-500">{mode.cost}</p>

              <button
                type="button"
                disabled={submitting !== null}
                onClick={() => handleChoose(mode.id)}
                className={cn(
                  'mt-5 w-full rounded-lg border px-4 py-2 text-sm font-medium transition disabled:opacity-50',
                  accentButton[mode.accent],
                )}
              >
                {submitting === mode.id ? 'Setting up…' : `Choose ${mode.label}`}
              </button>
            </div>
          ))}
        </div>

        <div className="mt-10 rounded-xl border border-zinc-800 bg-zinc-900/30 p-5">
          <p className="text-xs font-semibold uppercase tracking-widest text-zinc-500">
            What you get in every mode
          </p>
          <div className="mt-3 grid gap-2 text-sm text-zinc-300 sm:grid-cols-2">
            {SHARED_FEATURES.map((feature) => (
              <div key={feature} className="flex items-center gap-2">
                <span className="text-emerald-400">✓</span>
                <span>{feature}</span>
              </div>
            ))}
          </div>
        </div>

        {onCancel && (
          <div className="mt-8 text-center">
            <button
              type="button"
              onClick={onCancel}
              className="text-sm text-zinc-500 underline hover:text-zinc-300"
            >
              Cancel
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

const SHARED_FEATURES = [
  'AI Gateway (GLM 5.2 / DeepSeek routing)',
  'Real git history',
  'Real database (SQLite or Postgres)',
  'Real HTTPS URL',
  'Phone remote control',
  'Memory Engine',
];

const MODE_FEATURES: Record<DeploymentModeId, string[]> = {
  PRIVATE: [
    'Runs entirely on your laptop',
    'Local Forgejo git forge + SQLite',
    'Cloudflare Tunnel on demand',
    'Tailscale phone remote',
  ],
  PUBLIC: [
    'GitHub + Vercel + Neon',
    'Global CDN, stays up while you sleep',
    'Postgres + branching (Neon)',
    'Public URL for real users',
  ],
  HYBRID: [
    'Build privately first',
    'One-click publish to cloud',
    'Code + history + data move with you',
    'Nothing rewritten at launch',
  ],
};
