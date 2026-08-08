import type { Metadata } from 'next';
import Link from 'next/link';
import { Download, Smartphone } from 'lucide-react';
import { SiteBrand, SiteNav } from '@/components/site-nav';

export const metadata: Metadata = {
  title: 'Download the app — Doxxed crypto',
  description:
    'Install the Doxxed crypto mobile app on Android, or use the iOS web app until TestFlight ships.',
};

/** Canonical APK path for the unified Capacitor shell (written by `npm run pack:android`). */
const ANDROID_APK_HREF = '/downloads/doxxedcrypto-android.apk';

/**
 * Unified mobile download hub. Nav “App” control → here.
 * Android APK is the Capacitor complete-package shell (v0.5+).
 */
export default function MobileAppHubPage() {
  return (
    <main className="min-h-screen bg-[#050508] text-zinc-100">
      <header className="border-b border-zinc-800">
        <div className="mx-auto flex max-w-5xl flex-wrap items-center justify-between gap-4 px-6 py-5">
          <div>
            <SiteBrand className="text-sm" />
            <h1 className="mt-1 text-2xl font-bold text-white">Mobile app</h1>
            <p className="text-sm text-zinc-400">
              One Capacitor shell for landing, chat, agents, account, and Founder IDE web —
              Android APK now, iOS TestFlight next.
            </p>
          </div>
          <SiteNav />
        </div>
      </header>

      <div className="mx-auto max-w-5xl px-6 py-12">
        <section className="mb-10 max-w-2xl">
          <p className="text-[11px] font-bold uppercase tracking-[0.18em] text-emerald-400/90">
            Download hub
          </p>
          <h2 className="mt-2 text-3xl font-bold tracking-tight text-white sm:text-4xl">
            Take Doxxed crypto with you
          </h2>
          <p className="mt-3 text-base text-zinc-400">
            The mobile app is a lightweight Capacitor shell around the live site — chat, agent hub,
            account, Founder IDE (web), and vault pairing. Desktop Founder Node / Void stay separate
            and are linked from Founder IDE.
          </p>
        </section>

        <div className="grid gap-6 md:grid-cols-2">
          <section
            id="android"
            className="scroll-mt-24 rounded-2xl border border-emerald-500/25 bg-zinc-950/50 p-6"
          >
            <div className="flex flex-wrap items-center gap-2">
              <Smartphone className="h-5 w-5 text-emerald-300" aria-hidden />
              <h3 className="text-lg font-semibold text-white">Android</h3>
              <span className="rounded-full border border-emerald-500/40 bg-emerald-500/10 px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide text-emerald-200">
                APK ready
              </span>
            </div>
            <p className="mt-2 text-sm text-zinc-400">
              Sideload the unified Capacitor APK. Allow install from your browser if prompted; on Play
              Protect warnings use <span className="text-zinc-200">More details → Install anyway</span>.
            </p>
            <a
              href={ANDROID_APK_HREF}
              download="doxxedcrypto-android.apk"
              className="mt-5 inline-flex items-center gap-2 rounded-lg border border-emerald-500/40 bg-emerald-500/15 px-4 py-2.5 text-sm font-semibold text-emerald-100 transition hover:bg-emerald-500/25 hover:text-white"
            >
              <Download className="h-4 w-4" aria-hidden />
              Download Android APK
            </a>
            <p className="mt-3 text-xs text-zinc-500">
              File:{' '}
              <code className="text-zinc-400">/downloads/doxxedcrypto-android.apk</code>
              . Rebuild with <code className="text-zinc-400">npm run pack:android</code>.
            </p>
            <ul className="mt-4 space-y-1.5 text-xs text-zinc-500">
              <li>Min Android 7.0 · recommended 8+</li>
              <li>After install, pair vault from Settings → Code for Android</li>
            </ul>
          </section>

          <section
            id="ios"
            className="scroll-mt-24 rounded-2xl border border-zinc-700/70 bg-zinc-950/50 p-6"
          >
            <div className="flex flex-wrap items-center gap-2">
              <Smartphone className="h-5 w-5 text-zinc-300" aria-hidden />
              <h3 className="text-lg font-semibold text-white">iOS</h3>
              <span className="rounded-full border border-zinc-600/80 px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide text-zinc-400">
                Coming soon
              </span>
            </div>
            <p className="mt-2 text-sm text-zinc-400">
              Native TestFlight is next. Until then, use Safari and Add to Home Screen for a
              full-screen web app with the same account.
            </p>
            <ol className="mt-5 list-decimal space-y-2 pl-5 text-sm text-zinc-300">
              <li>
                Open{' '}
                <Link href="/" className="font-medium text-emerald-300 hover:underline">
                  doxxedcrypto.digital
                </Link>{' '}
                in Safari
              </li>
              <li>Tap Share → Add to Home Screen</li>
              <li>Launch from the icon — sign in as usual</li>
            </ol>
            <p className="mt-4 text-xs text-zinc-500">
              TestFlight will be listed here when the iOS Capacitor target ships.
            </p>
          </section>
        </div>

        <p className="mt-10 text-center text-sm text-zinc-500">
          Prefer desktop? Pair Founder Node from{' '}
          <Link href="/founder-ide" className="text-emerald-300/90 hover:underline">
            Founder IDE
          </Link>
          .
        </p>
      </div>
    </main>
  );
}
