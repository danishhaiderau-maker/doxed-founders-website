import Link from 'next/link';

const WHY_BULLETS = [
  {
    icon: '01',
    text: 'Founder Free includes 200K managed weighted units every week.',
  },
  {
    icon: '02',
    text: 'DeepSeek V4 Flash handles everyday work; Pro is reserved for explicit hard tasks.',
  },
  {
    icon: '03',
    text: 'Project memory and coordination keep agents aware of verified prior work.',
  },
  {
    icon: '04',
    text: 'Add unlimited personal AI profiles with your own key, model, and base URL.',
  },
  {
    icon: '05',
    text: 'Run Ollama locally for private, offline-capable work without managed quota.',
  },
  {
    icon: '06',
    text: 'Every managed task shows the route, usage, cache result, and measured savings.',
  },
];

const PATHS = [
  {
    id: 'sovereign',
    name: 'Sovereign',
    blurb: 'Everything runs locally. Maximum privacy. Lowest cost.',
    href: '/founder-den?onboard=sovereign',
  },
  {
    id: 'byo',
    name: 'Hybrid',
    blurb: 'Local compute with optional cloud models when needed.',
    href: '/founder-den?onboard=byo',
  },
  {
    id: 'founder_cloud',
    name: 'Founder Cloud',
    blurb: 'Scale to cloud infrastructure once your idea has traction.',
    href: '/founder-den?onboard=founder_cloud',
  },
];

export function LandingFounderOsPromo() {
  return (
    <section className="overflow-hidden rounded-2xl border border-violet-500/30 bg-gradient-to-br from-violet-950/30 via-[#0a0a12] to-black shadow-[0_0_60px_rgba(139,92,246,0.08)]">
      {/* Headline + pitch */}
      <div className="border-b border-violet-500/15 px-4 py-6 sm:px-6 lg:px-8 lg:py-8">
        <p className="text-[10px] font-bold uppercase tracking-[0.28em] text-violet-400">Founder OS</p>
        <h2 className="mt-2 text-2xl font-bold leading-tight tracking-tight text-white sm:text-3xl lg:text-4xl">
          Your laptop is the compute.
        </h2>
        <p className="mt-3 max-w-2xl text-sm leading-relaxed text-zinc-300 sm:text-base">
          A coordinated workspace that spends context on the work that matters.
        </p>
        <p className="mt-2 max-w-3xl text-sm leading-relaxed text-zinc-400">
          Start with a weekly Founder Free allowance, bring any OpenAI-compatible key, or work
          privately through Ollama. Founder OS keeps project memory, agents, receipts, and local
          infrastructure in one installed application.
        </p>
      </div>

      {/* Why founders use Founder OS */}
      <div className="border-b border-violet-500/15 px-4 py-5 sm:px-6 lg:px-8">
        <p className="text-[10px] font-bold uppercase tracking-[0.2em] text-violet-300/80">
          Why founders use Founder OS
        </p>
        <ul className="mt-3 grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
          {WHY_BULLETS.map((b) => (
            <li
              key={b.text}
              className="flex items-start gap-2 rounded-lg border border-zinc-800/70 bg-black/30 px-3 py-2 text-[12px] text-zinc-200"
            >
              <span className="text-base leading-none">{b.icon}</span>
              <span>{b.text}</span>
            </li>
          ))}
        </ul>
      </div>

      {/* Pick your path */}
      <div className="border-b border-violet-500/15 px-4 py-5 sm:px-6 lg:px-8">
        <p className="text-[10px] font-bold uppercase tracking-[0.2em] text-violet-300/80">Pick your path</p>
        <div className="mt-3 grid gap-3 md:grid-cols-3">
          {PATHS.map((p) => (
            <Link
              key={p.id}
              href={p.href}
              className="group rounded-xl border border-zinc-800/80 bg-black/40 p-3 transition hover:border-violet-400/50 hover:bg-violet-950/20"
            >
              <p className="text-sm font-bold text-white group-hover:text-violet-100">{p.name}</p>
              <p className="mt-1 text-[11px] leading-relaxed text-zinc-400">{p.blurb}</p>
              <p className="mt-2 text-[11px] font-semibold text-violet-300/90 group-hover:text-violet-200">
                Explore path →
              </p>
            </Link>
          ))}
        </div>
      </div>

      {/* Start building before you start spending */}
      <div className="border-b border-violet-500/15 px-4 py-4 sm:px-6 lg:px-8">
        <p className="text-sm font-semibold text-white">
          Start building before you start spending.
        </p>
        <p className="mt-1 text-[12px] text-zinc-400">
          Most founders spend money before they validate an idea. Founder OS helps you validate first.
          Then scale.
        </p>
      </div>

      {/* Two offers */}
      <div className="grid gap-3 px-4 py-5 sm:px-6 lg:grid-cols-2 lg:px-8">
        <div className="rounded-xl border border-cyan-500/35 bg-cyan-950/15 p-4">
          <div className="flex items-center justify-between gap-2">
            <p className="text-[10px] font-bold uppercase tracking-[0.18em] text-cyan-300">
              Founder Free
            </p>
            <span className="rounded-full border border-cyan-500/40 bg-cyan-500/10 px-2 py-0.5 text-[9px] font-bold uppercase text-cyan-100">
              200K weekly
            </span>
          </div>
          <p className="mt-2 text-base font-bold text-white">Build with managed DeepSeek</p>
          <p className="mt-1.5 text-[12px] leading-relaxed text-zinc-300">
            Use Founder-managed DeepSeek V4 Flash for everyday work. Personal provider profiles
            and local Ollama remain available without consuming the managed allowance.
          </p>
          <Link
            href="/founder-den?onboard=sovereign"
            className="mt-3 inline-flex rounded-lg border border-cyan-500/45 bg-cyan-500/10 px-3 py-1.5 text-xs font-bold text-cyan-100 transition hover:bg-cyan-500/20"
          >
            Open Founder OS →
          </Link>
        </div>

        {/* Hire an AI Agent — Free (DDollar) */}
        <div className="rounded-xl border border-emerald-500/40 bg-gradient-to-br from-emerald-950/30 via-black/40 to-black p-4">
          <div className="flex items-center justify-between gap-2">
            <p className="text-[10px] font-bold uppercase tracking-[0.18em] text-emerald-300">
              Hire an AI Agent
            </p>
            <span className="rounded-full border border-emerald-500/50 bg-emerald-500/10 px-2 py-0.5 text-[9px] font-bold uppercase text-emerald-200">
              Free*
            </span>
          </div>
          <p className="mt-2 text-base font-bold text-white">Hire an AI Agent — Free</p>
          <p className="mt-1.5 text-[12px] leading-relaxed text-zinc-300">
            Launch a ready-to-use agent and let it work for you. For trading research, connect your
            Bitfinex API and run paper trading to evaluate strategies with live market data — without
            risking real capital.
          </p>
          <p className="mt-2 text-[11px] font-semibold text-emerald-300/90">
            * Free in DDollar — earn it on-platform, no card needed.
          </p>
          <Link
            href="/agent-hub"
            className="mt-3 inline-flex rounded-lg border border-emerald-500/50 bg-emerald-500/10 px-3 py-1.5 text-xs font-bold text-emerald-200 transition hover:bg-emerald-500/20"
          >
            Hire Agent →
          </Link>
        </div>
      </div>
    </section>
  );
}
