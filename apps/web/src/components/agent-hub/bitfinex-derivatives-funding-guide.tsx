'use client';

import { BITFINEX_DERIVATIVES_FUNDING_COPY, DEFAULT_SUBSCRIBER_MAX_MARGIN_USD } from '@dcf/utils';

export function BitfinexDerivativesFundingGuide({
  derivativesUsd,
  exchangeUsd,
  fundingUsd,
  compact = false,
}: {
  derivativesUsd?: number | null;
  exchangeUsd?: number | null;
  fundingUsd?: number | null;
  compact?: boolean;
}) {
  const copy = BITFINEX_DERIVATIVES_FUNDING_COPY;
  const ready =
    derivativesUsd != null && derivativesUsd >= DEFAULT_SUBSCRIBER_MAX_MARGIN_USD * 0.9;
  const wrongWallet =
    (exchangeUsd ?? 0) + (fundingUsd ?? 0) > 10 &&
    (derivativesUsd ?? 0) < 5;

  return (
    <section
      className={`rounded-xl border ${
        wrongWallet
          ? 'border-amber-500/45 bg-amber-950/20'
          : ready
            ? 'border-emerald-500/35 bg-emerald-950/15'
            : 'border-amber-500/35 bg-amber-950/15'
      } ${compact ? 'p-3' : 'p-4'}`}
    >
      {wrongWallet && (
        <p className="mb-2 text-xs font-semibold text-amber-200">
          USDT is in the wrong wallet — move it to <strong>Derivatives</strong> before copy trades can fire.
        </p>
      )}
      <p className="text-[10px] font-bold uppercase tracking-widest text-zinc-500">{copy.title}</p>
      <p className={`mt-1 font-semibold text-white ${compact ? 'text-xs' : 'text-sm'}`}>
        {copy.walletLabel}
      </p>
      <p className={`mt-2 text-zinc-400 ${compact ? 'text-[11px]' : 'text-xs'}`}>{copy.summary}</p>

      {(derivativesUsd != null || exchangeUsd != null || fundingUsd != null) && (
        <dl className={`mt-3 grid gap-1 ${compact ? 'text-[10px]' : 'text-xs'} sm:grid-cols-3`}>
          <div>
            <dt className="text-zinc-500">Derivatives (tradeable)</dt>
            <dd className={ready ? 'font-bold text-emerald-300' : 'font-bold text-amber-200'}>
              ${(derivativesUsd ?? 0).toFixed(2)}
            </dd>
          </div>
          <div>
            <dt className="text-zinc-500">Exchange</dt>
            <dd className="text-zinc-300">${(exchangeUsd ?? 0).toFixed(2)}</dd>
          </div>
          <div>
            <dt className="text-zinc-500">Funding</dt>
            <dd className="text-zinc-300">${(fundingUsd ?? 0).toFixed(2)}</dd>
          </div>
        </dl>
      )}

      {!compact && (
        <ol className="mt-3 list-decimal space-y-1 pl-4 text-xs text-zinc-400">
          {copy.depositSteps.map((step) => (
            <li key={step}>{step}</li>
          ))}
        </ol>
      )}

      <p className={`mt-2 text-zinc-500 ${compact ? 'text-[10px]' : 'text-[11px]'}`}>{copy.apiNote}</p>
    </section>
  );
}
