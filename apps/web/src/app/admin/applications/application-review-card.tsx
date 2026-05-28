'use client';

import type { ReactNode } from 'react';
import {
  FOUNDER_VERIFICATION_LABELS,
  scoreFounderVerification,
} from '@dcf/utils';
import { AdminApplicationUpdates, PendingApplication } from '@/lib/api';

const CHAIN_OPTIONS = [
  'SOLANA',
  'ETHEREUM',
  'BASE',
  'ARBITRUM',
  'POLYGON',
  'OPTIMISM',
  'AVALANCHE',
  'BNB_CHAIN',
] as const;

function toFormState(item: PendingApplication): AdminApplicationUpdates {
  return {
    projectName: item.projectName,
    ticker: item.ticker,
    websiteUrl: item.websiteUrl ?? '',
    docsUrl: item.docsUrl ?? '',
    whitepaperUrl: item.whitepaperUrl ?? '',
    contractAddress: item.contractAddress ?? '',
    chainSlug: item.chainSlug ?? '',
    dexscreenerUrl: item.dexscreenerUrl ?? '',
    logoUrl: item.logoUrl ?? '',
    telegramUrl: item.telegramUrl ?? '',
    founderName: item.founderName ?? '',
    founderLinkedIn: item.founderLinkedIn ?? '',
    founderTwitter: item.founderTwitter ?? '',
    founderGithub: item.founderGithub ?? '',
    founderVideoUrl: item.founderVideoUrl ?? '',
    founderInterviewUrl: item.founderInterviewUrl ?? '',
    companyDetails: item.companyDetails ?? '',
    auditUrl: item.auditUrl ?? '',
    summary: item.summary ?? '',
    marketPreview: item.marketPreview ?? undefined,
  };
}

function formatUsd(value?: number | string | null) {
  if (value == null || value === '') return '—';
  const n = typeof value === 'string' ? Number(value) : value;
  if (Number.isNaN(n)) return String(value);
  if (n >= 1_000_000) return `$${(n / 1_000_000).toFixed(2)}M`;
  if (n >= 1_000) return `$${(n / 1_000).toFixed(1)}K`;
  return `$${n.toFixed(n < 1 ? 6 : 2)}`;
}

function Field({
  label,
  required,
  hint,
  children,
}: {
  label: string;
  required?: boolean;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <label className="block">
      <span className="text-xs font-medium text-[var(--color-muted)]">
        {label}
        {required && <span className="text-[var(--color-danger)]"> *</span>}
      </span>
      {hint && <span className="mt-0.5 block text-[10px] text-[var(--color-muted)]">{hint}</span>}
      <div className="mt-1.5">{children}</div>
    </label>
  );
}

function inputClass(missing?: boolean) {
  return `w-full rounded-lg border bg-[var(--color-background)] px-3 py-2 text-sm text-white outline-none focus:border-[var(--color-accent)] ${
    missing ? 'border-[var(--color-danger)]/70' : 'border-[var(--color-border)]'
  }`;
}

function LinkRow({ label, href }: { label: string; href: string | null | undefined }) {
  if (!href?.trim()) {
    return (
      <div className="flex flex-col gap-0.5 sm:flex-row sm:gap-3">
        <span className="shrink-0 text-xs text-[var(--color-muted)] sm:w-36">{label}</span>
        <span className="text-xs text-[var(--color-muted)]">Not provided</span>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-0.5 sm:flex-row sm:gap-3">
      <span className="shrink-0 text-xs text-[var(--color-muted)] sm:w-36">{label}</span>
      <a
        href={href}
        target="_blank"
        rel="noopener noreferrer"
        className="break-all text-xs text-[var(--color-accent)] hover:underline"
      >
        {href}
      </a>
    </div>
  );
}

export function applicationToReviewPayload(
  form: AdminApplicationUpdates,
): AdminApplicationUpdates {
  const payload: AdminApplicationUpdates = {};
  for (const [key, value] of Object.entries(form)) {
    if (value === undefined) continue;
    if (key === 'marketPreview') {
      payload.marketPreview = value as AdminApplicationUpdates['marketPreview'];
      continue;
    }
    payload[key as keyof AdminApplicationUpdates] = value as never;
  }
  return payload;
}

export function createReviewFormState(items: PendingApplication[]) {
  return Object.fromEntries(items.map((item) => [item.id, toFormState(item)]));
}

interface ApplicationReviewCardProps {
  item: PendingApplication;
  expanded: boolean;
  busy: boolean;
  reviewNotes: string;
  form: AdminApplicationUpdates;
  onToggle: () => void;
  onNotesChange: (value: string) => void;
  onFormChange: (updates: AdminApplicationUpdates) => void;
  onApprove: () => void;
  onReject: () => void;
}

export function ApplicationReviewCard({
  item,
  expanded,
  busy,
  reviewNotes,
  form,
  onToggle,
  onNotesChange,
  onFormChange,
  onApprove,
  onReject,
}: ApplicationReviewCardProps) {
  const liveVerification = scoreFounderVerification({
    founderName: form.founderName,
    founderLinkedIn: form.founderLinkedIn,
    founderGithub: form.founderGithub,
    companyDetails: form.companyDetails,
    founderVideoUrl: form.founderVideoUrl,
    founderInterviewUrl: form.founderInterviewUrl,
  });
  const eligible = liveVerification.meetsThreshold;
  const missingFounderName = !form.founderName?.trim();
  const missingChain = !form.chainSlug?.trim();
  const canApprove = eligible && !missingFounderName && !missingChain;

  function updateField<K extends keyof AdminApplicationUpdates>(
    key: K,
    value: AdminApplicationUpdates[K],
  ) {
    onFormChange({ ...form, [key]: value });
  }

  return (
    <div className="rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] p-5">
      <div className="flex flex-col gap-4 sm:flex-row">
        {item.logoUrl && (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={item.logoUrl} alt="" className="h-14 w-14 rounded-full" />
        )}
        <div className="flex-1">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <div className="flex flex-wrap items-center gap-3">
                <h2 className="text-lg font-semibold">
                  {item.projectName}{' '}
                  <span className="text-[var(--color-muted)]">({item.ticker})</span>
                </h2>
                <span
                  className={`rounded-full px-2.5 py-0.5 text-xs font-semibold ${
                    eligible
                      ? 'bg-emerald-950/50 text-[var(--color-success)]'
                      : 'bg-amber-950/40 text-amber-300'
                  }`}
                >
                  {liveVerification.score}/6 {eligible ? '· Eligible' : '· Insufficient'}
                </span>
              </div>
              <p className="mt-1 text-xs text-[var(--color-muted)]">
                Submitted {new Date(item.createdAt).toLocaleString()}
                {form.chainSlug ? ` · ${form.chainSlug}` : ' · Chain not set'}
              </p>
            </div>
            <button
              type="button"
              onClick={onToggle}
              className="rounded-lg border border-[var(--color-border)] px-3 py-1.5 text-sm text-[var(--color-muted)] hover:text-white"
            >
              {expanded ? 'Hide details' : 'Review full application'}
            </button>
          </div>

          <ul className="mt-3 flex flex-wrap gap-2">
            {liveVerification.criteria.map((c) => (
              <li
                key={c}
                className="rounded-md bg-[var(--color-background)] px-2 py-1 text-xs text-white"
              >
                {FOUNDER_VERIFICATION_LABELS[c]}
              </li>
            ))}
            {liveVerification.criteria.length === 0 && (
              <li className="text-xs text-[var(--color-muted)]">No verification criteria met yet</li>
            )}
          </ul>

          {!expanded && (
            <div className="mt-4 space-y-2 rounded-lg border border-[var(--color-border)]/60 bg-[var(--color-background)]/40 p-3">
              <LinkRow label="DexScreener" href={form.dexscreenerUrl} />
              <LinkRow label="Interview / video" href={form.founderInterviewUrl || form.founderVideoUrl} />
              <LinkRow label="Website" href={form.websiteUrl} />
              {(missingFounderName || missingChain) && (
                <p className="text-xs text-amber-300">
                  Expand to add missing fields before approve
                  {missingFounderName ? ' (founder name)' : ''}
                  {missingChain ? ' (chain)' : ''}.
                </p>
              )}
            </div>
          )}

          {expanded && (
            <div className="mt-5 space-y-6 border-t border-[var(--color-border)] pt-5">
              <section>
                <h3 className="text-sm font-semibold text-white">Submitted links (open to verify)</h3>
                <div className="mt-3 space-y-2 rounded-lg border border-[var(--color-border)]/60 bg-[var(--color-background)]/40 p-3">
                  <LinkRow label="DexScreener" href={form.dexscreenerUrl} />
                  <LinkRow label="Website" href={form.websiteUrl} />
                  <LinkRow label="Docs" href={form.docsUrl} />
                  <LinkRow label="Whitepaper" href={form.whitepaperUrl} />
                  <LinkRow label="Telegram" href={form.telegramUrl} />
                  <LinkRow label="Audit" href={form.auditUrl} />
                  <LinkRow label="Founder video" href={form.founderVideoUrl} />
                  <LinkRow label="Interview / talk" href={form.founderInterviewUrl} />
                  <LinkRow label="LinkedIn" href={form.founderLinkedIn} />
                  <LinkRow label="GitHub" href={form.founderGithub} />
                </div>
              </section>

              {form.marketPreview && (
                <section>
                  <h3 className="text-sm font-semibold text-white">Market snapshot</h3>
                  <div className="mt-2 grid grid-cols-2 gap-2 text-xs sm:grid-cols-4">
                    <div className="rounded-md bg-[var(--color-background)] px-3 py-2">
                      <div className="text-[var(--color-muted)]">Price</div>
                      <div>{formatUsd(form.marketPreview.priceUsd)}</div>
                    </div>
                    <div className="rounded-md bg-[var(--color-background)] px-3 py-2">
                      <div className="text-[var(--color-muted)]">Market cap</div>
                      <div>{formatUsd(form.marketPreview.marketCap)}</div>
                    </div>
                    <div className="rounded-md bg-[var(--color-background)] px-3 py-2">
                      <div className="text-[var(--color-muted)]">24h volume</div>
                      <div>{formatUsd(form.marketPreview.volume24h)}</div>
                    </div>
                    <div className="rounded-md bg-[var(--color-background)] px-3 py-2">
                      <div className="text-[var(--color-muted)]">Liquidity</div>
                      <div>{formatUsd(form.marketPreview.liquidityUsd)}</div>
                    </div>
                  </div>
                </section>
              )}

              <section>
                <h3 className="text-sm font-semibold text-white">Admin review — fill or correct fields</h3>
                <p className="mt-1 text-xs text-[var(--color-muted)]">
                  Research the project, then complete anything missing before approving.
                </p>
                <div className="mt-4 grid gap-4 sm:grid-cols-2">
                  <Field label="Project name">
                    <input
                      className={inputClass()}
                      value={form.projectName ?? ''}
                      onChange={(e) => updateField('projectName', e.target.value)}
                    />
                  </Field>
                  <Field label="Ticker">
                    <input
                      className={inputClass()}
                      value={form.ticker ?? ''}
                      onChange={(e) => updateField('ticker', e.target.value)}
                    />
                  </Field>
                  <Field label="Chain" required hint="Required to publish">
                    <select
                      className={inputClass(missingChain)}
                      value={form.chainSlug ?? ''}
                      onChange={(e) => updateField('chainSlug', e.target.value)}
                    >
                      <option value="">Select chain…</option>
                      {CHAIN_OPTIONS.map((chain) => (
                        <option key={chain} value={chain}>
                          {chain}
                        </option>
                      ))}
                    </select>
                  </Field>
                  <Field label="Contract address">
                    <input
                      className={inputClass()}
                      value={form.contractAddress ?? ''}
                      onChange={(e) => updateField('contractAddress', e.target.value)}
                    />
                  </Field>
                  <Field label="Founder name" required hint="Required to publish">
                    <input
                      className={inputClass(missingFounderName)}
                      value={form.founderName ?? ''}
                      onChange={(e) => updateField('founderName', e.target.value)}
                      placeholder="Full name from your research"
                    />
                  </Field>
                  <Field label="Founder Twitter / X">
                    <input
                      className={inputClass()}
                      value={form.founderTwitter ?? ''}
                      onChange={(e) => updateField('founderTwitter', e.target.value)}
                    />
                  </Field>
                  <Field label="DexScreener URL">
                    <input
                      className={inputClass()}
                      value={form.dexscreenerUrl ?? ''}
                      onChange={(e) => updateField('dexscreenerUrl', e.target.value)}
                    />
                  </Field>
                  <Field label="Website">
                    <input
                      className={inputClass()}
                      value={form.websiteUrl ?? ''}
                      onChange={(e) => updateField('websiteUrl', e.target.value)}
                    />
                  </Field>
                  <Field label="Logo URL">
                    <input
                      className={inputClass()}
                      value={form.logoUrl ?? ''}
                      onChange={(e) => updateField('logoUrl', e.target.value)}
                    />
                  </Field>
                  <Field label="Telegram">
                    <input
                      className={inputClass()}
                      value={form.telegramUrl ?? ''}
                      onChange={(e) => updateField('telegramUrl', e.target.value)}
                    />
                  </Field>
                  <Field label="Founder LinkedIn">
                    <input
                      className={inputClass()}
                      value={form.founderLinkedIn ?? ''}
                      onChange={(e) => updateField('founderLinkedIn', e.target.value)}
                    />
                  </Field>
                  <Field label="Founder GitHub">
                    <input
                      className={inputClass()}
                      value={form.founderGithub ?? ''}
                      onChange={(e) => updateField('founderGithub', e.target.value)}
                    />
                  </Field>
                  <Field label="Founder video URL">
                    <input
                      className={inputClass()}
                      value={form.founderVideoUrl ?? ''}
                      onChange={(e) => updateField('founderVideoUrl', e.target.value)}
                    />
                  </Field>
                  <Field label="Interview / podcast URL">
                    <input
                      className={inputClass()}
                      value={form.founderInterviewUrl ?? ''}
                      onChange={(e) => updateField('founderInterviewUrl', e.target.value)}
                    />
                  </Field>
                  <Field label="Docs URL">
                    <input
                      className={inputClass()}
                      value={form.docsUrl ?? ''}
                      onChange={(e) => updateField('docsUrl', e.target.value)}
                    />
                  </Field>
                  <Field label="Whitepaper URL">
                    <input
                      className={inputClass()}
                      value={form.whitepaperUrl ?? ''}
                      onChange={(e) => updateField('whitepaperUrl', e.target.value)}
                    />
                  </Field>
                  <Field label="Audit URL">
                    <input
                      className={inputClass()}
                      value={form.auditUrl ?? ''}
                      onChange={(e) => updateField('auditUrl', e.target.value)}
                    />
                  </Field>
                </div>
                <div className="mt-4 grid gap-4">
                  <Field label="Summary">
                    <textarea
                      className={`${inputClass()} min-h-[80px]`}
                      value={form.summary ?? ''}
                      onChange={(e) => updateField('summary', e.target.value)}
                    />
                  </Field>
                  <Field label="Company / team details">
                    <textarea
                      className={`${inputClass()} min-h-[80px]`}
                      value={form.companyDetails ?? ''}
                      onChange={(e) => updateField('companyDetails', e.target.value)}
                    />
                  </Field>
                  <Field label="Admin review notes (internal)">
                    <textarea
                      className={`${inputClass()} min-h-[60px]`}
                      value={reviewNotes}
                      onChange={(e) => onNotesChange(e.target.value)}
                      placeholder="Optional notes about your review decision"
                    />
                  </Field>
                </div>
              </section>

              {!canApprove && (
                <p className="text-sm text-amber-300">
                  Before approving: add a public video or interview link, founder name, and chain.
                </p>
              )}
            </div>
          )}

          <div className="mt-4 flex flex-wrap gap-2">
            <button
              type="button"
              disabled={busy || (expanded && !canApprove)}
              onClick={onApprove}
              className="rounded-lg bg-[var(--color-success)]/90 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
              title={
                expanded && !canApprove
                  ? 'Complete required fields before approving'
                  : undefined
              }
            >
              Approve & publish
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={onReject}
              className="rounded-lg border border-[var(--color-border)] px-4 py-2 text-sm text-[var(--color-muted)] hover:text-white disabled:opacity-50"
            >
              Reject
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

export { toFormState };
