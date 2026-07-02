'use client';

import { useCallback, useEffect, useState } from 'react';
import {
  fetchSpreadGate,
  postSpreadGate,
  SPREAD_GATE_DEFAULT_BUCKETS,
  type SpreadGateState,
} from '@/lib/api';

/**
 * Spread gate — global per-spread-bucket allow/deny for the showcase bot's limit
 * order placement. Talks DIRECTLY to the bot (bot.doxxedcrypto.digital/api/spread-gate):
 *   GET  -> {disabled_buckets:[int], known_buckets:[int]}
 *   POST {disabled_buckets:[int]} -> persists spread-gate.json + updates in-memory set.
 *
 * Ticked = allowed (default); unticked = blocked from placing a limit order. The hard
 * gate itself is enforced inside the bot's `_place_simulated_limit_order` (which calls
 * `_spread_gate_blocks_signal` right before building the order dict) — sim AND Bitfinex
 * live submit are both skipped when the bucket is disabled, and the bot logs
 * `[SPREAD GATE] bucket=X disabled -> skipping limit order ...`. This control is the UI
 * for that gate — NOT a UI-only filter.
 *
 * When the bot is unreachable the whole control renders disabled with a "bot offline"
 * hint instead of crashing.
 */

const POLL_MS = 30_000;

function bucketLabel(b: number): string {
  return b >= 7 ? '7+' : String(b);
}

export function SpreadGateControl() {
  const [state, setState] = useState<SpreadGateState | null>(null);
  const [online, setOnline] = useState<boolean | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    const r = await fetchSpreadGate();
    setOnline(r.ok);
    if (r.ok) {
      setState({ disabled_buckets: r.disabled_buckets, known_buckets: r.known_buckets });
      setError(null);
    }
  }, []);

  useEffect(() => {
    void load();
    const id = setInterval(() => void load(), POLL_MS);
    return () => clearInterval(id);
  }, [load]);

  const toggle = useCallback(
    async (bucket: number, allow: boolean) => {
      if (!state) return;
      setBusy(true);
      setError(null);
      const next = new Set(state.disabled_buckets);
      if (allow) next.delete(bucket);
      else next.add(bucket);
      const r = await postSpreadGate(Array.from(next).sort((a, b) => a - b));
      setOnline(r.ok);
      if (r.ok) {
        setState({ disabled_buckets: r.disabled_buckets, known_buckets: r.known_buckets });
      } else {
        setError('bot unreachable — update not saved. Retry once the showcase bot is back online.');
      }
      setBusy(false);
    },
    [state],
  );

  const buckets =
    state && state.known_buckets.length > 0 ? state.known_buckets : SPREAD_GATE_DEFAULT_BUCKETS;
  const disabledSet = new Set(state?.disabled_buckets ?? []);
  const offline = online === false;
  const disabled = offline || busy;

  return (
    <section
      className="rounded-2xl border border-amber-500/30 bg-gradient-to-br from-amber-950/15 to-zinc-950/60 p-5 sm:p-6"
      aria-label="Spread gate"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-[10px] font-bold uppercase tracking-[0.2em] text-amber-300">
            Spread gate · global parameter
          </p>
          <h2 className="mt-1 text-lg font-bold text-white">Hard spread-bucket block</h2>
          <p className="mt-1 max-w-2xl text-xs text-zinc-400">
            Unticked buckets are <span className="text-amber-300">blocked from placing a limit order</span>
            {' '}— the bot skips order submission entirely (sim + Bitfinex live) and logs the skip.
            Ticked = allowed (default). Effective on the next signal; existing pending orders are
            unaffected. Persists in <code className="text-zinc-300">spread-gate.json</code> on the bot.
          </p>
        </div>
        <div className="text-right text-[10px] text-zinc-600">
          <p>
            source:{' '}
            <a
              href="https://bot.doxxedcrypto.digital/api/spread-gate"
              target="_blank"
              rel="noreferrer"
              className="underline decoration-dotted hover:text-zinc-400"
            >
              bot /api/spread-gate
            </a>
          </p>
          <p>status: {online === null ? '…' : online ? 'online' : 'offline'}</p>
        </div>
      </div>

      <div className="mt-4 flex flex-wrap gap-2">
        {buckets.map((b) => {
          const allowed = !disabledSet.has(b);
          return (
            <label
              key={b}
              className={`flex cursor-pointer select-none items-center gap-2 rounded-xl border px-3 py-2 text-sm transition ${
                allowed
                  ? 'border-emerald-500/40 bg-emerald-950/20 text-emerald-200'
                  : 'border-red-500/40 bg-red-950/20 text-red-200'
              } ${disabled ? 'cursor-not-allowed opacity-60' : 'hover:border-amber-400/60'}`}
              title={allowed ? 'Bucket allowed — limit orders may place' : 'Bucket disabled — limit orders skipped'}
            >
              <input
                type="checkbox"
                checked={allowed}
                disabled={disabled}
                onChange={(e) => void toggle(b, e.target.checked)}
                className="h-4 w-4 accent-amber-400"
              />
              <span className="font-semibold">Spread {bucketLabel(b)}</span>
              <span className="text-[10px] uppercase tracking-widest opacity-70">
                {allowed ? 'allow' : 'block'}
              </span>
            </label>
          );
        })}
      </div>

      {offline && (
        <p className="mt-3 rounded-xl border border-amber-500/30 bg-amber-950/15 px-3 py-2 text-xs text-amber-200/90">
          bot offline — the showcase bot (bot.doxxedcrypto.digital) is unreachable. The spread gate
          control is disabled until the bot responds.
        </p>
      )}
      {error && !offline && (
        <p className="mt-3 rounded-xl border border-red-500/30 bg-red-950/15 px-3 py-2 text-xs text-red-200/90">
          {error}
        </p>
      )}
    </section>
  );
}
