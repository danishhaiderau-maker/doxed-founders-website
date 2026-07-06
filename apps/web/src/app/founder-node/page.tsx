import Link from 'next/link';
import { SiteBrand, SiteNav } from '@/components/site-nav';
import { FounderNodeDownloads, FOUNDER_NODE_GITHUB_RELEASES } from '@/components/founder-node-downloads';
import {
  FOUNDER_NODE_MIN_VERSION,
  FOUNDER_NODE_MIN_VERSION_LABEL,
} from '@/lib/founder-node-requirements';

export const metadata = {
  title: 'Founder Node — Desktop download & pairing',
  description:
    'Download Founder Node for Windows, macOS, or Linux. Pair once with Founder OS for encrypted vault sync, IDE bridge, and local AI.',
};

export default function FounderNodeDownloadPage() {
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
          <p className="text-[10px] font-bold uppercase tracking-[0.24em] text-cyan-400">Desktop vault</p>
          <h1 className="mt-2 text-3xl font-bold tracking-tight">Founder Node for Windows, macOS &amp; Linux</h1>
          <p className="mt-3 text-sm leading-relaxed text-zinc-400">
            Tray app for encrypted vault sync, Cursor IDE bridge, and local Ollama — your files stay on your PC. Pair
            once with the same Founder OS account you use in the browser or mobile app.
          </p>
          <p className="mt-2 text-sm text-zinc-500">
            Current release: <strong className="text-zinc-300">v{FOUNDER_NODE_MIN_VERSION}</strong> ·{' '}
            {FOUNDER_NODE_MIN_VERSION_LABEL} recommended for pairing, firewall fix, and hourly auto-updates.
          </p>
          <div className="mt-6">
            <FounderNodeDownloads showInstallGuide />
          </div>
          <div className="mt-5 flex flex-wrap gap-3">
            <Link
              href="/settings/builder"
              className="inline-flex rounded-xl bg-cyan-600 px-5 py-2.5 text-sm font-semibold text-white hover:bg-cyan-500"
            >
              Pair in Settings → Founder Node
            </Link>
            <Link
              href="/founder-den"
              className="inline-flex rounded-xl border border-zinc-600 px-5 py-2.5 text-sm font-semibold text-zinc-200 hover:border-zinc-500"
            >
              Open Founder OS →
            </Link>
          </div>
        </section>

        <section className="rounded-2xl border border-violet-500/25 bg-violet-950/15 p-6">
          <p className="text-[10px] font-bold uppercase tracking-[0.2em] text-violet-400">Pairing (about 2 minutes)</p>
          <h2 className="mt-2 text-xl font-bold">Connect desktop to Founder OS</h2>
          <ol className="mt-4 list-decimal space-y-2 pl-5 text-sm text-zinc-400">
            <li>
              Install Founder Node from the buttons above (Windows auto-updates from the tray menu).
            </li>
            <li>
              In <Link href="/settings/builder" className="text-violet-300 underline">Settings → Founder Node</Link>,
              choose <strong className="text-zinc-200">Founder Vault (Founder Node)</strong> and generate a pairing
              code.
            </li>
            <li>
              Tray icon → <strong className="text-zinc-200">Pair with Founder OS</strong> → paste the 8-character
              code (in the tray app, not the browser).
            </li>
            <li>
              Click <strong className="text-zinc-200">Rebuild vector index</strong> once — then IDE workspaces and vault
              search unlock in Development Workspace.
            </li>
          </ol>
          <p className="mt-4 text-xs text-zinc-500">
            Vault path: <code className="text-zinc-400">~/FounderVault/</code> — encrypted metadata sync only; plain-text
            notes stay local.
          </p>
        </section>

        <section className="rounded-2xl border border-zinc-800 bg-zinc-950/50 p-6 text-sm text-zinc-400">
          <h2 className="text-base font-semibold text-white">Looking for the phone app?</h2>
          <p className="mt-2 leading-relaxed">
            Android APK, Safari, and TestFlight live on the{' '}
            <Link href="/mobile" className="font-semibold text-emerald-300 underline hover:text-emerald-200">
              Founder OS Mobile
            </Link>{' '}
            page — separate from this desktop download.
          </p>
        </section>

        <section className="rounded-2xl border border-zinc-800 bg-zinc-950/50 p-6 text-xs text-zinc-500">
          <p>
            Release notes and older builds:{' '}
            <a href={FOUNDER_NODE_GITHUB_RELEASES} className="text-cyan-400 underline" target="_blank" rel="noreferrer">
              GitHub releases
            </a>
          </p>
        </section>
      </div>
    </main>
  );
}
