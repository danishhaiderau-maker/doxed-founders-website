'use client';

import Link from 'next/link';
import { useSession } from 'next-auth/react';
import { useState } from 'react';
import { claimProjectProfile } from '@/lib/api';

type Props = {
  slug: string;
  projectName: string;
  claimProfile?: {
    claimable: boolean;
    claimed: boolean;
    isOwner: boolean;
    projectTwitterHandle: string | null;
    requiresXSignIn: boolean;
  };
  onClaimed: () => void;
};

export function ClaimProfilePanel({ slug, projectName, claimProfile, onClaimed }: Props) {
  const { data: session } = useSession();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  if (!claimProfile?.claimable && !claimProfile?.claimed) return null;

  if (claimProfile.claimed) {
    return (
      <div className="rounded-xl border border-emerald-500/30 bg-emerald-950/20 px-4 py-3 text-sm text-emerald-100">
        Verified owner profile — founder controls updates, build logs, and Founder OS tools.
        {session?.accessToken && (
          <Link href="/founder-den" className="ml-2 font-semibold underline">
            Open Founder OS →
          </Link>
        )}
      </div>
    );
  }

  async function handleClaim() {
    if (!session?.accessToken) return;
    setBusy(true);
    setError(null);
    try {
      const res = await claimProjectProfile(slug, session.accessToken);
      setSuccess(`Profile claimed! Founder OS is live for ${projectName}.`);
      onClaimed();
      if (res.founderSlug) {
        setTimeout(() => {
          window.location.href = '/founder-den';
        }, 1200);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Claim failed');
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="rounded-xl border border-violet-500/35 bg-violet-950/25 px-4 py-4">
      <p className="text-xs font-bold uppercase tracking-wider text-violet-300">Claim profile</p>
      <p className="mt-2 text-sm text-zinc-300">
        Are you the founder of {projectName}? Verify with X to unlock Founder OS — updates, videos,
        build logs, and community tools.
      </p>
      {claimProfile.projectTwitterHandle && (
        <p className="mt-2 text-xs text-zinc-500">
          Must sign in as{' '}
          <span className="font-semibold text-sky-300">@{claimProfile.projectTwitterHandle}</span>{' '}
          (matches DexScreener)
        </p>
      )}
      {!session?.accessToken ? (
        <Link
          href={`/login?callbackUrl=${encodeURIComponent(`/project/${slug}`)}`}
          className="mt-3 inline-block rounded-lg bg-violet-600 px-4 py-2 text-sm font-semibold text-white hover:bg-violet-500"
        >
          Sign in with X to claim
        </Link>
      ) : (
        <button
          type="button"
          onClick={handleClaim}
          disabled={busy}
          className="mt-3 rounded-lg bg-violet-600 px-4 py-2 text-sm font-semibold text-white hover:bg-violet-500 disabled:opacity-50"
        >
          {busy ? 'Verifying…' : 'Claim profile'}
        </button>
      )}
      {error && <p className="mt-2 text-xs text-red-300">{error}</p>}
      {success && <p className="mt-2 text-xs text-emerald-300">{success}</p>}
    </div>
  );
}
