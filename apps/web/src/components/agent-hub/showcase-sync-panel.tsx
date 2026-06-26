'use client';

import { useEffect, useMemo, useState } from 'react';
import {
  computeShowcaseSyncScore,
  formatShowcaseSyncPct,
  getDefaultShowcaseSyncStopThreshold,
  type ShowcaseSyncScoreInput,
} from '@dcf/utils';

const STORAGE_KEY = 'relay-sim-auto-stop-threshold';
const LIVE_STORAGE_KEY = 'live-copy-auto-stop-threshold';
const LIVE_FLATTEN_KEY = 'live-copy-flatten-on-breach';

function readThreshold(storageKey = STORAGE_KEY): number {
  if (typeof window === 'undefined') return getDefaultShowcaseSyncStopThreshold();
  const raw = localStorage.getItem(storageKey);
  const n = raw ? Number(raw) : NaN;
  return Number.isFinite(n) && n >= 90 && n <= 100 ? n : getDefaultShowcaseSyncStopThreshold();
}

function syncRingColor(pct: number): string {
  if (pct >= 99) return 'text-emerald-400';
  if (pct >= 98) return 'text-sky-400';
  if (pct >= 90) return 'text-amber-400';
  return 'text-red-400';
}

function syncBarColor(pct: number): string {
  if (pct >= 99) return 'bg-emerald-500';
  if (pct >= 98) return 'bg-sky-500';
  if (pct >= 90) return 'bg-amber-500';
  return 'bg-red-500';
}

/** Showcase sync meter + auto-stop guard for relay sim (and optional live). */
export function ShowcaseSyncPanel({
  input,
  mode,
  simActive,
  liveActive,
  onAutoStop,
  autoStopBusy,
}: {
  input: ShowcaseSyncScoreInput;
  mode: 'sim' | 'live';
  simActive?: boolean;
  liveActive?: boolean;
  onAutoStop?: (opts?: { flatten?: boolean }) => void;
  autoStopBusy?: boolean;
}) {
  const score = useMemo(() => computeShowcaseSyncScore(input), [input]);
  const storageKey = mode === 'live' ? LIVE_STORAGE_KEY : STORAGE_KEY;
  const [autoStopEnabled, setAutoStopEnabled] = useState(false);
  const [flattenOnBreach, setFlattenOnBreach] = useState(false);
  const [threshold, setThreshold] = useState(getDefaultShowcaseSyncStopThreshold);
  const [autoStopped, setAutoStopped] = useState(false);

  useEffect(() => {
    setThreshold(readThreshold(storageKey));
    if (mode === 'live' && typeof window !== 'undefined') {
      setFlattenOnBreach(localStorage.getItem(LIVE_FLATTEN_KEY) === '1');
    }
  }, [mode, storageKey]);

  useEffect(() => {
    const active = mode === 'sim' ? simActive : liveActive;
    if (!active || !autoStopEnabled || !onAutoStop) return;
    if (score.pct < threshold && !autoStopped && !autoStopBusy) {
      setAutoStopped(true);
      onAutoStop({ flatten: mode === 'live' && flattenOnBreach });
    }
  }, [
    mode,
    simActive,
    liveActive,
    autoStopEnabled,
    flattenOnBreach,
    score.pct,
    threshold,
    onAutoStop,
    autoStopped,
    autoStopBusy,
  ]);

  useEffect(() => {
    if (mode === 'sim' && simActive) setAutoStopped(false);
    if (mode === 'live' && liveActive) setAutoStopped(false);
  }, [mode, simActive, liveActive]);

  const persistThreshold = (v: number) => {
    setThreshold(v);
    localStorage.setItem(storageKey, String(v));
  };

  const persistFlatten = (v: boolean) => {
    setFlattenOnBreach(v);
    localStorage.setItem(LIVE_FLATTEN_KEY, v ? '1' : '0');
  };

  return (
    <section className="rounded-xl border border-violet-500/35 bg-violet-950/15 p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-[10px] font-bold uppercase tracking-[0.2em] text-violet-300">
            Showcase sync
          </p>
          <p className="mt-1 text-xs text-zinc-500">
            Auto-sync polls showcase :7002 every few seconds — no manual button needed. Score
            reflects trade ID match, ledger reconcile, and lifecycle integrity.
          </p>
        </div>
        <div className="text-right">
          <p className={`text-2xl font-black tabular-nums ${syncRingColor(score.pct)}`}>
            {formatShowcaseSyncPct(score.pct)}
          </p>
          <p className="text-[10px] font-semibold uppercase tracking-wider text-zinc-500">
            {score.label}
          </p>
        </div>
      </div>

      <div className="mt-3 h-2 overflow-hidden rounded-full bg-zinc-800">
        <div
          className={`h-full transition-all duration-500 ${syncBarColor(score.pct)}`}
          style={{ width: `${Math.min(100, score.pct)}%` }}
        />
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-2 text-[11px]">
        <span
          className={`rounded-full px-2.5 py-0.5 font-bold uppercase ${
            score.autoSyncing
              ? 'bg-emerald-500/15 text-emerald-300'
              : 'bg-red-500/15 text-red-300'
          }`}
        >
          Auto-sync {score.autoSyncing ? 'ON' : 'OFF'}
        </span>
        {score.healthy ? (
          <span className="text-zinc-500">Mirroring showcase within tolerance</span>
        ) : (
          <span className="text-amber-200/90">{score.issues[0] ?? 'Sync below target'}</span>
        )}
      </div>

      {score.issues.length > 1 ? (
        <ul className="mt-2 space-y-0.5 text-[11px] text-zinc-500">
          {score.issues.slice(1, 4).map((issue) => (
            <li key={issue}>· {issue}</li>
          ))}
        </ul>
      ) : null}

      {mode === 'sim' || mode === 'live' ? (
        <div className="mt-4 rounded-lg border border-zinc-800 bg-black/25 p-3">
          <label className="flex cursor-pointer items-start gap-2 text-xs text-zinc-300">
            <input
              type="checkbox"
              checked={autoStopEnabled}
              onChange={(e) => setAutoStopEnabled(e.target.checked)}
              className="mt-0.5"
            />
            <span>
              <strong className="text-white">
                {mode === 'live' ? 'Stop live copy' : 'Stop simulation'} if sync drops below
              </strong>{' '}
              <select
                value={threshold}
                onChange={(e) => persistThreshold(Number(e.target.value))}
                className="mx-1 rounded border border-zinc-700 bg-zinc-900 px-1.5 py-0.5 text-xs"
                onClick={(e) => e.stopPropagation()}
              >
                {[99, 98, 97, 95, 90].map((v) => (
                  <option key={v} value={v}>
                    {v}%
                  </option>
                ))}
              </select>
              {mode === 'live'
                ? '— pauses relay and cancels pending orders.'
                : '— protects you before going live with real money.'}
            </span>
          </label>
          {mode === 'live' ? (
            <label className="mt-3 flex cursor-pointer items-start gap-2 text-xs text-zinc-300">
              <input
                type="checkbox"
                checked={flattenOnBreach}
                onChange={(e) => persistFlatten(e.target.checked)}
                className="mt-0.5"
              />
              <span>
                <strong className="text-white">Also close all open positions</strong> on breach —
                emergency flatten at market to limit damage when sync diverges.
              </span>
            </label>
          ) : null}
          {autoStopped ? (
            <p className="mt-2 text-[11px] font-semibold text-amber-200">
              {mode === 'live' ? 'Live copy' : 'Simulation'} auto-stopped — sync fell below {threshold}%.
            </p>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
