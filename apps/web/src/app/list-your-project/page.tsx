'use client';

import Link from 'next/link';
import { FormEvent, useMemo, useState, useEffect, Suspense } from 'react';
import { useSearchParams } from 'next/navigation';
import { useSession } from 'next-auth/react';
import { FounderVerificationChecklist } from '@/components/founder-verification-checklist';
import { GeckoTerminalChart } from '@/components/gecko-terminal-chart';
import { extractPoolAddressFromDexUrl } from '@dcf/utils';
import {
  DexScreenerPreview,
  ListingFormData,
  previewContract,
  previewDexScreener,
  submitListingApplication,
  fetchProject,
} from '@/lib/api';
import { scoreFounderVerification } from '@dcf/utils';

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
  whyList: '',
  whyDoxxed: '',
  founderDoxxedStatus: 'DOXXED',
  scoutHighlightNote: '',
};

function ListYourProjectPageInner() {
  const { data: session } = useSession();
  const searchParams = useSearchParams();
  const editSlug = searchParams.get('edit');
  const [dexUrl, setDexUrl] = useState('');
  const [contractInput, setContractInput] = useState('');
  const [contractChain, setContractChain] = useState<string>('SOLANA');
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

  useEffect(() => {
    if (!editSlug) return;
    fetchProject(editSlug)
      .then((p) => {
        setForm((prev) => ({
          ...prev,
          projectName: p.name,
          ticker: p.ticker,
          websiteUrl: p.websiteUrl ?? '',
          docsUrl: p.docsUrl ?? '',
          contractAddress: p.contractAddress ?? '',
          chainSlug: p.chain.slug,
          dexscreenerUrl: p.dexscreenerUrl ?? '',
          logoUrl: p.logoUrl ?? '',
          summary: p.summary ?? '',
          founderName: p.verificationDossier?.founderName ?? '',
          founderLinkedIn: p.verificationDossier?.founderLinkedIn ?? '',
          founderTwitter: p.verificationDossier?.founderTwitter ?? '',
          founderGithub: p.verificationDossier?.founderGithub ?? '',
          founderVideoUrl: p.verificationDossier?.founderVideoUrl ?? '',
          founderInterviewUrl: p.verificationDossier?.founderInterviewUrl ?? '',
          companyDetails: p.verificationDossier?.companyDetails ?? '',
          whyList: p.verificationDossier?.whyList ?? '',
          whyDoxxed: p.verificationDossier?.whyDoxxed ?? '',
        }));
        if (p.dexscreenerUrl) setDexUrl(p.dexscreenerUrl);
      })
      .catch(() => {});
  }, [editSlug]);

  async function handleAutoFill() {
    setError(null);
    setLoading(true);
    try {
      const preview = await previewDexScreener(dexUrl);
      applyPreview(preview);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Auto-fill failed');
    } finally {
      setLoading(false);
    }
  }

  async function handleContractAutoFill() {
    setError(null);
    setLoading(true);
    try {
      const preview = await previewContract(contractChain, contractInput);
      applyPreview(preview);
    } catch (err) {
      setError(
        `${err instanceof Error ? err.message : 'Contract lookup failed'} — you can still fill the form manually and submit.`,
      );
    } finally {
      setLoading(false);
    }
  }

  function applyPreview(preview: DexScreenerPreview) {
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
    setDexUrl(preview.dexscreenerUrl);
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);

    const verification = scoreFounderVerification(form);
    if (!verification.meetsSubmissionThreshold) {
      setError(
        'Add a public founder video or interview/podcast URL. You do not need to be the founder — if you found proof on X or YouTube, that is enough.',
      );
      return;
    }

    if (!session?.accessToken) {
      setError('Sign in first so scout points link to your account.');
      return;
    }

    if (!form.whyList?.trim()) {
      setError('Explain why this project should be listed. This goes on the public scout vote board.');
      return;
    }
    if (form.founderDoxxedStatus !== 'BUILDING_IN_PUBLIC' && !form.whyDoxxed?.trim()) {
      setError('Explain why the founder is doxxed, or select "Building in public" and add a scout highlight.');
      return;
    }
    if (form.founderDoxxedStatus === 'BUILDING_IN_PUBLIC' && !form.scoutHighlightNote?.trim()) {
      setError('Add a scout highlight for non-doxxed founders building in public.');
      return;
    }

    setSubmitting(true);
    try {
      const payload = cleanListingPayload({
        ...form,
        chainSlug: form.chainSlug || undefined,
        marketPreview: marketPreview ?? undefined,
      });
      const result = await submitListingApplication(payload, session?.accessToken);
      setSuccessId(result.id);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Submission failed');
    } finally {
      setSubmitting(false);
    }
  }

  function cleanListingPayload(data: ListingFormData): ListingFormData {
    const out: ListingFormData = { ...data };
    for (const key of Object.keys(out) as (keyof ListingFormData)[]) {
      const value = out[key];
      if (value === '' || value === null || value === undefined) {
        delete out[key];
      }
    }
    return out;
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
          <h1 className="mt-4 text-2xl font-semibold">Scout listing submitted</h1>
          <p className="mt-3 text-[var(--color-muted)]">
            Your project is on the scout vote board for <strong className="text-white">48 hours</strong>.
            Traders can vote and comment; admin can fast-track approve anytime, or review after 48h.
            {session ? ' You earned +50 scout submit points.' : ' Sign in next time to earn scout points.'}
          </p>
          <Link
            href="/scout-votes"
            className="mt-6 inline-block rounded-lg border border-emerald-500/50 px-6 py-3 text-sm font-medium text-emerald-200"
          >
            View scout vote board
          </Link>
          <Link
            href="/reputation"
            className="mt-4 ml-4 inline-block text-sm text-[var(--color-muted)] hover:text-white"
          >
            Points math →
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
            ← Doxxed crypto
          </Link>
          <span className="text-sm text-[var(--color-muted)]">Listing application</span>
        </div>
      </header>

      <div className="mx-auto max-w-5xl px-6 py-12">
        <h1 className="text-3xl font-bold tracking-tight">List your project</h1>
        <p className="mt-3 max-w-2xl text-[var(--color-muted)]">
          Anyone can suggest a project — you do not need to be the founder. Found a public
          video or podcast? Paste it below with basic project info. DexScreener or contract
          lookup is optional (helps fill Telegram, Twitter, prices).
        </p>

        <div className="mt-8 space-y-6">
        <div className="rounded-xl border border-[var(--color-accent)]/30 bg-[var(--color-card)] p-6">
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
        </div>

        <div className="rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] p-6">
          <label className="text-sm font-medium">Or contract address (auto-fill)</label>
          <p className="mt-1 text-xs text-[var(--color-muted)]">
            Looks up the token on DexScreener — best for Telegram, Twitter, logo, and price when listed.
          </p>
          <div className="mt-3 flex flex-col gap-3 sm:flex-row">
            <select
              value={contractChain}
              onChange={(e) => setContractChain(e.target.value)}
              className="rounded-lg border border-[var(--color-border)] bg-[var(--color-background)] px-4 py-2.5 text-sm outline-none focus:border-[var(--color-accent)] sm:w-44"
            >
              {CHAIN_OPTIONS.map((chain) => (
                <option key={chain} value={chain}>
                  {chain}
                </option>
              ))}
            </select>
            <input
              type="text"
              value={contractInput}
              onChange={(e) => setContractInput(e.target.value)}
              placeholder="Token contract address"
              className="flex-1 rounded-lg border border-[var(--color-border)] bg-[var(--color-background)] px-4 py-2.5 text-sm outline-none focus:border-[var(--color-accent)]"
            />
            <button
              type="button"
              onClick={handleContractAutoFill}
              disabled={loading || !contractInput.trim()}
              className="rounded-lg border border-[var(--color-accent)] px-5 py-2.5 text-sm font-medium text-white hover:bg-[var(--color-accent)]/10 disabled:opacity-50"
            >
              {loading ? 'Fetching…' : 'Look up contract'}
            </button>
          </div>
        </div>
        </div>

        {(form.dexscreenerUrl || marketPreview) && (
        <div className="mt-6 rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] p-6">
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
                Live chart
              </p>
              <GeckoTerminalChart
                chainSlug={form.chainSlug}
                poolAddress={poolAddress}
                dexscreenerUrl={form.dexscreenerUrl ?? dexUrl}
                height={360}
              />
            </div>
          )}
        </div>
        )}

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

          <Section title="Founder proof (community submissions welcome)">
            <FounderVerificationChecklist input={form} />
            <div className="mt-4 space-y-4">
              <Field label="Founder video URL (on camera)" value={form.founderVideoUrl ?? ''} onChange={(v) => updateField('founderVideoUrl', v)} placeholder="YouTube, Loom, X video… — required if no interview below" />
              <Field label="Public interview / podcast URL" value={form.founderInterviewUrl ?? ''} onChange={(v) => updateField('founderInterviewUrl', v)} placeholder="Twitter Spaces, podcast, conference talk… — required if no video above" />
              <Field label="Founder name (optional — admin can add later)" value={form.founderName ?? ''} onChange={(v) => updateField('founderName', v)} />
              <Field label="LinkedIn (optional)" value={form.founderLinkedIn ?? ''} onChange={(v) => updateField('founderLinkedIn', v)} />
              <Field label="Twitter / X (optional)" value={form.founderTwitter ?? ''} onChange={(v) => updateField('founderTwitter', v)} />
              <Field label="GitHub (optional)" value={form.founderGithub ?? ''} onChange={(v) => updateField('founderGithub', v)} />
              <Field label="Company details (optional)" value={form.companyDetails ?? ''} onChange={(v) => updateField('companyDetails', v)} multiline placeholder="Legal entity, team size, location…" />
              <Field label="Audit report URL (optional)" value={form.auditUrl ?? ''} onChange={(v) => updateField('auditUrl', v)} />
            </div>
          </Section>

          <Section title="Scout thesis (public on vote board)">
            <Field
              label="Why should this project be listed?"
              value={form.whyList ?? ''}
              onChange={(v) => updateField('whyList', v)}
              multiline
              required
              placeholder="Product, traction, why the community should care…"
            />
            <div className="mt-4 space-y-3 rounded-lg border border-violet-500/25 bg-violet-950/10 p-4">
              <p className="text-sm font-medium text-violet-100">Founder verification status</p>
              <label className="flex cursor-pointer items-start gap-2 text-sm">
                <input
                  type="radio"
                  name="doxxedStatus"
                  checked={form.founderDoxxedStatus !== 'BUILDING_IN_PUBLIC'}
                  onChange={() => updateField('founderDoxxedStatus', 'DOXXED')}
                  className="mt-1"
                />
                <span>
                  <strong className="text-white">Doxxed / verified founder</strong>
                  <span className="block text-xs text-zinc-500">Public video, interview, LinkedIn, or identity proof</span>
                </span>
              </label>
              <label className="flex cursor-pointer items-start gap-2 text-sm">
                <input
                  type="radio"
                  name="doxxedStatus"
                  checked={form.founderDoxxedStatus === 'BUILDING_IN_PUBLIC'}
                  onChange={() => updateField('founderDoxxedStatus', 'BUILDING_IN_PUBLIC')}
                  className="mt-1"
                />
                <span>
                  <strong className="text-white">Not fully doxxed — building in public</strong>
                  <span className="block text-xs text-zinc-500">Highlight GitHub, podcasts, X activity instead</span>
                </span>
              </label>
            </div>
            {form.founderDoxxedStatus === 'BUILDING_IN_PUBLIC' ? (
              <Field
                label="Scout highlight (shown on project cards)"
                value={form.scoutHighlightNote ?? ''}
                onChange={(v) => updateField('scoutHighlightNote', v)}
                multiline
                required
                placeholder="e.g. Shipping weekly on GitHub, active on X Spaces, podcast guest on…"
              />
            ) : (
              <Field
                label="Why is the founder doxxed / verified?"
                value={form.whyDoxxed ?? ''}
                onChange={(v) => updateField('whyDoxxed', v)}
                multiline
                required
                placeholder="Video link, interview, LinkedIn, public identity proof you found…"
              />
            )}
          </Section>

          <p className="text-xs text-[var(--color-muted)]">
            Free listing during beta. After submit, traders vote for <strong className="text-white">48 hours</strong> on the{' '}
            <Link href="/scout-votes" className="text-emerald-400 hover:underline">
              scout board
            </Link>
            . Passed listings queue sooner; after 48h all listings land in admin inbox either way.
            {!session && ' Sign in to earn scout points (+50 submit, +1,000 if approved).'}
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

export default function ListYourProjectPage() {
  return (
    <Suspense fallback={<div className="min-h-screen bg-[#050508] p-8 text-[var(--color-muted)]">Loading…</div>}>
      <ListYourProjectPageInner />
    </Suspense>
  );
}
