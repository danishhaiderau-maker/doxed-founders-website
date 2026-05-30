import Link from 'next/link';

const STEPS = [
  {
    title: 'Download & install',
    body: (
      <>
        Use the Windows <strong className="text-zinc-200">.exe</strong> or macOS{' '}
        <strong className="text-zinc-200">.dmg</strong> above. No Git or Node.js required.
        After install, Founder Node lives in your system tray.
      </>
    ),
  },
  {
    title: 'Generate a pairing code',
    body: (
      <>
        In Founder OS, open{' '}
        <Link href="/settings/builder" className="text-cyan-300 hover:underline">
          Builder settings
        </Link>
        . Under <strong className="text-zinc-200">Memory storage</strong>, choose{' '}
        <strong className="text-zinc-200">Founder Node</strong> and click{' '}
        <strong className="text-zinc-200">Generate pairing code</strong>.
      </>
    ),
  },
  {
    title: 'Pair this machine',
    body: (
      <>
        In the tray app, choose <strong className="text-zinc-200">Pair with Founder OS…</strong>, paste
        the code, and confirm the URL is{' '}
        <strong className="text-zinc-200">https://doxxedcrypto.digital</strong>. Sync starts
        automatically.
      </>
    ),
  },
  {
    title: 'Vault on disk',
    body: (
      <>
        Your files live at{' '}
        <code className="rounded bg-zinc-900 px-1.5 py-0.5">~/FounderVault/</code> — project context,
        roadmap, tasks, and pairing config. Full vault contents never leave your machine.
      </>
    ),
  },
] as const;

const TROUBLESHOOTING = [
  {
    q: 'SmartScreen or macOS blocked the app',
    a: 'The installer is not code-signed yet. On Windows: More info → Run anyway. On Mac: right-click the app → Open.',
  },
  {
    q: 'Pairing code invalid or expired',
    a: 'Codes are short-lived. Generate a fresh code in Builder settings and pair again within a few minutes.',
  },
  {
    q: 'Where is the tray icon?',
    a: 'Check hidden icons (Windows) or the menu bar (macOS). Relaunch Founder Node from Start Menu or Applications.',
  },
  {
    q: 'What gets synced?',
    a: 'Only metadata — project name, task counts, device label, heartbeat. Not your full roadmap or context files.',
  },
] as const;

export function FounderNodeSetupGuide() {
  return (
    <section className="rounded-xl border border-zinc-700 bg-zinc-950/50 p-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="font-semibold text-white">Setup guide (README)</h2>
          <p className="mt-1 text-sm text-zinc-500">
            Follow these steps when you click Founder Node from the nav — install, pair once, done.
          </p>
        </div>
        <a
          href="https://github.com/danishhaiderau-maker/doxed-founders-website/blob/master/apps/founder-node/README.md"
          target="_blank"
          rel="noopener noreferrer"
          className="shrink-0 text-sm text-cyan-300 hover:underline"
        >
          Full README on GitHub ↗
        </a>
      </div>

      <ol className="mt-6 space-y-5">
        {STEPS.map((step, i) => (
          <li key={step.title} className="flex gap-4 text-sm">
            <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-cyan-950 text-xs font-bold text-cyan-300 ring-1 ring-cyan-500/40">
              {i + 1}
            </span>
            <div>
              <p className="font-medium text-zinc-200">{step.title}</p>
              <p className="mt-1 leading-relaxed text-zinc-400">{step.body}</p>
            </div>
          </li>
        ))}
      </ol>

      <div className="mt-8 border-t border-zinc-800 pt-6">
        <h3 className="text-sm font-semibold text-zinc-300">Troubleshooting</h3>
        <dl className="mt-4 space-y-4">
          {TROUBLESHOOTING.map((item) => (
            <div key={item.q}>
              <dt className="text-sm font-medium text-zinc-200">{item.q}</dt>
              <dd className="mt-1 text-sm leading-relaxed text-zinc-500">{item.a}</dd>
            </div>
          ))}
        </dl>
      </div>
    </section>
  );
}
