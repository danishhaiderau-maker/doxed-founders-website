import Link from 'next/link';

export function ProjectsFounderClaimBanner() {
  return (
    <section
      className="mt-6 rounded-2xl border border-violet-500/30 bg-gradient-to-br from-violet-950/40 via-zinc-900/50 to-sky-950/25 p-5 md:p-6"
      aria-labelledby="founder-claim-heading"
    >
      <p className="text-[10px] font-semibold uppercase tracking-[0.2em] text-violet-300">
        For project founders
      </p>
      <h2 id="founder-claim-heading" className="mt-1 text-lg font-bold text-white md:text-xl">
        Claim your listing — run it from Founder OS
      </h2>
      <p className="mt-2 max-w-3xl text-sm leading-relaxed text-zinc-400">
        If you built one of the projects below, you can take control in a few minutes. No paperwork —
        just prove you are the public face on X.
      </p>

      <ol className="mt-4 grid gap-2 text-sm text-zinc-300 md:grid-cols-2">
        <li className="flex gap-2 rounded-lg border border-zinc-800/80 bg-black/25 px-3 py-2.5">
          <span className="shrink-0 font-bold text-violet-400">1</span>
          <span>
            Open <strong className="text-white">your project page</strong> from the grid below.
          </span>
        </li>
        <li className="flex gap-2 rounded-lg border border-zinc-800/80 bg-black/25 px-3 py-2.5">
          <span className="shrink-0 font-bold text-violet-400">2</span>
          <span>
            <Link href="/login" className="font-semibold text-sky-300 underline hover:text-sky-200">
              Sign in with X
            </Link>{' '}
            — the same handle shown on that project (from DexScreener / public profile).
          </span>
        </li>
        <li className="flex gap-2 rounded-lg border border-zinc-800/80 bg-black/25 px-3 py-2.5">
          <span className="shrink-0 font-bold text-violet-400">3</span>
          <span>
            Tap <strong className="text-white">Claim profile</strong> to verify automatically, edit
            your listing, and activate your founder account.
          </span>
        </li>
        <li className="flex gap-2 rounded-lg border border-zinc-800/80 bg-black/25 px-3 py-2.5">
          <span className="shrink-0 font-bold text-violet-400">4</span>
          <span>
            Get <strong className="text-amber-200">25,000 DDollar</strong> to reward scouts and your
            community — spend it on agents, votes, and perks today.
          </span>
        </li>
      </ol>

      <p className="mt-4 text-xs leading-relaxed text-zinc-500">
        <span className="font-medium text-emerald-300/90">Token launch tip:</span> the more DDollar you
        earn and hold before we go live, the larger your share of the community airdrop. Build in
        public, grow your balance, and your allocation scales with your contribution.
      </p>

      <div className="mt-4 flex flex-wrap gap-2">
        <Link
          href="/login?callbackUrl=/projects"
          className="rounded-lg bg-violet-600 px-4 py-2 text-xs font-semibold text-white hover:bg-violet-500"
        >
          Sign in with X
        </Link>
        <Link
          href="/ddollar"
          className="rounded-lg border border-amber-500/35 bg-amber-950/20 px-4 py-2 text-xs font-semibold text-amber-100 hover:border-amber-400/50"
        >
          DDollar rewards →
        </Link>
        <Link
          href="/founder-den"
          className="rounded-lg border border-zinc-700 px-4 py-2 text-xs text-zinc-300 hover:border-violet-500/40 hover:text-white"
        >
          Founder OS →
        </Link>
      </div>
    </section>
  );
}
