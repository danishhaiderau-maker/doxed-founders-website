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
          <p className="text-[10px] font-bold uppercase tracking-[0.24em] text-emerald-400">Phase 3 — Android vault</p>
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

        <section className="rounded-2xl border border-amber-500/20 bg-amber-950/10 p-6 text-sm text-zinc-300">
          <h2 className="text-base font-semibold text-amber-100">Why is the APK only ~4&nbsp;MB?</h2>
          <p className="mt-2 leading-relaxed text-zinc-400">
            That size is <strong className="text-white">correct</strong>. The install file is a thin{' '}
            <strong className="text-white">WebView shell</strong> (icon + launcher + secure browser). It does{' '}
            <strong className="text-white">not</strong> bundle the whole website, LLMs, Founder Node, or your vault
            files — those load from{' '}
            <a href="https://doxxedcrypto.digital" className="text-cyan-300 underline">
              doxxedcrypto.digital
            </a>{' '}
            after you open the app (same as mobile Chrome, with an app icon).
          </p>
          <p className="mt-3 text-xs text-zinc-500">
            Founder OS <strong className="text-zinc-400">settings you see inside the app</strong> (pairing, AI stack,
            cloud sync) are the live website — not extra code inside the APK.{' '}
            <strong className="text-zinc-400">Local vault files on the phone</strong> are Phase&nbsp;3 and will increase
            APK size when we ship native sync.
          </p>
        </section>

        <section className="rounded-2xl border border-amber-500/25 bg-amber-950/15 p-6 text-sm text-zinc-300">
          <h2 className="text-base font-semibold text-amber-100">Phone requirements &amp; Play Protect</h2>
          <ul className="mt-3 list-disc space-y-2 pl-5 text-zinc-400">
            <li>
              <strong className="text-white">Minimum:</strong> Android 7.0 (API 23) ·{' '}
              <strong className="text-white">Recommended:</strong> Android 8.0+ ·{' '}
              <strong className="text-white">Best:</strong> Android 10+ (Pixel 8, Samsung, etc.)
            </li>
            <li>
              <strong className="text-white">Play Protect / Pixel:</strong> v0.4.2+ is <strong className="text-white">release-signed</strong>.
              If blocked, tap <strong className="text-white">More details → Install anyway</strong>. Advanced Protection may
              block sideloads entirely — use Chrome at doxxedcrypto.digital.
            </li>
            <li>
              <strong className="text-white">Android 6–7:</strong> update Android System WebView from Play Store; white screen
              otherwise.
            </li>
          </ul>
        </section>

        <section className="rounded-2xl border border-zinc-800 bg-zinc-950/50 p-6 text-sm text-zinc-400">
          <h2 className="text-base font-semibold text-white">What the Android app includes today</h2>
          <ul className="mt-3 list-disc space-y-1 pl-5">
            <li>Discover, projects, Trust Center, agent hub</li>
            <li>Paper trading, predictions, watchlist, portfolio</li>
            <li>Founder OS, Builder settings, Copilot (cloud AI / Phala when configured)</li>
          </ul>
          <p className="mt-4 text-xs text-zinc-500">
            Pair mobile vault in Settings → Memory storage. Two-way PC↔phone merge is Phase 4 — see{' '}
            <a
              href="https://github.com/danishhaiderau-maker/doxed-founders-website/blob/master/docs/MOBILE_VAULT_ROADMAP.md"
              className="text-cyan-300 underline"
              target="_blank"
              rel="noreferrer"
            >
              roadmap
            </a>
            .
          </p>
        </section>
      </div>
    </main>
  );
}
