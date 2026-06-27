'use client';

import Link from 'next/link';
import { useEffect, useRef, useState } from 'react';
import { ChevronDown } from 'lucide-react';
import { AndroidAppDownloads } from '@/components/android-app-downloads';

type MoreLink = {
  href: string;
  label: string;
  sub?: string;
};

const MORE_LINKS: MoreLink[] = [
  { href: '/predict?tab=rules', label: 'Predictions', sub: 'Oracle rank · constitution markets' },
  { href: '/list-your-project', label: 'Apply for listing', sub: 'Doxxed founders only' },
  { href: '/trust-center?tab=scout-voting', label: 'Scout voting', sub: 'Review pending projects' },
  { href: '/discover', label: 'Discover projects', sub: 'Curated build-in-public' },
  { href: '/builder-rewards', label: 'Builder rewards', sub: 'Airdrop share by contribution' },
  { href: '/mobile', label: 'Android app', sub: 'Trade & Founder OS on phone' },
];

export function LandingQuickActions({ scoutPending = 0 }: { scoutPending?: number }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function onClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener('mousedown', onClick);
    return () => document.removeEventListener('mousedown', onClick);
  }, []);

  return (
    <div className="flex flex-wrap items-center gap-2">
      <Link
        href="/agent-hub"
        className="rounded-xl border border-emerald-500/50 bg-emerald-950/40 px-4 py-2 text-sm font-semibold text-emerald-50 shadow-lg shadow-emerald-950/25 transition hover:bg-emerald-900/50"
      >
        BTC Agent
        <span className="mt-0.5 block text-[10px] font-normal text-emerald-200/70">Live showcase · copy trade</span>
      </Link>
      <Link
        href="/founder-den?onboard=sovereign"
        className="rounded-xl bg-violet-600 px-4 py-2 text-sm font-semibold text-white shadow-lg shadow-violet-900/40 transition hover:bg-violet-500"
      >
        Start Founder OS
        <span className="mt-0.5 block text-[10px] font-normal text-violet-200/80">$0 inference · local compute</span>
      </Link>
      <Link
        href="/paper-trading"
        className="rounded-xl border border-amber-500/40 bg-amber-950/30 px-4 py-2 text-sm font-semibold text-amber-50 transition hover:border-amber-400/60 hover:bg-amber-900/35"
      >
        Paper trade
        <span className="mt-0.5 block text-[10px] font-normal text-amber-200/70">Free DDollar · verified rank</span>
      </Link>

      <div className="relative" ref={ref}>
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          className="flex items-center gap-1.5 rounded-xl border border-zinc-700 bg-zinc-900/80 px-4 py-2 text-sm font-semibold text-zinc-100 transition hover:border-zinc-500 hover:bg-zinc-800"
          aria-expanded={open}
          aria-haspopup="true"
        >
          More
          <ChevronDown className={`h-4 w-4 transition ${open ? 'rotate-180' : ''}`} />
        </button>
        {open ? (
          <div className="absolute right-0 z-50 mt-1.5 w-72 overflow-hidden rounded-xl border border-zinc-700/90 bg-[#0a0a10] shadow-2xl shadow-black/60">
            <ul className="max-h-[min(24rem,70vh)] overflow-y-auto py-1">
              {MORE_LINKS.map((link) => {
                const label =
                  link.label === 'Scout voting' && scoutPending > 0
                    ? `${link.label} (${scoutPending})`
                    : link.label;
                return (
                  <li key={link.href}>
                    <Link
                      href={link.href}
                      onClick={() => setOpen(false)}
                      className="block px-4 py-2.5 transition hover:bg-zinc-800/80"
                    >
                      <span className="text-sm font-semibold text-zinc-100">{label}</span>
                      {link.sub ? (
                        <span className="mt-0.5 block text-[10px] text-zinc-500">{link.sub}</span>
                      ) : null}
                    </Link>
                  </li>
                );
              })}
            </ul>
            <div className="border-t border-zinc-800 px-3 py-2">
              <AndroidAppDownloads variant="default" />
            </div>
          </div>
        ) : null}
      </div>
    </div>
  );
}
