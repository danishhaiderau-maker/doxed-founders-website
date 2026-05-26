'use client';

import Link from 'next/link';
import { FormEvent, useMemo, useState } from 'react';
import { FounderVerificationChecklist } from '@/components/founder-verification-checklist';
import { GeckoTerminalChart } from '@/components/gecko-terminal-chart';
import { extractPoolAddressFromDexUrl } from '@dcf/utils';
import {
  DexScreenerPreview,
  ListingFormData,
  previewDexScreener,
  submitListingApplication,
} from '@/lib/api';

const emptyForm: ListingFormData = {
  projectName: '',
  ticker: '',
  websiteUrl: '',
  docsUrl: '',
  contractAddress: '',
  chainSlug: '',
  dexscreenerUrl: '',
  logoUrl: '',
  telegramUrl: '',
  founderName: '',
  founderLinkedIn: '',
  founderTwitter: '',
  founderGithub: '',
  founderVideoUrl: '',
  founderInterviewUrl: '',
  companyDetails: '',
  auditUrl: '',
  summary: '',
};

export default function ListYourProjectPage() {
  const [dexUrl, setDexUrl] = useState('');
  const [form, setForm] = useState<ListingFormData>(emptyForm);
  const [marketPreview, setMarketPreview] = useState<
    DexScreenerPreview['marketPreview'] | null
  >(null);
  const [loading, setLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [successId, setSuccessId] = useState<string | null>(null);

  const poolAddress = useMemo(
    () => extractPoolAddressFromDexUrl(form.dexscreenerUrl ?? dexUrl),
    [form.dexscreenerUrl, dexUrl],
  );

  async function handleAutoFill() {
    setError(null);
    setLoading(true);
    try {
      const preview = await previewDexScreener(dexUrl);
      setForm({
        ...form,
        projectName: preview.projectName,
        ticker: preview.ticker,
        websiteUrl: preview.websiteUrl ?? '',
        telegramUrl: preview.telegramUrl ?? '',
        founderTwitter: preview.founderTwitter ?? '',
        contractAddress: preview.contractAddress,
        chainSlug: preview.chainSlug ?? '',
        dexscreenerUrl: preview.dexscreenerUrl,
        logoUrl: preview.logoUrl ?? '',
        summary: preview.summary ?? '',
        marketPreview: preview.marketPreview,
      });
      setMarketPreview(preview.marketPreview);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Auto-fill failed');
    } finally {
      setLoading(false);
    }
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      const payload: ListingFormData = {
        ...form,
        chainSlug: form.chainSlug || undefined,
        marketPreview: marketPreview ?? undefined,
      };
      const result = await submitListingApplication(payload);
      setSuccessId(result.id);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Submission failed');
    } finally {
      setSubmitting(false);
    }
  }

  function updateField(key: keyof ListingFormData, value: string) {
    setForm((prev) => ({ ...prev, [key]: value }));
  }

  if (successId) {
    return (
      <main className="min-h-screen px-6 py-16">
        <div className="mx-auto max-w-lg rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] p-8 text-center">
          <p className="text-sm uppercase tracking-widest text-[var(--color-success)]">
            Request submitted
          </p>
          <h1 className="mt-4 text-2xl font-semibold">We received your listing</h1>
          <p className="mt-3 text-[var(--color-muted)]">
            Our team will review founder verification (2+ proof points required).
            Reference: {successId.slice(0, 8)}…
          </p>
          <Link
            href="/"
            className="mt-8 inline-block rounded-lg bg-[var(--color-accent)] px-6 py-3 text-sm font-medium text-white"
          >
            Back to home
          </Link>
        </div>
      </main>
    );
  }

  return (
    <main className="min-h-screen">
      <header className="border-b border-[var(--color-border)]">
        <div className="mx-auto flex max-w-5xl items-center justify-between px-6 py-5">
          <Link href="/" className="text-sm text-[var(--color-muted)] hover:text-white">
            ← DoxedCryptoFounder
          </Link>
          <span className="text-sm text-[var(--color-muted)]">Listing application</span>
        </div>
      </header>

      <div className="mx-auto max-w-5xl px-6 py-12">
        <h1 className="text-3xl font-bold tracking-tight">List your project</h1>
        <p className="mt-3 max-w-2xl text-[var(--color-muted)]">
          Curated listings require a public, doxxed founder with proof — video interviews,
          LinkedIn, GitHub, or company details. Paste DexScreener to auto-fill market data.
        </p>

        <div className="mt-8 rounded-xl border border-[var(--color-accent)]/30 bg-[var(--color-card)] p-6">
          <label className="text-sm font-medium">DexScreener link (auto-fill)</label>
          <div className="mt-2 flex flex-col gap-3 sm:flex-row">
            <input
              type="url"
              value={dexUrl}
              onChange={(e) => setDexUrl(e.target.value)}
              placeholder="https://dexscreener.com/solana/..."
              className="flex-1 rounded-lg border border-[var(--color-border)] bg-[var(--color-background)] px-4 py-2.5 text-sm outline-none focus:border-[var(--color-accent)]"
            />
            <button
              type="button"
              onClick={handleAutoFill}
              disabled={loading || !dexUrl.trim()}
              className="rounded-lg bg-[var(--color-accent)] px-5 py-2.5 text-sm font-medium text-white disabled:opacity-50"
            >
              {loading ? 'Fetching…' : 'Auto-fill from DexScreener'}
            </button>
          </div>
          {form.dexscreenerUrl && (
            <div className="mt-3 flex flex-wrap gap-3 text-sm">
              <a
                href={form.dexscreenerUrl}
                target="_blank"
                rel="noreferrer"
                className="text-[var(--color-accent)] hover:underline"
              >
                View on DexScreener ↗
              </a>
            </div>
          )}
          {form.logoUrl && (
            <div className="mt-4 flex items-center gap-3">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src={form.logoUrl} alt="" className="h-10 w-10 rounded-full" />
              <span className="text-sm text-[var(--color-muted)]">
                Logo pulled from DexScreener
              </span>
            </div>
          )}
          {marketPreview && (
            <div className="mt-4 grid grid-cols-2 gap-2 text-xs sm:grid-cols-4">
              {marketPreview.priceUsd && (
                <Stat label="Price" value={`$${marketPreview.priceUsd}`} />
              )}
              {marketPreview.marketCap != null && (
                <Stat label="Market cap" value={`$${marketPreview.marketCap.toLocaleString()}`} />
              )}
              {marketPreview.volume24h != null && (
                <Stat label="24h vol" value={`$${marketPreview.volume24h.toLocaleString()}`} />
              )}
              {marketPreview.liquidityUsd != null && (
                <Stat label="Liquidity" value={`$${marketPreview.liquidityUsd.toLocaleString()}`} />
              )}
            </div>
          )}
          {poolAddress && (
            <div className="mt-6">
              <p className="mb-2 text-sm font-medium text-[var(--color-muted)]">
                Live chart (GeckoTerminal)
              </p>
              <GeckoTerminalChart
                chainSlug={form.chainSlug}
                poolAddress={poolAddress}
                height={360}
              />
            </div>
          )}
        </div>

        {error && (
          <p className="mt-4 rounded-lg border border-[var(--color-danger)]/40 bg-red-950/30 px-4 py-3 text-sm text-red-300">
            {error}
          </p>
        )}

        <form onSubmit={handleSubmit} className="mt-8 space-y-6">
          <Section title="Project">
            <Field label="Project name" value={form.projectName} onChange={(v) => updateField('projectName', v)} required />
            <Field label="Ticker" value={form.ticker} onChange={(v) => updateField('ticker', v)} required />
            <Field label="Website" value={form.websiteUrl ?? ''} onChange={(v) => updateField('websiteUrl', v)} />
            <Field label="Docs URL" value={form.docsUrl ?? ''} onChange={(v) => updateField('docsUrl', v)} />
            <Field label="Contract address" value={form.contractAddress ?? ''} onChange={(v) => updateField('contractAddress', v)} />
            <Field label="Chain" value={form.chainSlug ?? ''} onChange={(v) => updateField('chainSlug', v)} placeholder="SOLANA, ETHEREUM, …" />
            <Field label="Telegram" value={form.telegramUrl ?? ''} onChange={(v) => updateField('telegramUrl', v)} />
            <Field label="Summary" value={form.summary ?? ''} onChange={(v) => updateField('summary', v)} multiline />
          </Section>

          <Section title="Founder (public / doxxed)">
            <FounderVerificationChecklist input={form} />
            <div className="mt-4 space-y-4">
              <Field label="Founder name" value={form.founderName ?? ''} onChange={(v) => updateField('founderName', v)} required />
              <Field label="Founder video URL (on camera)" value={form.founderVideoUrl ?? ''} onChange={(v) => updateField('founderVideoUrl', v)} placeholder="YouTube, Loom, X video…" />
              <Field label="Public interview / talk URL" value={form.founderInterviewUrl ?? ''} onChange={(v) => updateField('founderInterviewUrl', v)} placeholder="Twitter Spaces, podcast, conference talk…" />
              <Field label="LinkedIn" value={form.founderLinkedIn ?? ''} onChange={(v) => updateField('founderLinkedIn', v)} />
              <Field label="Twitter / X" value={form.founderTwitter ?? ''} onChange={(v) => updateField('founderTwitter', v)} />
              <Field label="GitHub" value={form.founderGithub ?? ''} onChange={(v) => updateField('founderGithub', v)} />
              <Field label="Company details" value={form.companyDetails ?? ''} onChange={(v) => updateField('companyDetails', v)} multiline placeholder="Legal entity, team size, location, registration…" />
              <Field label="Audit report URL" value={form.auditUrl ?? ''} onChange={(v) => updateField('auditUrl', v)} />
            </div>
          </Section>

          <p className="text-xs text-[var(--color-muted)]">
            Free listing during beta. Approval requires 2+ verification criteria. Paid featured
            listings may be offered later.
          </p>

          <button
            type="submit"
            disabled={submitting}
            className="w-full rounded-lg bg-[var(--color-accent)] py-3 text-sm font-medium text-white disabled:opacity-50 sm:w-auto sm:px-10"
          >
            {submitting ? 'Submitting…' : 'Submit listing request'}
          </button>
        </form>
      </div>
    </main>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] p-6">
      <h2 className="text-lg font-semibold">{title}</h2>
      <div className="mt-4">{children}</div>
    </div>
  );
}

function Field({
  label,
  value,
  onChange,
  required,
  multiline,
  placeholder,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  required?: boolean;
  multiline?: boolean;
  placeholder?: string;
}) {
  const cls =
    'w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-background)] px-4 py-2.5 text-sm outline-none focus:border-[var(--color-accent)]';
  return (
    <label className="block text-sm">
      <span className="font-medium text-[var(--color-muted)]">{label}</span>
      {multiline ? (
        <textarea
          value={value}
          onChange={(e) => onChange(e.target.value)}
          required={required}
          rows={3}
          placeholder={placeholder}
          className={`${cls} mt-1.5 resize-y`}
        />
      ) : (
        <input
          type="text"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          required={required}
          placeholder={placeholder}
          className={`${cls} mt-1.5`}
        />
      )}
    </label>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg bg-[var(--color-background)] px-3 py-2">
      <p className="text-[var(--color-muted)]">{label}</p>
      <p className="font-medium">{value}</p>
    </div>
  );
}
