'use client';

import Link from 'next/link';

type ClaimProfile = {
  claimable: boolean;
  claimed: boolean;
  isOwner?: boolean;
  profileLocked?: boolean;
  projectTwitterHandle: string | null;
};

type Props = {
  projectName: string;
  slug: string;
  claimProfile?: ClaimProfile;
};

export function ProjectOwnerFlashBanner({ projectName, slug, claimProfile }: Props) {
  if (!claimProfile?.projectTwitterHandle && !claimProfile?.claimable && !claimProfile?.claimed) {
    return null;
  }

  const handle = claimProfile?.projectTwitterHandle;

  if (claimProfile?.isOwner) {
    return (
      <div className="rounded-xl border border-emerald-500/35 bg-emerald-950/25 px-4 py-3 text-sm text-emerald-100">
        <p className="font-semibold text-emerald-200">You control this listing</p>
        <p className="mt-1 text-xs text-emerald-100/80">
          Use Founder OS for community, agents, and build-in-public. Lock your profile (button beside
          Share on X) so no one can hijack this page with a fake X after you leave DexScreener.
        </p>
        <Link href="/founder-den" className="mt-2 inline-block text-xs font-semibold underline">
          Open Founder OS →
        </Link>
      </div>
    );
  }

  if (claimProfile?.claimed) {
    return (
      <div className="rounded-xl border border-zinc-700/80 bg-zinc-900/40 px-4 py-3 text-xs text-zinc-400">
        This project profile is claimed by the verified founder
        {handle ? (
          <>
            {' '}
            (<span className="text-sky-300">@{handle}</span>)
          </>
        ) : null}
        . Community is managed on Doxxed Crypto.
      </div>
    );
  }

  return (
    <div className="rounded-xl border border-amber-500/35 bg-amber-950/20 px-4 py-3">
      <p className="text-xs font-bold uppercase tracking-wider text-amber-200">
        Founders — claim this listing
      </p>
      <p className="mt-2 text-sm text-amber-50/90">
        If you run <strong className="text-white">{projectName}</strong>, connect with the{' '}
        <strong className="text-sky-300">
          official X on DexScreener{handle ? ` (@${handle})` : ''}
        </strong>
        , claim automatically, and manage your community here. AI agents help you post updates, answer
        scouts, and ship integration advice. Share feedback anytime — connected founders qualify for
        the airdrop (more DDollar = larger share at token launch).
      </p>
      <div className="mt-3 flex flex-wrap gap-2">
        <Link
          href={`/login?callbackUrl=${encodeURIComponent(`/project/${slug}`)}`}
          className="rounded-lg bg-violet-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-violet-500"
        >
          Sign in with X to claim
        </Link>
        <Link href="/projects" className="rounded-lg border border-zinc-600 px-3 py-1.5 text-xs text-zinc-300">
          How claiming works →
        </Link>
      </div>
    </div>
  );
}
