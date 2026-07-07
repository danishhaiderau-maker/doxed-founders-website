'use client';

import { useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import { apiUrl } from '@/lib/api-base';

type DashboardData = {
  founder?: {
    id: string;
    tier?: string;
    contributorLevel?: string;
    reputationPoints?: number;
    lifetimeContributionEarned?: number;
  } | null;
  user?: {
    reputationPoints?: number;
    contributorLevel?: string;
  };
  connectedApps?: Array<{ provider: string; label: string; connectedAt?: string | null }>;
};

type Props = { accessToken: string };

export function FounderOsShell({ accessToken }: Props) {
  const [dashboard, setDashboard] = useState<DashboardData | null>(null);
  const [tier, setTier] = useState<'VISITOR' | 'DOXXED' | 'UNKNOWN'>('UNKNOWN');
  const [ddollar, setDdollar] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await fetch(apiUrl('/api/founder-os/dashboard'), {
        headers: { Authorization: `Bearer ${accessToken}` },
      });
      if (res.ok) {
        const data = (await res.json()) as DashboardData;
        setDashboard(data);
        const points =
          data.founder?.reputationPoints ?? data.user?.reputationPoints ?? 0;
        setDdollar(points);
        const level =
          data.founder?.tier ?? data.user?.contributorLevel ?? 'PARASITE';
        const normalized = String(level).toUpperCase();
        setTier(
          normalized === 'VERIFIED_BUILDER' || normalized === 'DOXXED'
            ? 'DOXXED'
            : 'VISITOR',
        );
      }
    } catch {
      // surfaced by empty state below
    } finally {
      setLoading(false);
    }
  }, [accessToken]);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <div className="space-y-8">
      <StatusBar tier={tier} ddollar={ddollar} loading={loading} />

      <CtaCards tier={tier} accessToken={accessToken} />

      <QuickLinksGrid />

      <RecentActivityStrip dashboard={dashboard} />
    </div>
  );
}

function StatusBar({
  tier,
  ddollar,
  loading,
}: {
  tier: 'VISITOR' | 'DOXXED' | 'UNKNOWN';
  ddollar: number | null;
  loading: boolean;
}) {
  const tierLabel =
    tier === 'DOXXED' ? 'Doxxed Builder' : tier === 'VISITOR' ? 'Visitor' : '—';
  const tierAccent =
    tier === 'DOXXED' ? 'text-emerald-300' : 'text-amber-300';
  return (
    <div className="flex flex-wrap items-center gap-4 rounded-xl border border-zinc-800 bg-zinc-950/40 px-5 py-4">
      <div>
        <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-zinc-500">
          Status
        </p>
        <p className={`mt-1 text-lg font-bold ${tierAccent}`}>
          {loading ? '…' : tierLabel}
        </p>
      </div>
      <div className="h-10 w-px bg-zinc-800" />
      <div>
        <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-zinc-500">
          DDollar balance
        </p>
        <p className="mt-1 text-lg font-bold text-white">
          {loading || ddollar == null
            ? '…'
            : `${ddollar.toLocaleString()} DD`}
        </p>
      </div>
      <div className="h-10 w-px bg-zinc-800" />
      <div className="flex-1 text-xs text-zinc-500">
        DoxxedCrypto = trust layer. Verify once → unlock unlimited AI + launch
        rights.
      </div>
    </div>
  );
}

function CtaCards({
  tier,
  accessToken,
}: {
  tier: 'VISITOR' | 'DOXXED' | 'UNKNOWN';
  accessToken: string;
}) {
  return (
    <div className="grid gap-4 md:grid-cols-2">
      {tier !== 'DOXXED' ? (
        <GetDoxxedCard accessToken={accessToken} />
      ) : (
        <AlreadyDoxxedCard />
      )}
      <LaunchTokenCard tier={tier} />
    </div>
  );
}

function GetDoxxedCard({ accessToken }: { accessToken: string }) {
  const [form, setForm] = useState({
    githubUrl: '',
    twitterHandle: '',
    videoUrl: '',
    ideaDescription: '',
    projectName: '',
  });
  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState<
    { ok: true; message: string } | { ok: false; error: string } | null
  >(null);

  async function submit() {
    if (!form.githubUrl || !form.videoUrl || !form.ideaDescription) {
      setResult({
        ok: false,
        error: 'GitHub, founder video, and idea description are required.',
      });
      return;
    }
    setSubmitting(true);
    setResult(null);
    try {
      const res = await fetch(apiUrl('/api/founder-applications'), {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${accessToken}`,
        },
        body: JSON.stringify({
          ...form,
          lifecycleStage: 'IDEA',
        }),
      });
      if (res.ok) {
        setResult({
          ok: true,
          message:
            'Application submitted. The team will personally review your video and respond within 48h.',
        });
      } else {
        const body = (await res.json().catch(() => ({}))) as {
          message?: string;
        };
        setResult({
          ok: false,
          error: body.message ?? `Submission failed (${res.status})`,
        });
      }
    } catch (err) {
      setResult({
        ok: false,
        error: err instanceof Error ? err.message : String(err),
      });
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="rounded-xl border border-emerald-500/30 bg-emerald-950/15 p-5">
      <h3 className="text-base font-bold text-emerald-200">Get Doxxed</h3>
      <p className="mt-1 text-xs text-zinc-400">
        Verify once → unlock unlimited AI, DDollar earning, and project launch
        rights. No KYC. Just a 60-second video.
      </p>

      <div className="mt-4 space-y-3">
        <Field
          label="GitHub URL"
          value={form.githubUrl}
          onChange={(v) => setForm({ ...form, githubUrl: v })}
          placeholder="https://github.com/your-handle"
        />
        <Field
          label="Project / company name"
          value={form.projectName}
          onChange={(v) => setForm({ ...form, projectName: v })}
          placeholder="What are you building?"
        />
        <Field
          label="Twitter handle"
          value={form.twitterHandle}
          onChange={(v) => setForm({ ...form, twitterHandle: v })}
          placeholder="@your-handle"
        />
        <Field
          label="Founder video URL (60-90 sec)"
          value={form.videoUrl}
          onChange={(v) => setForm({ ...form, videoUrl: v })}
          placeholder="https://… (YouTube / Loom / mp4)"
        />
        <TextArea
          label="What are you building?"
          value={form.ideaDescription}
          onChange={(v) => setForm({ ...form, ideaDescription: v })}
          placeholder="One paragraph — the problem, the product, why you."
        />
      </div>

      <button
        type="button"
        disabled={submitting}
        onClick={submit}
        className="mt-4 w-full rounded-lg bg-emerald-500 px-4 py-2 text-sm font-semibold text-black hover:bg-emerald-400 disabled:opacity-50"
      >
        {submitting ? 'Submitting…' : 'Submit for review'}
      </button>

      {result?.ok && (
        <p className="mt-3 rounded-lg border border-emerald-700 bg-emerald-900/30 p-3 text-xs text-emerald-200">
          {result.message}
        </p>
      )}
      {result && !result.ok && (
        <p className="mt-3 rounded-lg border border-rose-700 bg-rose-900/30 p-3 text-xs text-rose-200">
          {result.error}
        </p>
      )}
    </div>
  );
}

function AlreadyDoxxedCard() {
  return (
    <div className="rounded-xl border border-emerald-500/40 bg-emerald-950/20 p-5">
      <h3 className="text-base font-bold text-emerald-200">
        ✅ Doxxed Builder
      </h3>
      <p className="mt-1 text-xs text-zinc-400">
        You have unlimited AI, full DDollar earning, and launch rights. Keep
        building in public.
      </p>
      <div className="mt-4 grid grid-cols-2 gap-3 text-xs">
        <Link
          href="/founder-den"
          className="rounded-lg border border-zinc-700 px-3 py-2 text-center text-zinc-200 hover:border-zinc-500 hover:text-white"
        >
          Founder Den →
        </Link>
        <Link
          href="/settings/ai-usage"
          className="rounded-lg border border-zinc-700 px-3 py-2 text-center text-zinc-200 hover:border-zinc-500 hover:text-white"
        >
          AI Usage →
        </Link>
      </div>
    </div>
  );
}

function LaunchTokenCard({ tier }: { tier: 'VISITOR' | 'DOXXED' | 'UNKNOWN' }) {
  const [waitlistEmail, setWaitlistEmail] = useState('');
  const [joined, setJoined] = useState(false);

  const canLaunch = tier === 'DOXXED';

  return (
    <div className="rounded-xl border border-violet-500/30 bg-violet-950/15 p-5">
      <h3 className="text-base font-bold text-violet-200">
        I&apos;m Ready — Launch My Token
      </h3>
      <p className="mt-1 text-xs text-zinc-400">
        Spin up a token on Solana, open a 15-day Raise Room commitment window,
        and trade on the integrated DEX. Revenue share for DDollar pledgers.
      </p>

      {!canLaunch && (
        <p className="mt-3 rounded-lg border border-amber-700/40 bg-amber-900/20 p-3 text-xs text-amber-200">
          {tier === 'VISITOR'
            ? '🔒 Get Doxxed first to unlock launch rights.'
            : 'Token launch unlocks at Phase 7. Join the waitlist for early access.'}
        </p>
      )}

      {canLaunch ? (
        <button
          type="button"
          disabled
          className="mt-4 w-full cursor-not-allowed rounded-lg border border-violet-700/50 bg-violet-900/30 px-4 py-2 text-sm font-semibold text-violet-300"
          title="Phase 7+ — not yet enabled"
        >
          Launching soon (Phase 7)
        </button>
      ) : (
        <div className="mt-4">
          {!joined ? (
            <div className="flex gap-2">
              <input
                type="email"
                value={waitlistEmail}
                onChange={(e) => setWaitlistEmail(e.target.value)}
                placeholder="founder@your-startup.com"
                className="min-w-0 flex-1 rounded-lg border border-zinc-700 bg-black px-3 py-2 text-xs text-zinc-200"
              />
              <button
                type="button"
                onClick={() => setJoined(true)}
                disabled={!waitlistEmail.includes('@')}
                className="rounded-lg bg-violet-500 px-3 py-2 text-xs font-semibold text-white hover:bg-violet-400 disabled:opacity-50"
              >
                Join waitlist
              </button>
            </div>
          ) : (
            <p className="rounded-lg border border-violet-700 bg-violet-900/30 p-3 text-xs text-violet-200">
              You&apos;re on the list. We&apos;ll email you when Phase 7 opens.
            </p>
          )}
        </div>
      )}
    </div>
  );
}

function QuickLinksGrid() {
  const links = [
    { href: '/raise-room', label: 'Raise Room', desc: 'Discover founders · pledge DDollar', accent: 'text-emerald-300' },
    { href: '/founder-den', label: 'Founder Den', desc: 'Personal build dashboard', accent: 'text-violet-300' },
    { href: '/settings/ai-usage', label: 'AI Usage', desc: 'Proxy stats · connect Cursor', accent: 'text-amber-300' },
    { href: '/founder-os/decisions', label: 'Decision Log', desc: 'Routing decisions · cache hits', accent: 'text-sky-300' },
  ];
  return (
    <div>
      <h3 className="mb-3 text-[11px] font-semibold uppercase tracking-[0.18em] text-zinc-500">
        Quick links
      </h3>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {links.map((l) => (
          <Link
            key={l.href}
            href={l.href}
            className="rounded-xl border border-zinc-800 bg-zinc-950/40 p-4 hover:border-zinc-600"
          >
            <p className={`text-sm font-bold ${l.accent}`}>{l.label}</p>
            <p className="mt-1 text-[11px] text-zinc-500">{l.desc}</p>
          </Link>
        ))}
      </div>
    </div>
  );
}

function RecentActivityStrip({ dashboard }: { dashboard: DashboardData | null }) {
  void dashboard;
  return (
    <div className="rounded-xl border border-zinc-800 bg-zinc-950/40 p-5">
      <h3 className="text-[11px] font-semibold uppercase tracking-[0.18em] text-zinc-500">
        Recent activity
      </h3>
      <p className="mt-3 text-xs text-zinc-500">
        No recent activity yet. Once the Learning Engine (Phase 4) is live,
        routing improvements and project events will stream here.
      </p>
    </div>
  );
}

function Field({
  label,
  value,
  onChange,
  placeholder,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
}) {
  return (
    <label className="block text-xs">
      <span className="text-zinc-400">{label}</span>
      <input
        value={value}
        onChange={(e) => onChange(e.currentTarget.value)}
        placeholder={placeholder}
        className="mt-1 w-full rounded-lg border border-zinc-700 bg-black px-3 py-2 text-xs text-zinc-200"
      />
    </label>
  );
}

function TextArea({
  label,
  value,
  onChange,
  placeholder,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
}) {
  return (
    <label className="block text-xs">
      <span className="text-zinc-400">{label}</span>
      <textarea
        value={value}
        onChange={(e) => onChange(e.currentTarget.value)}
        placeholder={placeholder}
        rows={3}
        className="mt-1 w-full rounded-lg border border-zinc-700 bg-black px-3 py-2 text-xs text-zinc-200"
      />
    </label>
  );
}
