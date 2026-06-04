import Link from 'next/link';
import { SiteBrand, SiteNav } from '@/components/site-nav';
import { AndroidAppDownloads } from '@/components/android-app-downloads';
import { FounderNodeDownloads } from '@/components/founder-node-downloads';

export const metadata = {
  title: 'Download — Doxxed Crypto Mobile & Founder Node',
  description:
    'Install the Doxxed Crypto Android app. Discover, trading, agents, and Founder OS in your pocket. Desktop Founder Node for encrypted vault.',
};

export default function MobileDownloadPage() {
  return (
    <main className="min-h-screen bg-[#050508] text-white">
      <header className="border-b border-zinc-800/80 bg-[#050508]/95">
        <div className="mx-auto flex w-full max-w-3xl flex-wrap items-center justify-between gap-4 px-4 py-5 sm:px-6">
          <SiteBrand />
          <SiteNav />
        </div>
      </header>

      <div className="mx-auto w-full max-w-3xl space-y-10 px-4 py-10 sm:px-6">
        <section>
          <p className="text-[10px] font-bold uppercase tracking-[0.24em] text-emerald-400">Phase 1 — Android</p>
          <h1 className="mt-2 text-3xl font-bold tracking-tight">Doxxed Crypto for Android</h1>
          <p className="mt-3 text-sm leading-relaxed text-zinc-400">
            Official app for Discover, rankings, Trust Center, agents, paper trading, feed, and Founder OS — same
            account as{' '}
            <a href="https://doxxedcrypto.digital" className="text-cyan-300 underline">
              doxxedcrypto.digital
            </a>
            .
          </p>
          <p className="mt-2 text-sm text-zinc-500">
            Vault encrypted on our servers; readable only on your devices. Choose Phala or local Ollama on desktop when
            you want confidential AI — not just encrypted storage.
          </p>
          <div className="mt-6">
            <AndroidAppDownloads variant="hero" showInstallGuide />
          </div>
        </section>

        <section className="rounded-2xl border border-violet-500/25 bg-violet-950/15 p-6">
          <p className="text-[10px] font-bold uppercase tracking-[0.2em] text-violet-400">Desktop — Founder Node</p>
          <h2 className="mt-2 text-xl font-bold">Private vault on your PC</h2>
          <p className="mt-2 text-sm text-zinc-400">
            Goals, roadmap, and private notes live in <code className="text-zinc-300">~/FounderVault/</code> with
            encrypted sync. Pair once in Settings → Builder.
          </p>
          <div className="mt-5">
            <FounderNodeDownloads />
          </div>
          <Link href="/settings/builder" className="mt-4 inline-block text-sm font-semibold text-violet-300 hover:underline">
            Open Founder Node settings →
          </Link>
        </section>

        <section className="rounded-2xl border border-zinc-800 bg-zinc-950/50 p-6 text-sm text-zinc-400">
          <h2 className="text-base font-semibold text-white">What the Android app includes today</h2>
          <ul className="mt-3 list-disc space-y-1 pl-5">
            <li>Discover, projects, Trust Center, agent hub</li>
            <li>Paper trading, predictions, watchlist, portfolio</li>
            <li>Founder OS, Builder settings, Copilot (cloud AI / Phala when configured)</li>
          </ul>
          <p className="mt-4 text-xs text-zinc-500">
            Coming later: full Founder Vault sync on phone (see{' '}
            <a
              href="https://github.com/danishhaiderau-maker/doxed-founders-website/blob/master/docs/MOBILE_VAULT_ROADMAP.md"
              className="text-cyan-300 underline"
              target="_blank"
              rel="noreferrer"
            >
              mobile vault roadmap
            </a>
            ).
          </p>
        </section>
      </div>
    </main>
  );
}
