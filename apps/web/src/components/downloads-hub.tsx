import Link from 'next/link';
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
        <h1 className="mt-2 text-3xl font-bold tracking-tight">Founder Stack downloads</h1>
        <p className="mt-3 text-sm leading-relaxed text-zinc-400">
          The Founder Stack desktop kit (Founder IDE + Founder Node) is the only install you need. Connect AI
          brains and infrastructure inside{' '}
          <Link href="/founder-ide" className="font-semibold text-violet-300 underline hover:text-violet-200">
            Founder IDE
          </Link>
          .
        </p>
        <nav className="mt-5 flex flex-wrap gap-2 text-xs" aria-label="Download sections">
          <a
            href="#founder-node"
            className="rounded-full border border-cyan-500/30 bg-cyan-950/20 px-3 py-1.5 font-semibold text-cyan-200 hover:border-cyan-400/50"
          >
            Founder Stack &amp; Founder Node
          </a>
        </nav>
      </section>

      <section id="founder-node" className="scroll-mt-24 space-y-8 border-t border-zinc-800/80 pt-12">
        <div>
          <p className="text-[10px] font-bold uppercase tracking-[0.24em] text-cyan-400">Desktop vault + IDE</p>
          <h2 className="mt-2 text-2xl font-bold tracking-tight">Founder Stack — one desktop kit</h2>
          <p className="mt-3 text-sm leading-relaxed text-zinc-400">
            <strong className="text-emerald-200">Founder Stack</strong> is the primary desktop install: Founder IDE
            (VS Code-based editor with Founder OS AI) plus the Founder Node tray (vault sync, pairing, local Ollama).
            On Windows, use the single <strong className="text-emerald-200">Founder Stack</strong> Desktop launcher —
            it starts the Node tray (if needed) then the IDE.{" "}
            <strong className="text-cyan-200">Founder Node</strong> standalone remains available for vault-only setups
            (no IDE). Pair once with the same Founder OS account you use in the browser or mobile app.
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
              href="/founder-ide"
              className="inline-flex rounded-xl bg-cyan-600 px-5 py-2.5 text-sm font-semibold text-white hover:bg-cyan-500"
            >
              Open Founder IDE →
            </Link>
          </div>
        </div>

        <div className="rounded-2xl border border-violet-500/25 bg-violet-950/15 p-6">
          <p className="text-[10px] font-bold uppercase tracking-[0.2em] text-violet-400">Pairing (about 2 minutes)</p>
          <h3 className="mt-2 text-xl font-bold">Connect desktop to Founder OS</h3>
          <ol className="mt-4 list-decimal space-y-2 pl-5 text-sm text-zinc-400">
            <li>Install Founder Stack (or Founder Node standalone) from the buttons above (Windows auto-updates from the tray menu).</li>
            <li>
              In{' '}
              <Link href="/founder-ide" className="text-violet-300 underline">
                Founder IDE
              </Link>
              , choose <strong className="text-zinc-200">Pair your device</strong> and generate a pairing code.
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
            . Connect AI keys and deployment infrastructure inside{' '}
            <Link href="/founder-ide" className="text-violet-300 underline">
              Founder IDE
            </Link>
            .
          </p>
        </div>
      </section>
    </div>
  );
}
