'use client';

import { useCallback, useEffect, useState } from 'react';
import {
  fetchDexPrice,
  swapOnDex,
  type DexPriceResponse,
} from '@/lib/api';

/**
 * DexPanel — the post-launch swap UI (stub). Input amount (USD), output
 * amount (tokens), 0.1% fee displayed, "Swap" button. Clearly labeled as
 * devnet/demo — no real AMM curve, no real settlement yet.
 *
 * The 0.1% fee math is real (accrues to PlatformTreasury on the backend) so
 * the treasury ledger is correct from day one.
 */
export function DexPanel({
  launchId,
  accessToken,
}: {
  launchId: string;
  accessToken: string;
}) {
  const [price, setPrice] = useState<DexPriceResponse | null>(null);
  const [input, setInput] = useState('10');
  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const loadPrice = useCallback(async () => {
    try {
      const p = await fetchDexPrice(launchId);
      setPrice(p);
    } catch {
      // price unavailable — surfaces via disabled state
    }
  }, [launchId]);

  useEffect(() => {
    void loadPrice();
  }, [loadPrice]);

  const inputNum = Number.parseFloat(input) || 0;
  const feeUsd = price ? (inputNum * price.feeBps) / 10000 : 0;
  const netUsd = inputNum - feeUsd;
  const outputTokens = price && price.priceUsd > 0 ? netUsd / price.priceUsd : 0;

  async function handleSwap() {
    setSubmitting(true);
    setError(null);
    setResult(null);
    try {
      const r = await swapOnDex(launchId, inputNum, accessToken);
      setResult(
        `Swapped $${r.inputAmount} → ${r.outputAmount.toFixed(2)} tokens (fee $${r.feeUsd.toFixed(6)})`,
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Swap failed');
    } finally {
      setSubmitting(false);
    }
  }

  const live = price?.live ?? false;

  return (
    <div className="rounded-2xl border border-zinc-800 bg-zinc-950/60 p-5">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <h3 className="text-sm font-semibold uppercase tracking-[0.18em] text-zinc-500">
            DEX Swap
          </h3>
          <span className="rounded bg-amber-500/15 px-1.5 py-0.5 text-[10px] font-bold uppercase text-amber-300">
            Devnet · Demo
          </span>
        </div>
        {price && (
          <span className="text-[11px] text-zinc-500">
            Price: ${price.priceUsd.toFixed(6)}
          </span>
        )}
      </div>

      <div className="mt-4 space-y-3">
        <label className="block">
          <span className="text-[11px] uppercase tracking-wider text-zinc-500">
            You pay (USD)
          </span>
          <input
            type="number"
            min="0"
            step="1"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            disabled={!live || submitting}
            className="mt-1 w-full rounded-lg border border-zinc-700 bg-zinc-900 px-3 py-2 text-sm text-zinc-100 outline-none focus:border-violet-500 disabled:opacity-50"
          />
        </label>

        <div className="flex items-center justify-between text-xs text-zinc-500">
          <span>0.1% platform fee</span>
          <span className="text-zinc-300">${feeUsd.toFixed(6)}</span>
        </div>

        <div className="flex items-center justify-between rounded-lg border border-zinc-800 bg-zinc-900/40 px-3 py-2 text-sm">
          <span className="text-zinc-500">You receive</span>
          <span className="font-semibold text-emerald-300">
            {outputTokens.toFixed(2)} tokens
          </span>
        </div>

        <button
          type="button"
          disabled={!live || submitting || inputNum <= 0}
          onClick={handleSwap}
          className="w-full rounded-lg bg-gradient-to-r from-emerald-600 to-teal-500 px-4 py-2.5 text-sm font-semibold text-white hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {submitting
            ? 'Swapping…'
            : live
              ? 'Swap'
              : 'Swaps open at launch'}
        </button>

        {!live && (
          <p className="text-[11px] text-zinc-600">
            Swaps enable once the commitment window closes and the launch is
            finalized to LIVE.
          </p>
        )}

        {error && (
          <div className="rounded-md border border-red-500/30 bg-red-950/20 px-3 py-2 text-xs text-red-200">
            {error}
          </div>
        )}

        {result && (
          <div className="rounded-md border border-emerald-500/30 bg-emerald-950/20 px-3 py-2 text-xs text-emerald-200">
            {result}
          </div>
        )}
      </div>
    </div>
  );
}
