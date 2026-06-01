'use client';

import { useMemo, useState } from 'react';
import type { DexScreenerPreview } from '@/lib/api';
import {
  computeTokenRiskScore,
  formatTokenPrice,
  formatUsd,
  riskScoreColor,
  RESTRICTED_CASH_THRESHOLD_USD,
  STARTING_CASH_USD,
} from '@dcf/utils';

type TradeAccountabilityModalProps = {
  open: boolean;
  preview: DexScreenerPreview;
  amountUsd: number;
  cashBalance: number;
  resetFeeUsd: number;
  thesis: string;
  onThesisChange: (v: string) => void;
  catalyst: string;
  onCatalystChange: (v: string) => void;
  targetUsd: string;
  onTargetUsdChange: (v: string) => void;
  timeHorizon: string;
  onTimeHorizonChange: (v: string) => void;
  founderDoxxedTick: boolean;
  onFounderDoxxedTickChange: (v: boolean) => void;
  onCancel: () => void;
  onConfirm: () => void;
};

function formatCompactUsd(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return '—';
  if (value >= 1_000_000) return `$${(value / 1_000_000).toFixed(2)}M`;
  if (value >= 1_000) return `$${Math.round(value / 1_000)}K`;
  return formatUsd(value, 0);
}

const THESIS_PRESETS = [
  'Support bounce',
  'Breakout',
  'Long-term hold',
  'Macro thesis',
  'News event',
] as const;

export function TradeAccountabilityModal({
  open,
  preview,
  amountUsd,
  cashBalance,
  resetFeeUsd,
  thesis,
  onThesisChange,
  catalyst,
  onCatalystChange,
  targetUsd,
  onTargetUsdChange,
  timeHorizon,
  onTimeHorizonChange,
  founderDoxxedTick,
  onFounderDoxxedTickChange,
  onCancel,
  onConfirm,
}: TradeAccountabilityModalProps) {
  const [c1, setC1] = useState(false);
  const [c2, setC2] = useState(false);
  const [c3, setC3] = useState(false);
  const [c4, setC4] = useState(false);
  const [c5, setC5] = useState(false);

  const price = Number(preview.marketPreview.priceUsd ?? 0);
  const estTokens = price > 0 ? Math.floor(amountUsd / price) : 0;
  const slippagePct = 1;
  const networkFee = 2.15;
  const totalCost = amountUsd + networkFee;

  const risk = useMemo(
    () =>
      computeTokenRiskScore({
        isDoxxedCurated: preview.isDoxxedCurated,
        userBelievesFounderDoxxed: founderDoxxedTick,
        liquidityUsd: preview.marketPreview.liquidityUsd,
        marketCap: preview.marketPreview.marketCap,
        volume24h: preview.marketPreview.volume24h,
        hasWebsite: Boolean(preview.websiteUrl),
        hasTwitter: Boolean(preview.founderTwitter),
      }),
    [preview, founderDoxxedTick],
  );

  const riskColor = riskScoreColor(risk.score);
  const riskTextClass =
    riskColor === 'green'
      ? 'text-emerald-400'
      : riskColor === 'amber'
        ? 'text-amber-400'
        : 'text-red-400';

  if (!open) return null;

  const balanceAfter = cashBalance - amountUsd;
  const canConfirm = c1 && c2 && c3 && c4 && c5;
  const isVerifiedDoxxed = Boolean(preview.isDoxxedCurated);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <button
        type="button"
        className="absolute inset-0 bg-black/80 backdrop-blur-sm"
        aria-label="Close"
        onClick={onCancel}
      />
      <div className="relative max-h-[94vh] w-full max-w-2xl overflow-y-auto rounded-2xl border border-zinc-700/80 bg-[#0c0c10] shadow-2xl">
        {/* Header */}
        <div className="border-b border-zinc-800 px-6 py-5">
          <div className="flex items-start gap-3">
            <span className="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-amber-500/15 text-lg text-amber-400">
              ⚠
            </span>
            <div>
              <h2 className="text-xl font-bold tracking-tight text-white">Before You Buy</h2>
              <p className="mt-1 text-sm text-zinc-400">
                Please review the risks and confirm before proceeding.
              </p>
            </div>
          </div>
        </div>

        <div className="space-y-4 px-6 py-5">
          {/* Founder status banner */}
          {isVerifiedDoxxed ? (
            <div className="rounded-xl border border-emerald-500/35 bg-emerald-950/25 px-4 py-3.5">
              <p className="flex items-center gap-2 text-sm font-semibold text-emerald-300">
                <span className="text-base">✅</span> Founder verified on Doxxed crypto
              </p>
              <p className="mt-1.5 text-xs leading-relaxed text-emerald-100/80">
                This project has a public, verified founder identity on our platform. That reduces
                anonymous-team risk — it does not guarantee success.
              </p>
            </div>
          ) : (
            <div className="rounded-xl border border-red-500/40 bg-red-950/30 px-4 py-3.5">
              <p className="flex items-center gap-2 text-sm font-semibold text-red-300">
                <span className="text-base">⛔</span> Non-doxxed = High Risk
              </p>
              <p className="mt-1.5 text-xs leading-relaxed text-red-100/85">
                <strong className="text-red-200">{preview.ticker}</strong> is not a verified
                doxxed-founder project. Anonymous teams are often scams. Doxxed crypto exists to
                protect retail — you are buying outside our verified founder layer.
              </p>
            </div>
          )}

          {/* Token stats bar */}
          <div className="overflow-x-auto rounded-xl border border-zinc-800 bg-zinc-900/60">
            <div className="flex min-w-max items-stretch divide-x divide-zinc-800">
              <StatCell
                label={preview.ticker}
                value={preview.projectName}
                sub={formatTokenPrice(price || null)}
              />
              <StatCell label="Market Cap" value={formatCompactUsd(preview.marketPreview.marketCap)} />
              <StatCell label="Liquidity" value={formatCompactUsd(preview.marketPreview.liquidityUsd)} />
              <div className="flex min-w-[120px] flex-col justify-center px-4 py-3">
                <p className="text-[10px] font-medium uppercase tracking-wider text-zinc-500">
                  Risk Score
                </p>
                <p className={`mt-1 text-lg font-bold ${riskTextClass}`}>
                  {risk.score}/10
                </p>
                <p className={`text-xs font-medium ${riskTextClass}`}>{risk.label}</p>
                {risk.userAdjusted && (
                  <p className="mt-0.5 text-[10px] text-zinc-500">Includes your doxxed opinion</p>
                )}
              </div>
            </div>
          </div>

          {/* Risk factor breakdown (collapsible feel) */}
          <details className="rounded-lg border border-zinc-800 bg-zinc-900/40 px-4 py-2 text-xs text-zinc-400">
            <summary className="cursor-pointer py-1 font-medium text-zinc-300">
              How this score was calculated
            </summary>
            <ul className="mt-2 space-y-1 pb-2">
              {risk.factors.map((f) => (
                <li key={f}>· {f}</li>
              ))}
            </ul>
          </details>

          {/* Checkboxes */}
          <div className="space-y-2.5 rounded-xl border border-zinc-800 bg-zinc-900/30 p-4">
            <Checkbox checked={c1} onChange={setC1} label="I understand I can lose 100% of this investment." />
            <Checkbox checked={c2} onChange={setC2} label="I have reviewed the token information and risks above." />
            <Checkbox
              checked={c3}
              onChange={setC3}
              label={
                isVerifiedDoxxed
                  ? 'I understand verified founders still carry market and execution risk.'
                  : 'I understand this project has an anonymous or unverified team.'
              }
            />
            <Checkbox checked={c4} onChange={setC4} label="I am making this decision based on my own research." />
            <Checkbox
              checked={c5}
              onChange={setC5}
              label={`I understand the account reset policy (${formatUsd(resetFeeUsd, 0)} USDC fee if cash falls below ${formatUsd(RESTRICTED_CASH_THRESHOLD_USD, 0)} with or without open positions).`}
            />
          </div>

          {/* Record conviction */}
          <div className="rounded-xl border border-emerald-500/25 bg-emerald-950/15 p-4">
            <p className="text-sm font-semibold text-emerald-200">Record conviction</p>
            <p className="mt-1 text-xs text-emerald-100/70">
              Optional — stored on your position. Months later, Share Conviction shows your thesis
              before the move happened.
            </p>
            <label className="mt-3 block">
              <span className="text-sm font-medium text-zinc-300">
                Why are you buying? <span className="text-zinc-500">(max 280 chars)</span>
              </span>
              <div className="mt-2 flex flex-wrap gap-1.5">
                {THESIS_PRESETS.map((preset) => (
                  <button
                    key={preset}
                    type="button"
                    onClick={() => onThesisChange(preset)}
                    className={`rounded-full border px-2.5 py-1 text-[11px] transition ${
                      thesis === preset
                        ? 'border-emerald-500/60 bg-emerald-950/40 text-emerald-200'
                        : 'border-zinc-700 text-zinc-400 hover:border-zinc-500 hover:text-zinc-200'
                    }`}
                  >
                    {preset}
                  </button>
                ))}
              </div>
              <textarea
                value={thesis}
                onChange={(e) => onThesisChange(e.target.value.slice(0, 280))}
                rows={3}
                placeholder="Doge ETF narrative is underpriced · Strong chart · Long-term hold…"
                className="mt-2 w-full resize-y rounded-xl border border-zinc-700 bg-zinc-950 px-4 py-3 text-sm text-white outline-none placeholder:text-zinc-600 focus:border-emerald-500/50"
              />
            </label>
            <div className="mt-3 grid gap-3 sm:grid-cols-2">
              <label className="block">
                <span className="text-xs font-medium text-zinc-400">Catalyst?</span>
                <input
                  type="text"
                  value={catalyst}
                  onChange={(e) => onCatalystChange(e.target.value.slice(0, 120))}
                  placeholder="Public launch, listing, macro…"
                  className="mt-1.5 w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm text-white outline-none placeholder:text-zinc-600 focus:border-emerald-500/50"
                />
              </label>
              <label className="block">
                <span className="text-xs font-medium text-zinc-400">Price target (USD)</span>
                <input
                  type="number"
                  min={0}
                  step="any"
                  value={targetUsd}
                  onChange={(e) => onTargetUsdChange(e.target.value)}
                  placeholder="0.000001"
                  className="mt-1.5 w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm text-white outline-none placeholder:text-zinc-600 focus:border-emerald-500/50"
                />
              </label>
            </div>
            <label className="mt-3 block">
              <span className="text-xs font-medium text-zinc-400">Time horizon</span>
              <input
                type="text"
                value={timeHorizon}
                onChange={(e) => onTimeHorizonChange(e.target.value.slice(0, 80))}
                placeholder="3–6 months, until launch, Q3 2026…"
                className="mt-1.5 w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm text-white outline-none placeholder:text-zinc-600 focus:border-emerald-500/50"
              />
            </label>
          </div>

          {!isVerifiedDoxxed && (
            <label className="flex cursor-pointer items-start gap-3 rounded-xl border border-zinc-800 bg-zinc-900/30 p-4 transition hover:border-zinc-700">
              <input
                type="checkbox"
                checked={founderDoxxedTick}
                onChange={(e) => onFounderDoxxedTickChange(e.target.checked)}
                className="mt-1 h-4 w-4 rounded border-zinc-600"
              />
              <div>
                <span className="text-sm font-medium text-zinc-200">
                  I believe the founder is publicly doxxed
                </span>
                <p className="mt-1 text-xs text-zinc-500">
                  Your opinion only — not platform verified. Lowers displayed risk by 1 point if
                  checked.
                </p>
              </div>
            </label>
          )}

          {/* Trade breakdown */}
          <div className="overflow-x-auto rounded-xl border border-zinc-800 bg-zinc-900/50">
            <div className="flex min-w-max items-stretch divide-x divide-zinc-800 text-sm">
              <TradeCell label="You're buying" value={formatUsd(amountUsd)} accent />
              <TradeCell
                label="Est. tokens"
                value={estTokens > 0 ? `${estTokens.toLocaleString()} ${preview.ticker}` : '—'}
              />
              <TradeCell label="Slippage" value={`${slippagePct}%`} />
              <TradeCell label="Network fee" value={`~${formatUsd(networkFee)}`} />
              <TradeCell label="Total" value={`~${formatUsd(totalCost)}`} accent />
            </div>
          </div>

          {/* Account risk notice */}
          <div className="rounded-xl border border-amber-500/30 bg-amber-950/20 px-4 py-3.5">
            <p className="text-sm font-semibold text-amber-200">Account Risk Notice</p>
            <p className="mt-1.5 text-xs leading-relaxed text-amber-100/80">
              Current balance: {formatUsd(cashBalance)} · After trade: ~{formatUsd(Math.max(0, balanceAfter))}.
              If your account goes bust (cash near {formatUsd(RESTRICTED_CASH_THRESHOLD_USD, 0)} with or
              without open positions), you may need to pay a {formatUsd(resetFeeUsd, 0)} USDC reset fee for
              fresh {formatUsd(STARTING_CASH_USD, 0)} paper cash. This encourages responsible risk
              management.
            </p>
          </div>
        </div>

        {/* Footer actions */}
        <div className="flex gap-3 border-t border-zinc-800 px-6 py-5">
          <button
            type="button"
            onClick={onCancel}
            className="flex-1 rounded-xl border border-zinc-700 py-3.5 text-sm font-medium text-zinc-300 transition hover:border-zinc-500 hover:text-white"
          >
            Cancel
          </button>
          <button
            type="button"
            disabled={!canConfirm}
            onClick={onConfirm}
            className="flex-[1.4] rounded-xl bg-emerald-600 py-3.5 text-sm font-semibold text-white shadow-lg shadow-emerald-900/30 transition hover:bg-emerald-500 disabled:cursor-not-allowed disabled:opacity-40"
          >
            I Understand the Risks, Buy Now
          </button>
        </div>
      </div>
    </div>
  );
}

function StatCell({
  label,
  value,
  sub,
}: {
  label: string;
  value: string;
  sub?: string;
}) {
  return (
    <div className="flex min-w-[100px] flex-col justify-center px-4 py-3">
      <p className="text-[10px] font-medium uppercase tracking-wider text-zinc-500">{label}</p>
      <p className="mt-1 truncate text-sm font-semibold text-white">{value}</p>
      {sub && <p className="text-xs text-zinc-400">{sub}</p>}
    </div>
  );
}

function TradeCell({
  label,
  value,
  accent,
}: {
  label: string;
  value: string;
  accent?: boolean;
}) {
  return (
    <div className="flex min-w-[110px] flex-col justify-center px-4 py-3">
      <p className="text-[10px] font-medium uppercase tracking-wider text-zinc-500">{label}</p>
      <p className={`mt-1 text-sm font-semibold ${accent ? 'text-emerald-400' : 'text-white'}`}>
        {value}
      </p>
    </div>
  );
}

function Checkbox({
  checked,
  onChange,
  label,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  label: string;
}) {
  return (
    <label className="flex cursor-pointer items-start gap-3 text-sm text-zinc-300">
      <input
        type="checkbox"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
        className="mt-0.5 h-4 w-4 rounded border-zinc-600"
      />
      <span>{label}</span>
    </label>
  );
}
