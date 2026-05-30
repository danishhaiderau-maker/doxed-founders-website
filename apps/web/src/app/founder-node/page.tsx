'use client';

import Link from 'next/link';
import { SiteNav } from '@/components/site-nav';

export default function FounderNodePage() {
  return (
    <div className="min-h-screen bg-[#050508]">
      <header className="border-b border-[var(--color-border)]">
        <div className="mx-auto flex max-w-4xl items-center justify-between px-6 py-5">
          <div>
            <Link href="/" className="text-xs text-[var(--color-muted)] hover:text-white">
              ← Home
            </Link>
            <h1 className="text-2xl font-bold">Founder Node</h1>
            <p className="text-sm text-[var(--color-muted)]">
              Self-custody vault for project memory — your machine, your data
            </p>
          </div>
          <SiteNav />
        </div>
      </header>

      <main className="mx-auto max-w-4xl space-y-8 px-6 py-10">
        <section className="rounded-xl border border-cyan-500/30 bg-cyan-950/10 p-6">
          <h2 className="text-lg font-semibold text-cyan-100">What is Founder Node?</h2>
          <p className="mt-3 text-sm leading-relaxed text-zinc-300">
            Founder Node is a small desktop app (Electron) that stores your Founder OS project memory,
            roadmaps, and AI context in a local vault on your laptop — not on our servers. Founder OS
            stays the control plane; the node syncs only lightweight metadata so you can resume on any
            device. Your full vault never leaves your machine.
          </p>
        </section>

        <section className="rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] p-6">
          <h2 className="font-semibold">How to run it (Phase 1 — developer setup)</h2>
          <ol className="mt-4 list-decimal space-y-3 pl-5 text-sm text-zinc-300">
            <li>Clone the repo and install dependencies: <code className="rounded bg-zinc-900 px-1.5 py-0.5">npm install</code></li>
            <li>Start the Founder Node app: <code className="rounded bg-zinc-900 px-1.5 py-0.5">npm run dev:founder-node</code></li>
            <li>
              In Founder OS, open{' '}
              <Link href="/settings/builder" className="text-cyan-300 hover:underline">
                Builder settings
              </Link>{' '}
              → Memory storage → choose <strong className="text-white">Founder Node</strong> → pair with the code shown in the tray app
            </li>
            <li>Your vault lives at <code className="rounded bg-zinc-900 px-1.5 py-0.5">~/FounderVault/</code> on your PC</li>
          </ol>
          <p className="mt-4 text-xs text-zinc-500">
            Packaged installer (one-click download) is Phase 2. For now, founders with GitHub access run the node from source.
          </p>
          <div className="mt-6 flex flex-wrap gap-3">
            <Link
              href="/settings/builder"
              className="rounded-lg bg-cyan-600 px-5 py-2.5 text-sm font-medium text-white hover:bg-cyan-500"
            >
              Pair Founder Node →
            </Link>
            <a
              href="https://github.com/danishhaiderau-maker/doxed-founders-website"
              target="_blank"
              rel="noopener noreferrer"
              className="rounded-lg border border-zinc-600 px-5 py-2.5 text-sm text-zinc-200 hover:border-zinc-400"
            >
              GitHub repo ↗
            </a>
          </div>
        </section>

        <section className="rounded-xl border border-zinc-700 bg-zinc-950/50 p-6 text-sm text-zinc-400">
          <h2 className="font-semibold text-white">Local storage vs cloud</h2>
          <ul className="mt-3 space-y-2">
            <li><strong className="text-zinc-200">Platform memory</strong> — stored in our database (convenient, not self-custody)</li>
            <li><strong className="text-zinc-200">Browser local</strong> — stays in your browser only</li>
            <li><strong className="text-zinc-200">GitHub files</strong> — memory in your repo</li>
            <li><strong className="text-cyan-200">Founder Node</strong> — encrypted vault on your disk; we only relay pairing metadata</li>
          </ul>
        </section>
      </main>
    </div>
  );
}
