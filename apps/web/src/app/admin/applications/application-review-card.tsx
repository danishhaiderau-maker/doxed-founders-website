'use client';

import type { ReactNode } from 'react';
import {
  VALIDATION_LABELS,
  founderStatusLabel,
  getPrimaryProofLink,
  tallyListingVotes,
  validateListingForApproval,
  type CommunityValidationCategory,
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

function approvalInput(item: PendingApplication, form: AdminApplicationUpdates) {
  return {
    dexscreenerUrl: form.dexscreenerUrl ?? item.dexscreenerUrl,
    founderDoxxedStatus: item.founderDoxxedStatus,
    founderVideoUrl: form.founderVideoUrl ?? item.founderVideoUrl,
    founderInterviewUrl: form.founderInterviewUrl ?? item.founderInterviewUrl,
    founderTwitter: form.founderTwitter ?? item.founderTwitter,
    founderLinkedIn: form.founderLinkedIn ?? item.founderLinkedIn,
    founderGithub: form.founderGithub ?? item.founderGithub,
    websiteUrl: form.websiteUrl ?? item.websiteUrl,
    chainSlug: form.chainSlug ?? item.chainSlug,
    contractAddress: form.contractAddress ?? item.contractAddress,
    founderName: form.founderName ?? item.founderName,
    projectName: form.projectName ?? item.projectName,
  };
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
  const input = approvalInput(item, form);
  const approval = validateListingForApproval(input);
  const proofLink = getPrimaryProofLink(input);
  const tally = tallyListingVotes(
    (item.votes ?? []).map((v) => ({
      vote: v.vote,
      weight: v.voteWeight ?? 1,
    })),
    item.requiredVoters ?? 3,
    item.minYesPercent ?? 70,
  );

  const categoryCounts = (item.votes ?? []).reduce<Record<string, number>>((acc, v) => {
    if (v.validationCategory) {
      acc[v.validationCategory] = (acc[v.validationCategory] ?? 0) + 1;
    }
    return acc;
  }, {});

  function updateField<K extends keyof AdminApplicationUpdates>(
    key: K,
    value: AdminApplicationUpdates[K],
  ) {
    onFormChange({ ...form, [key]: value });
  }

  function requestMoreProof() {
    onNotesChange(
      reviewNotes.trim() ||
        'Need stronger public proof — add a founder video, interview, verification page, or official team link before we can list.',
    );
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
              <div className="flex flex-wrap items-center gap-2">
                <h2 className="text-lg font-semibold">
                  {item.projectName}{' '}
                  <span className="text-[var(--color-muted)]">({item.ticker})</span>
                </h2>
                <span
                  className={`rounded-full px-2.5 py-0.5 text-xs font-semibold ${
                    approval.ok
                      ? 'bg-emerald-950/50 text-[var(--color-success)]'
                      : 'bg-amber-950/40 text-amber-300'
                  }`}
                >
                  {approval.ok ? 'Ready to approve' : 'Missing proof'}
                </span>
                <span
                  className={`rounded-full px-2.5 py-0.5 text-xs font-semibold ${
                    item.status === 'COMMUNITY_VOTING'
                      ? 'bg-blue-950/50 text-blue-300'
                      : 'bg-purple-950/50 text-purple-300'
                  }`}
                >
                  {item.status === 'COMMUNITY_VOTING' ? '48h community vote' : 'Admin queue'}
                </span>
              </div>
              <p className="mt-1 text-xs text-[var(--color-muted)]">
                Submitted {new Date(item.createdAt).toLocaleString()}
              </p>
            </div>
            <button
              type="button"
              onClick={onToggle}
              className="rounded-lg border border-[var(--color-border)] px-3 py-1.5 text-sm text-[var(--color-muted)] hover:text-white"
            >
              {expanded ? 'Hide enrichment' : 'Optional enrichment'}
            </button>
          </div>

          <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            <Info label="Contract" value={form.contractAddress || item.contractAddress || '—'} />
            <Info label="Market cap" value={formatUsd(form.marketPreview?.marketCap ?? item.marketPreview?.marketCap)} />
            <Info label="Founder status" value={founderStatusLabel(item.founderDoxxedStatus)} />
            <Info
              label="Community score"
              value={`${tally.yesPercent}% yes · ${tally.total}/${item.requiredVoters ?? 3} voters`}
            />
            <Info label="Trust signals" value={`${item.verificationScore}/6 verification`} />
            <Info label="Chain" value={form.chainSlug || item.chainSlug || 'Auto from DexScreener'} />
          </div>

          <div className="mt-4 space-y-2 rounded-lg border border-[var(--color-border)]/60 bg-[var(--color-background)]/40 p-3">
            <LinkRow label="DexScreener" href={form.dexscreenerUrl || item.dexscreenerUrl} required />
            <LinkRow label="Proof link" href={proofLink} required />
            {item.scoutHighlightNote && (
              <p className="text-xs text-zinc-400">
                <span className="text-zinc-500">Scout highlight:</span> {item.scoutHighlightNote}
              </p>
            )}
          </div>

          {!approval.ok && (
            <ul className="mt-3 list-disc space-y-1 pl-5 text-sm text-amber-300">
              {approval.errors.map((err) => (
                <li key={err}>{err}</li>
              ))}
            </ul>
          )}

          {approval.warnings.length > 0 && (
            <ul className="mt-2 list-disc space-y-1 pl-5 text-xs text-zinc-500">
              {approval.warnings.map((w) => (
                <li key={w}>{w}</li>
              ))}
            </ul>
          )}

          {(item.votes?.length ?? 0) > 0 && (
            <section className="mt-5 rounded-xl border border-emerald-500/20 bg-emerald-950/10 p-4">
              <h3 className="text-sm font-semibold text-emerald-200">Community validation (48h)</h3>
              <p className="mt-1 text-xs text-zinc-400">
                Weighted yes: {tally.yesPercent}% · Pass threshold: {item.minYesPercent ?? 70}% ·{' '}
                {tally.passed ? 'Vote passed' : 'Vote pending / did not pass'}
              </p>
              <div className="mt-3 flex flex-wrap gap-2">
                {Object.entries(categoryCounts).map(([cat, count]) => (
                  <span
                    key={cat}
                    className="rounded-md bg-black/30 px-2 py-1 text-[10px] text-white"
                  >
                    {VALIDATION_LABELS[cat as CommunityValidationCategory] ?? cat} · {count}
                  </span>
                ))}
              </div>
              <ul className="mt-3 space-y-2 border-t border-[var(--color-border)]/40 pt-3">
                {item.votes?.slice(0, 6).map((v) => (
                  <li key={v.id} className="rounded-lg bg-black/20 p-2 text-xs">
                    <span className={v.vote === 'YES' ? 'text-emerald-400' : 'text-red-400'}>
                      {v.validationCategory
                        ? VALIDATION_LABELS[v.validationCategory as CommunityValidationCategory]
                        : v.vote}
                    </span>{' '}
                    · {v.user.name ?? 'Trader'} · wt {v.voteWeight ?? 1}
                    {v.comment && <p className="mt-1 italic text-[var(--color-muted)]">{v.comment}</p>}
                  </li>
                ))}
              </ul>
            </section>
          )}

          {(item.whyList || item.whyDoxxed) && (
            <section className="mt-4 rounded-lg border border-zinc-800 bg-black/20 p-3 text-sm">
              {item.whyList && (
                <p>
                  <span className="text-xs uppercase text-zinc-500">Scout thesis</span>
                  <span className="mt-1 block whitespace-pre-wrap">{item.whyList}</span>
                </p>
              )}
              {item.whyDoxxed && (
                <p className="mt-2">
                  <span className="text-xs uppercase text-zinc-500">Proof narrative</span>
                  <span className="mt-1 block whitespace-pre-wrap">{item.whyDoxxed}</span>
                </p>
              )}
            </section>
          )}

          {expanded && (
            <div className="mt-5 space-y-4 border-t border-[var(--color-border)] pt-5">
              <p className="text-xs text-[var(--color-muted)]">
                Optional enrichment — auto-filled from DexScreener. Not required for approval.
              </p>
              <div className="grid gap-3 sm:grid-cols-2">
                <Field label="Chain (if not auto-detected)">
                  <select
                    className={inputClass()}
                    value={form.chainSlug ?? ''}
                    onChange={(e) => updateField('chainSlug', e.target.value)}
                  >
                    <option value="">Auto from DexScreener</option>
                    {CHAIN_OPTIONS.map((chain) => (
                      <option key={chain} value={chain}>
                        {chain}
                      </option>
                    ))}
                  </select>
                </Field>
                <Field label="Founder name (optional)">
                  <input
                    className={inputClass()}
                    value={form.founderName ?? ''}
                    onChange={(e) => updateField('founderName', e.target.value)}
                  />
                </Field>
                <Field label="Proof video URL">
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
                <Field label="Founder X / Twitter">
                  <input
                    className={inputClass()}
                    value={form.founderTwitter ?? ''}
                    onChange={(e) => updateField('founderTwitter', e.target.value)}
                  />
                </Field>
                <Field label="LinkedIn">
                  <input
                    className={inputClass()}
                    value={form.founderLinkedIn ?? ''}
                    onChange={(e) => updateField('founderLinkedIn', e.target.value)}
                  />
                </Field>
              </div>
              <Field label="Admin review notes">
                <textarea
                  className={`${inputClass()} min-h-[60px]`}
                  value={reviewNotes}
                  onChange={(e) => onNotesChange(e.target.value)}
                  placeholder="Internal notes — shown on reject / request more proof"
                />
              </Field>
            </div>
          )}

          <div className="mt-5 flex flex-wrap gap-2">
            <button
              type="button"
              disabled={busy || !approval.ok}
              onClick={onApprove}
              className="rounded-lg bg-[var(--color-success)]/90 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
              title={!approval.ok ? approval.errors.join(' ') : undefined}
            >
              Approve
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={onReject}
              className="rounded-lg border border-red-500/40 px-4 py-2 text-sm text-red-300 hover:bg-red-950/20 disabled:opacity-50"
            >
              Reject
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={requestMoreProof}
              className="rounded-lg border border-amber-500/40 px-4 py-2 text-sm text-amber-200 hover:bg-amber-950/20 disabled:opacity-50"
            >
              Request more proof
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

function Info({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg bg-[var(--color-background)]/50 px-3 py-2">
      <p className="text-[10px] uppercase tracking-wider text-[var(--color-muted)]">{label}</p>
      <p className="mt-0.5 text-sm font-medium text-white">{value}</p>
    </div>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="block text-xs font-medium text-[var(--color-muted)]">
      {label}
      <div className="mt-1.5">{children}</div>
    </label>
  );
}

function inputClass() {
  return 'w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-background)] px-3 py-2 text-sm text-white outline-none focus:border-[var(--color-accent)]';
}

function LinkRow({
  label,
  href,
  required,
}: {
  label: string;
  href: string | null | undefined;
  required?: boolean;
}) {
  return (
    <div className="flex flex-col gap-0.5 sm:flex-row sm:gap-3">
      <span className="shrink-0 text-xs text-[var(--color-muted)] sm:w-28">
        {label}
        {required && <span className="text-red-400"> *</span>}
      </span>
      {href?.trim() ? (
        <a
          href={href}
          target="_blank"
          rel="noopener noreferrer"
          className="break-all text-xs text-[var(--color-accent)] hover:underline"
        >
          {href}
        </a>
      ) : (
        <span className="text-xs text-amber-300">Missing</span>
      )}
    </div>
  );
}

export { toFormState };
