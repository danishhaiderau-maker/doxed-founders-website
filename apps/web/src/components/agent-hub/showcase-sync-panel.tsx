'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import {
  computeShowcaseSyncScore,
  formatShowcaseSyncPct,
  type ShowcaseSyncScoreInput,
} from '@dcf/utils';

// Simulation remains an optional browser control. Live protection is enforced
// durably by the Railway executor at a fixed 60%; the browser never owns or
// overrides the money-path safety threshold.
const STORAGE_KEY = 'relay-sim-auto-stop-threshold-v2';
export const SIM_DEFAULT_STOP_THRESHOLD_PCT = 60;
export const LIVE_DEFAULT_STOP_THRESHOLD_PCT = 60;
export const LIVE_MIN_STOP_THRESHOLD_PCT = 60;
export const BREACH_CHECKS_REQUIRED = 3;
export const BREACH_MIN_DURATION_MS = 90_000;

function defaultThreshold(mode: 'sim' | 'live'): number {
  return mode === 'live'
    ? LIVE_DEFAULT_STOP_THRESHOLD_PCT
    : SIM_DEFAULT_STOP_THRESHOLD_PCT;
}

function readThreshold(storageKey: string, mode: 'sim' | 'live'): number {
  const fallback = defaultThreshold(mode);
  if (typeof window === 'undefined') return fallback;
  const raw = localStorage.getItem(storageKey);
  const n = raw ? Number(raw) : NaN;
  const minimum = mode === 'live' ? LIVE_MIN_STOP_THRESHOLD_PCT : SIM_DEFAULT_STOP_THRESHOLD_PCT;
  return Number.isFinite(n) && n >= minimum && n <= 100 ? n : fallback;
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
  const [autoStopEnabled, setAutoStopEnabled] = useState(false);
  const [threshold, setThreshold] = useState(() => defaultThreshold(mode));
  const [autoStopped, setAutoStopped] = useState(false);
  const [breachChecks, setBreachChecks] = useState(0);
  const breachChecksRef = useRef(0);
  const breachStartedAtRef = useRef<number | null>(null);

  useEffect(() => {
    setThreshold(
      mode === 'live'
        ? LIVE_DEFAULT_STOP_THRESHOLD_PCT
        : readThreshold(STORAGE_KEY, mode),
    );
  }, [mode]);

  useEffect(() => {
    // Live protection is backend-owned. Running a second browser-local guard
    // would make behavior depend on an open tab and could stop at a stale
    // localStorage threshold.
    const active = mode === 'sim' ? simActive : false;
    const resetBreach = () => {
      breachChecksRef.current = 0;
      breachStartedAtRef.current = null;
      setBreachChecks(0);
    };
    if (!active || !autoStopEnabled || !onAutoStop) {
      resetBreach();
      return;
    }
    // Don't auto-stop while we have no showcase comparison data yet. The sim
    // legitimately waits many minutes for the first canonical Fly signal, and an empty
    // (low) sync score in that window is not a divergence — tripping the stop
    // here was flipping the button to "Stopping…" right after Start.
    const hasComparisonData = Boolean(
      input.reconcile ?? input.fidelity ?? input.lifecycle,
    );
    if (!hasComparisonData || score.pct >= threshold) {
      resetBreach();
      return;
    }

    const now = Date.now();
    if (breachStartedAtRef.current == null) breachStartedAtRef.current = now;
    breachChecksRef.current += 1;
    setBreachChecks(breachChecksRef.current);
    const breachDurationMs = now - breachStartedAtRef.current;

    if (
      breachChecksRef.current >= BREACH_CHECKS_REQUIRED &&
      breachDurationMs >= BREACH_MIN_DURATION_MS &&
      !autoStopped &&
      !autoStopBusy
    ) {
      setAutoStopped(true);
      onAutoStop();
    }
  }, [
    mode,
    simActive,
    autoStopEnabled,
    score.pct,
    threshold,
    onAutoStop,
    autoStopped,
    autoStopBusy,
    input.reconcile,
    input.fidelity,
    input.lifecycle,
  ]);

  useEffect(() => {
    if ((mode === 'sim' && simActive) || (mode === 'live' && liveActive)) {
      setAutoStopped(false);
      breachChecksRef.current = 0;
      breachStartedAtRef.current = null;
      setBreachChecks(0);
    }
  }, [mode, simActive, liveActive]);

  const persistThreshold = (v: number) => {
    setThreshold(v);
    localStorage.setItem(STORAGE_KEY, String(v));
  };

  return (
    <section className="rounded-xl border border-violet-500/35 bg-violet-950/15 p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-[10px] font-bold uppercase tracking-[0.2em] text-violet-300">
            Showcase sync
          </p>
          <p className="mt-1 text-xs text-zinc-500">
            This integrity score refreshes every 45–90s. Live order execution uses the webhook plus a
            2-second backstop, then follows the exact showcase trade ID, limit, chase, fill, and exit.
            Use Refresh to update this score immediately.
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

      {score.notes.length > 0 ? (
        <ul className="mt-2 space-y-0.5 text-[11px] text-zinc-600">
          {score.notes.map((note) => (
            <li key={note}>· {note}</li>
          ))}
        </ul>
      ) : null}

      {mode === 'live' ? (
        <div className="mt-4 rounded-lg border border-emerald-900/60 bg-emerald-950/20 p-3 text-xs text-zinc-300">
          <strong className="text-emerald-300">
            Automatic live safety guard: fixed at {LIVE_DEFAULT_STOP_THRESHOLD_PCT}%
          </strong>
          <p className="mt-1 text-[11px] text-zinc-400">
            Railway checks fresh canonical Fly and Bitfinex reconciliation evidence.
            It pauses live copy only after {BREACH_CHECKS_REQUIRED} low observations
            spanning at least {BREACH_MIN_DURATION_MS / 1000}s. Confirmed unfilled
            entries are cancelled; open positions remain under the normal risk and
            exit manager.
          </p>
          <p className="mt-1 text-[11px] text-zinc-500">
            This protection runs on the server even when this page is closed.
            Readiness remains a separate 98% pre-activation gate.
          </p>
        </div>
      ) : mode === 'sim' ? (
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
                Stop simulation if sync drops below
              </strong>{' '}
              <select
                value={threshold}
                onChange={(e) => persistThreshold(Number(e.target.value))}
                className="mx-1 rounded border border-zinc-700 bg-zinc-900 px-1.5 py-0.5 text-xs"
                onClick={(e) => e.stopPropagation()}
              >
                {[98, 90, 80, 70, 60].map((v) => (
                  <option key={v} value={v}>
                    {v}%
                  </option>
                ))}
              </select>
              — protects you before going live with real money.
            </span>
          </label>
          {autoStopEnabled && !autoStopped ? (
            <p className="mt-2 text-[11px] text-zinc-500">
              Brief fluctuations are ignored. Stop requires {BREACH_CHECKS_REQUIRED} consecutive
              low checks spanning at least {BREACH_MIN_DURATION_MS / 1000}s
              {breachChecks > 0
                ? ` (currently ${Math.min(breachChecks, BREACH_CHECKS_REQUIRED)}/${BREACH_CHECKS_REQUIRED}).`
                : '.'}
            </p>
          ) : null}
          {autoStopped ? (
            <p className="mt-2 text-[11px] font-semibold text-amber-200">
              Simulation auto-stopped — sync fell below {threshold}%.
            </p>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
