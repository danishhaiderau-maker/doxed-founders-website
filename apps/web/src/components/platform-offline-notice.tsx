import Link from 'next/link';
import { BACKEND_API_RETIRED_REASON } from '@/lib/backend-api-retired';

const FEATURE_BLURBS: Record<string, { title: string; body: string }> = {
  login: {
    title: 'Sign-in is offline',
    body: 'Email, Google, X / Twitter, and passkey login used the Railway website API and Neon. Those backends are being removed, so account sign-in is static-only for now.',
  },
  register: {
    title: 'Registration is offline',
    body: 'New accounts required the Nest API + Neon. Registration is retired on this static Vercel site.',
  },
  'x-sync': {
    title: 'X sync is offline',
    body: 'Connected Accounts / X (Twitter) OAuth sync wrote through Railway `/api` into Neon. That path is retired — no sync loops run from this site.',
  },
  x402: {
    title: 'x402 Signal API is offline',
    body: 'Agentic x402 polls and Signal Cycle keys hit Railway `/api/trading-agents/...`. With the API gone, x402 docs are archival static copy only.',
  },
  trading: {
    title: 'Trading & agent dashboards are offline',
    body: 'Paper trading, Agent Hub, live-copy hire, portfolios, and related dashboards depended on Railway. They are replaced with this static notice so nothing polls a dead API.',
  },
  account: {
    title: 'Account hub is offline',
    body: 'Overview, Connected Accounts (X sync), security, and messages all called Railway `/api`. Static notice only.',
  },
  generic: {
    title: 'This feature is offline',
    body: 'This page used the Railway website API. It is replaced with static content so the Vercel site stays deployable without a backend.',
  },
};

export type OfflineFeature = keyof typeof FEATURE_BLURBS;

export function PlatformOfflineNotice({
  feature = 'generic',
  heading,
}: {
  feature?: OfflineFeature;
  heading?: string;
}) {
  const blurb = FEATURE_BLURBS[feature] ?? FEATURE_BLURBS.generic;

  return (
    <main className="mx-auto flex min-h-[70vh] max-w-2xl flex-col justify-center px-4 py-16 text-zinc-200">
      <p className="text-[11px] font-semibold uppercase tracking-[0.2em] text-amber-400/90">
        Static site · API retired
      </p>
      <h1 className="mt-3 text-3xl font-bold text-white">{heading ?? blurb.title}</h1>
      <p className="mt-4 text-sm leading-relaxed text-zinc-400">{blurb.body}</p>
      <p className="mt-4 rounded-lg border border-zinc-800 bg-zinc-950/80 p-4 text-xs leading-relaxed text-zinc-500">
        {BACKEND_API_RETIRED_REASON} Cite: RK t781u · Fly deleted · Railway/Neon removal plan · R50c
        cost check: static reduces API dependency.
      </p>
      <div className="mt-8 flex flex-wrap gap-3 text-sm">
        <Link
          href="/"
          className="rounded-lg border border-zinc-600 px-4 py-2 font-medium text-white hover:border-zinc-400"
        >
          Home
        </Link>
        <Link href="/privacy" className="rounded-lg px-4 py-2 text-zinc-400 hover:text-white">
          Privacy
        </Link>
        <Link href="/rules" className="rounded-lg px-4 py-2 text-zinc-400 hover:text-white">
          Rules
        </Link>
        <Link href="/pricing" className="rounded-lg px-4 py-2 text-zinc-400 hover:text-white">
          Pricing
        </Link>
      </div>
    </main>
  );
}
