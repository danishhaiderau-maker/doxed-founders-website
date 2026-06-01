'use client';

import Link from 'next/link';
import { SiteNav } from '@/components/site-nav';

const SECTIONS = [
  {
    title: 'Founder Copilot setup',
    summary:
      'Connect founder profile, GitHub, AI Stack, and optional Founder Node. Copilot routes workforce agents and Cursor dispatch from chat.',
    href: '/founder-den',
    cta: 'Open Founder OS',
    topics: ['Rule-based vs LLM chat', 'Workforce agents (PM, Builder, Launch)', 'Command cursor in chat', 'GitHub auto-sync'],
  },
  {
    title: 'AI Stack (Builder settings)',
    summary:
      'LLM keys, Cursor Cloud Agents, Ollama, Phala TEE, memory storage mode, and current goal focus.',
    href: '/settings/builder',
    cta: 'Open AI Stack',
    topics: ['DeepSeek · OpenAI · Claude · Gemini', 'Cursor API for remote builds', 'Phala private inference', 'Goal focus for briefings'],
  },
  {
    title: 'Founder Node (Windows vault)',
    summary:
      'Self-custody project memory on your PC. Pair once via system tray — Founder OS receives metadata only.',
    href: '/founder-node',
    cta: 'Download Founder Node',
    topics: ['Windows .exe installer', 'Pairing code flow', '~/FounderVault files', 'Encrypted relay'],
  },
  {
    title: 'GitHub integration',
    summary:
      'OAuth connect (recommended) or repo + PAT. Commits auto-sync every 15 minutes and when Copilot opens.',
    href: '/founder-den?tab=launch',
    cta: 'Connect GitHub',
    topics: ['OAuth one-click', 'Repo starter templates', 'Auto-sync by commit SHA', 'Publish everywhere'],
  },
  {
    title: 'Launch pipeline',
    summary:
      'Readiness score, launchpad checks, Raise Room demand proof, and project room — unified in Founder OS.',
    href: '/founder-den?tab=launch',
    cta: 'Launch tab',
    topics: ['Launch readiness %', 'Build logs + video', 'Simulated raise', 'Launchpad request'],
  },
  {
    title: 'Agents marketplace',
    summary: 'Install workforce agents and run them against your project memory and GitHub context.',
    href: '/agents',
    cta: 'Browse agents',
    topics: ['PM · Researcher · Builder · Launch', 'Deduped build queue', 'GitHub issues', 'Cursor dispatch'],
  },
];

const QUICK_LINKS = [
  { label: 'List your project (scout)', href: '/list-your-project' },
  { label: 'Raise Room', href: '/raise-room' },
  { label: 'Build feed', href: '/feed' },
  { label: 'Paper trading alpha', href: '/paper-trading' },
];

export default function DevelopersPage() {
  return (
    <div className="min-h-screen bg-[#050508]">
      <header className="border-b border-zinc-800/80">
        <div className="mx-auto flex max-w-5xl flex-wrap items-center justify-between gap-4 px-6 py-5">
          <div>
            <Link href="/" className="text-xs text-zinc-500 hover:text-white">
              ← Home
            </Link>
            <h1 className="mt-1 text-2xl font-bold text-white">Developers & founders</h1>
            <p className="text-sm text-zinc-400">
              Build in public on Doxxed Crypto — Copilot, GitHub sync, Raise Room, and launch pipeline
            </p>
          </div>
          <SiteNav />
        </div>
      </header>

      <main className="mx-auto max-w-5xl space-y-10 px-6 py-10">
        <section className="rounded-2xl border border-violet-500/30 bg-violet-950/15 p-6">
          <p className="text-xs font-semibold uppercase tracking-wider text-violet-300">Start here</p>
          <h2 className="mt-2 text-xl font-bold text-white">~5 minute Windows onboarding</h2>
          <p className="mt-2 text-sm text-zinc-300">
            Sign in, open Founder OS, and follow the setup wizard: activate profile → GitHub OAuth → AI Stack →
            goal → optional Founder Node vault.
          </p>
          <div className="mt-4 flex flex-wrap gap-3">
            <Link
              href="/login"
              className="rounded-lg bg-violet-600 px-5 py-2.5 text-sm font-medium text-white hover:bg-violet-500"
            >
              Sign in
            </Link>
            <Link
              href="/founder-den"
              className="rounded-lg border border-zinc-600 px-5 py-2.5 text-sm text-zinc-200 hover:border-zinc-400"
            >
              Founder OS →
            </Link>
          </div>
        </section>

        <div className="grid gap-6 md:grid-cols-2">
          {SECTIONS.map((section) => (
            <article
              key={section.title}
              className="rounded-2xl border border-zinc-800/80 bg-zinc-900/30 p-5"
            >
              <h3 className="font-semibold text-white">{section.title}</h3>
              <p className="mt-2 text-sm text-zinc-400">{section.summary}</p>
              <ul className="mt-3 space-y-1 text-xs text-zinc-500">
                {section.topics.map((t) => (
                  <li key={t}>· {t}</li>
                ))}
              </ul>
              <Link
                href={section.href}
                className="mt-4 inline-block text-sm font-medium text-violet-300 hover:underline"
              >
                {section.cta} →
              </Link>
            </article>
          ))}
        </div>

        <section className="rounded-2xl border border-zinc-800 bg-zinc-950/50 p-6">
          <h3 className="font-semibold text-white">Environment (self-host / Railway)</h3>
          <ul className="mt-3 space-y-2 font-mono text-xs text-zinc-400">
            <li>GITHUB_CLIENT_ID · GITHUB_CLIENT_SECRET — OAuth connect</li>
            <li>GITHUB_OAUTH_CALLBACK_URL — defaults to API /api/auth/github/callback</li>
            <li>WEB_APP_URL — redirect after OAuth (production site)</li>
            <li>DISABLE_GITHUB_AUTO_SYNC=1 — optional kill switch</li>
          </ul>
          <p className="mt-4 text-sm text-zinc-500">
            Full operator docs live in the repo under <code className="text-zinc-400">docs/</code> — including{' '}
            <code className="text-zinc-400">FOUNDER_COPILOT_SETUP.md</code> and{' '}
            <code className="text-zinc-400">PHALA_PRIVATE_AI.md</code>.
          </p>
        </section>

        <section>
          <h3 className="text-sm font-semibold uppercase tracking-wider text-zinc-500">Quick links</h3>
          <div className="mt-3 flex flex-wrap gap-2">
            {QUICK_LINKS.map((link) => (
              <Link
                key={link.href}
                href={link.href}
                className="rounded-full border border-zinc-700 px-3 py-1 text-xs text-zinc-300 hover:border-zinc-500"
              >
                {link.label}
              </Link>
            ))}
          </div>
        </section>
      </main>
    </div>
  );
}
