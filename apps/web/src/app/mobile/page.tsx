import type { Metadata } from 'next';
import Link from 'next/link';
import { Smartphone } from 'lucide-react';
import { SiteBrand, SiteNav } from '@/components/site-nav';

export const metadata: Metadata = {
  title: 'Download the app — Doxxed crypto',
  description:
    'Get the Doxxed crypto mobile app on Android and iOS. Unified shell for Founder OS — APK and TestFlight land here.',
};

/**
 * Unified mobile download hub. Nav “App” control points here.
 * Do not link retired/legacy APK paths — sibling agents publish the new build here when ready.
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
              One unified shell for Founder OS — Android and iOS downloads live here.
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
            The mobile app is a lightweight native shell around the live site — Discover, chat,
            vault pairing, and Founder OS — without a second product listing.
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
                APK soon
              </span>
            </div>
            <p className="mt-2 text-sm text-zinc-400">
              A fresh unified Android APK is being prepared. When it publishes, the download button
              will appear on this page — we are not linking retired builds.
            </p>
            <div className="mt-5 rounded-xl border border-zinc-800 bg-zinc-900/60 px-4 py-3 text-sm text-zinc-300">
              <p className="font-medium text-white">Meanwhile</p>
              <p className="mt-1 text-zinc-400">
                Open the full site in Chrome on your phone, or pair from{' '}
                <Link href="/founder-ide" className="font-medium text-emerald-300 hover:underline">
                  Founder IDE
                </Link>{' '}
                on desktop.
              </p>
            </div>
            <ul className="mt-4 space-y-1.5 text-xs text-zinc-500">
              <li>Target: release-signed sideload APK (Play Protect friendly)</li>
              <li>After install: pair vault via Settings → Code for Android</li>
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
              TestFlight will be listed here when the iOS Capacitor target ships. No App Store
              listing yet.
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
