'use client';

import Link from 'next/link';

const WORKFLOW_CARDS = [
  {
    title: 'Build in public',
    color: 'violet',
    icon: '🔨',
    body: 'Connect GitHub. Ship in public. Every commit becomes transparency, content, and reputation.',
    tags: [
      { label: 'GitHub Sync', href: '/founder-den?tab=build' },
      { label: 'Auto Updates', href: '/feed' },
    ],
  },
  {
    title: 'Validate demand',
    color: 'blue',
    icon: '📈',
    body: 'Traders use Ddollar paper capital. Scouts vote. Conviction is visible before you raise.',
    tags: [
      { label: 'Paper Trading', href: '/paper-trading' },
      { label: 'Scout Votes', href: '/scout-votes' },
    ],
  },
  {
    title: 'Raise support',
    color: 'amber',
    icon: '👥',
    body: 'Find your earliest believers. Simulated allocations, public proof, real community.',
    tags: [
      { label: 'Raise Room', href: '/raise-room' },
      { label: 'Allocations', href: '/raise-room' },
    ],
  },
  {
    title: 'Launch with trust',
    color: 'emerald',
    icon: '🚀',
    body: 'Launch when execution and demand are visible — not before. Trust is earned, not promised.',
    tags: [
      { label: 'Launch Score', href: '/founder-den' },
      { label: 'Distribution', href: '/projects' },
    ],
  },
] as const;

const colorMap = {
  violet: 'border-violet-500/30 bg-violet-950/20 from-violet-600/10',
  blue: 'border-blue-500/30 bg-blue-950/20 from-blue-600/10',
  amber: 'border-amber-500/30 bg-amber-950/20 from-amber-600/10',
  emerald: 'border-emerald-500/30 bg-emerald-950/20 from-emerald-600/10',
};

export function LandingWorkflowStrip() {
  return (
    <section className="border-b border-zinc-800/80 py-14 md:py-20">
      <div className="mx-auto w-full max-w-[90rem] px-4 sm:px-6 lg:px-10">
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          {WORKFLOW_CARDS.map((card) => (
            <div
              key={card.title}
              className={`rounded-2xl border bg-gradient-to-br to-transparent p-5 ${colorMap[card.color]}`}
            >
              <span className="text-2xl" aria-hidden>
                {card.icon}
              </span>
              <h3 className="mt-3 text-sm font-bold uppercase tracking-wide text-white">
                {card.title}
              </h3>
              <p className="mt-2 text-sm leading-relaxed text-zinc-400">{card.body}</p>
              <div className="mt-4 flex flex-wrap gap-2">
                {card.tags.map((tag) => (
                  <Link
                    key={tag.label}
                    href={tag.href}
                    className="rounded-lg border border-zinc-700/80 bg-black/30 px-2.5 py-1 text-[11px] font-medium text-zinc-300 hover:border-white/30 hover:text-white"
                  >
                    {tag.label}
                  </Link>
                ))}
              </div>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

export function LandingTrustFooter() {
  const items = [
    'No VC allocations',
    '80% community owned',
    'Transparent by default',
    'Privacy by design',
  ];

  return (
    <section className="border-t border-zinc-800/80 bg-zinc-950/80 py-6">
      <div className="mx-auto flex w-full max-w-[90rem] flex-wrap items-center justify-between gap-4 px-4 sm:px-6 lg:px-10">
        <div className="flex flex-wrap gap-x-6 gap-y-2">
          {items.map((item) => (
            <span key={item} className="inline-flex items-center gap-2 text-xs text-zinc-500">
              <span className="text-emerald-400">✓</span>
              {item}
            </span>
          ))}
        </div>
        <span className="text-xs text-zinc-600">Scroll to explore ↓</span>
      </div>
    </section>
  );
}
