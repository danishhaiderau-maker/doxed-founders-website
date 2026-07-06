import Link from 'next/link';
import { SiteBrand, SiteNav } from '@/components/site-nav';
import { AndroidAppDownloads } from '@/components/android-app-downloads';
import { IosAppDownloads } from '@/components/ios-app-downloads';

export const metadata = {
  title: 'Founder OS Mobile — Android & iOS',
  description:
    'Install the Doxxed Crypto Android app or use Founder OS in Safari. Discover, trading, agents, and Founder OS in your pocket.',
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
          <p className="text-[10px] font-bold uppercase tracking-[0.24em] text-emerald-400">Founder OS Mobile</p>
          <h1 className="mt-2 text-3xl font-bold tracking-tight">Doxxed Crypto for Android &amp; iOS</h1>
          <p className="mt-3 text-sm leading-relaxed text-zinc-400">
            One app for Discover, trading, agents, and Founder OS — same account as{' '}
            <a href="https://doxxedcrypto.digital" className="text-cyan-300 underline">
              doxxedcrypto.digital
            </a>
            .
          </p>
          <p className="mt-2 text-sm text-zinc-500">
            Need the desktop vault tray app? See{' '}
            <Link href="/founder-node" className="font-semibold text-cyan-300 underline hover:text-cyan-200">
              Founder Node for Windows, macOS &amp; Linux
            </Link>
            .
          </p>
          <div className="mt-6">
            <AndroidAppDownloads variant="hero" showInstallGuide />
          </div>
          <Link
            href="/founder-den?onboard=byo"
            className="mt-4 inline-flex rounded-xl bg-violet-600 px-5 py-2.5 text-sm font-semibold text-white hover:bg-violet-500"
          >
            Start Founder OS on phone →
          </Link>
        </section>

        <section className="rounded-2xl border border-sky-500/25 bg-sky-950/15 p-6">
          <p className="text-[10px] font-bold uppercase tracking-[0.2em] text-sky-400">iPhone &amp; iPad</p>
          <h2 className="mt-2 text-xl font-bold">Founder OS on iOS</h2>
          <p className="mt-2 text-sm text-zinc-400">
            Safari and Add to Home Screen work today. TestFlight native app uses the same unified shell as Android.
          </p>
          <div className="mt-5">
            <IosAppDownloads />
          </div>
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
              <strong className="text-white">Play Protect / Pixel:</strong> v0.4.2+ is{' '}
              <strong className="text-white">release-signed</strong>. If blocked, tap{' '}
              <strong className="text-white">More details → Install anyway</strong>. Advanced Protection may block
              sideloads entirely — use Chrome at doxxedcrypto.digital.
            </li>
            <li>
              <strong className="text-white">Android 6–7:</strong> update Android System WebView from Play Store; white
              screen otherwise.
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
            . Desktop vault + Ollama:{' '}
            <Link href="/founder-node" className="text-cyan-300 underline">
              Founder Node download
            </Link>
            .
          </p>
        </section>
      </div>
    </main>
  );
}
