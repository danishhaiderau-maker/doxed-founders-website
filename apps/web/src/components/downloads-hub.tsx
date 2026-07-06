import Link from 'next/link';
import { AndroidAppDownloads } from '@/components/android-app-downloads';
import { IosAppDownloads } from '@/components/ios-app-downloads';
import {
  FounderNodeDownloads,
  FOUNDER_NODE_GITHUB_RELEASES,
} from '@/components/founder-node-downloads';
import {
  FOUNDER_NODE_MIN_VERSION,
  FOUNDER_NODE_MIN_VERSION_LABEL,
} from '@/lib/founder-node-requirements';

export function DownloadsHub() {
  return (
    <div className="mx-auto w-full max-w-3xl space-y-12 px-4 py-10 sm:px-6">
      <section className="rounded-2xl border border-zinc-800 bg-zinc-950/40 p-6">
        <p className="text-[10px] font-bold uppercase tracking-[0.24em] text-zinc-500">Install hub</p>
        <h1 className="mt-2 text-3xl font-bold tracking-tight">All downloads</h1>
        <p className="mt-3 text-sm leading-relaxed text-zinc-400">
          Everything you install lives here — mobile apps, desktop Founder Node, and pairing steps. Connect AI
          brains and deployment stack separately in{' '}
          <Link href="/settings/builder?tab=ai" className="font-semibold text-violet-300 underline hover:text-violet-200">
            AI Providers
          </Link>{' '}
          and{' '}
          <Link
            href="/settings/builder?tab=infra"
            className="font-semibold text-cyan-300 underline hover:text-cyan-200"
          >
            Infrastructure
          </Link>
          .
        </p>
        <nav className="mt-5 flex flex-wrap gap-2 text-xs" aria-label="Download sections">
          <a
            href="#mobile"
            className="rounded-full border border-emerald-500/30 bg-emerald-950/20 px-3 py-1.5 font-semibold text-emerald-200 hover:border-emerald-400/50"
          >
            Founder OS Mobile
          </a>
          <a
            href="#founder-node"
            className="rounded-full border border-cyan-500/30 bg-cyan-950/20 px-3 py-1.5 font-semibold text-cyan-200 hover:border-cyan-400/50"
          >
            Founder Node Desktop
          </a>
        </nav>
      </section>

      <section id="mobile" className="scroll-mt-24 space-y-8">
        <div>
          <p className="text-[10px] font-bold uppercase tracking-[0.24em] text-emerald-400">Founder OS Mobile</p>
          <h2 className="mt-2 text-2xl font-bold tracking-tight">Android APK &amp; iOS</h2>
          <p className="mt-3 text-sm leading-relaxed text-zinc-400">
            One app for Discover, trading, agents, and Founder OS — same account as{' '}
            <a href="https://doxxedcrypto.digital" className="text-cyan-300 underline">
              doxxedcrypto.digital
            </a>
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
        </div>

        <div id="ios" className="scroll-mt-24 rounded-2xl border border-sky-500/25 bg-sky-950/15 p-6">
          <p className="text-[10px] font-bold uppercase tracking-[0.2em] text-sky-400">iPhone &amp; iPad</p>
          <h3 className="mt-2 text-xl font-bold">Founder OS on iOS</h3>
          <p className="mt-2 text-sm text-zinc-400">
            Safari and Add to Home Screen work today. TestFlight native app uses the same unified shell as Android.
          </p>
          <div className="mt-5">
            <IosAppDownloads />
          </div>
        </div>

        <div className="rounded-2xl border border-amber-500/20 bg-amber-950/10 p-6 text-sm text-zinc-300">
          <h3 className="text-base font-semibold text-amber-100">Why is the APK only ~4&nbsp;MB?</h3>
          <p className="mt-2 leading-relaxed text-zinc-400">
            That size is <strong className="text-white">correct</strong>. The install file is a thin{' '}
            <strong className="text-white">WebView shell</strong> — it loads the live site after you open the app.
            AI keys, Founder Node, and vault files are configured in the browser, not bundled in the APK.
          </p>
        </div>

        <div className="rounded-2xl border border-amber-500/25 bg-amber-950/15 p-6 text-sm text-zinc-300">
          <h3 className="text-base font-semibold text-amber-100">Phone requirements &amp; Play Protect</h3>
          <ul className="mt-3 list-disc space-y-2 pl-5 text-zinc-400">
            <li>
              <strong className="text-white">Minimum:</strong> Android 7.0 (API 23) ·{' '}
              <strong className="text-white">Recommended:</strong> Android 8.0+ ·{' '}
              <strong className="text-white">Best:</strong> Android 10+
            </li>
            <li>
              <strong className="text-white">Play Protect:</strong> v0.4.2+ is release-signed. If blocked, tap{' '}
              <strong className="text-white">Install anyway</strong>.
            </li>
          </ul>
        </div>

        <p className="text-xs text-zinc-500">
          After install, pair your phone vault in{' '}
          <Link href="/settings/builder?tab=downloads" className="text-violet-300 underline">
            Settings → Downloads &amp; pairing
          </Link>
          . AI brains:{' '}
          <Link href="/settings/builder?tab=ai" className="text-violet-300 underline">
            AI Providers
          </Link>
          .
        </p>
      </section>

      <section id="founder-node" className="scroll-mt-24 space-y-8 border-t border-zinc-800/80 pt-12">
        <div>
          <p className="text-[10px] font-bold uppercase tracking-[0.24em] text-cyan-400">Desktop vault</p>
          <h2 className="mt-2 text-2xl font-bold tracking-tight">Founder Node for Windows, macOS &amp; Linux</h2>
          <p className="mt-3 text-sm leading-relaxed text-zinc-400">
            Tray app for encrypted vault sync, Cursor IDE bridge, and local Ollama — your files stay on your PC. Pair
            once with the same Founder OS account you use in the browser or mobile app.
          </p>
          <p className="mt-2 text-sm text-zinc-500">
            Current release: <strong className="text-zinc-300">v{FOUNDER_NODE_MIN_VERSION}</strong> ·{' '}
            {FOUNDER_NODE_MIN_VERSION_LABEL} recommended for pairing, firewall fix, and hourly auto-updates.
          </p>
          <div className="mt-6">
            <FounderNodeDownloads showInstallGuide sectionId="founder-node-download" />
          </div>
          <div className="mt-5 flex flex-wrap gap-3">
            <Link
              href="/settings/builder?tab=downloads"
              className="inline-flex rounded-xl bg-cyan-600 px-5 py-2.5 text-sm font-semibold text-white hover:bg-cyan-500"
            >
              Pair in Settings →
            </Link>
            <Link
              href="/founder-den"
              className="inline-flex rounded-xl border border-zinc-600 px-5 py-2.5 text-sm font-semibold text-zinc-200 hover:border-zinc-500"
            >
              Open Founder OS →
            </Link>
          </div>
        </div>

        <div className="rounded-2xl border border-violet-500/25 bg-violet-950/15 p-6">
          <p className="text-[10px] font-bold uppercase tracking-[0.2em] text-violet-400">Pairing (about 2 minutes)</p>
          <h3 className="mt-2 text-xl font-bold">Connect desktop to Founder OS</h3>
          <ol className="mt-4 list-decimal space-y-2 pl-5 text-sm text-zinc-400">
            <li>Install Founder Node from the buttons above (Windows auto-updates from the tray menu).</li>
            <li>
              In{' '}
              <Link href="/settings/builder?tab=downloads" className="text-violet-300 underline">
                Settings → Downloads &amp; pairing
              </Link>
              , choose <strong className="text-zinc-200">Founder Vault (Founder Node)</strong> and generate a pairing
              code.
            </li>
            <li>
              Tray icon → <strong className="text-zinc-200">Pair with Founder OS</strong> → paste the 8-character
              code in the tray app.
            </li>
            <li>
              Click <strong className="text-zinc-200">Rebuild vector index</strong> once — then IDE workspaces unlock.
            </li>
          </ol>
          <p className="mt-4 text-xs text-zinc-500">
            Vault path: <code className="text-zinc-400">~/FounderVault/</code> — encrypted metadata sync only.
          </p>
        </div>

        <div className="rounded-2xl border border-zinc-800 bg-zinc-950/50 p-6 text-xs text-zinc-500">
          <p>
            Release notes:{' '}
            <a href={FOUNDER_NODE_GITHUB_RELEASES} className="text-cyan-400 underline" target="_blank" rel="noreferrer">
              GitHub releases
            </a>
            . Local Ollama setup:{' '}
            <Link href="/settings/builder?tab=ai" className="text-violet-300 underline">
              AI Providers
            </Link>
            . Deploy stack:{' '}
            <Link href="/settings/builder?tab=infra" className="text-cyan-300 underline">
              Infrastructure
            </Link>
            .
          </p>
        </div>
      </section>
    </div>
  );
}
