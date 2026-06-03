'use client';

import Link from 'next/link';

type Props = {
  githubTokenConnected?: boolean;
  repoLinked?: string | null;
};

export function GitHubPatConnectGuide({ githubTokenConnected, repoLinked }: Props) {
  return (
    <div className="mt-4 space-y-4 text-sm text-zinc-300">
      <div className="rounded-lg border border-zinc-700/80 bg-zinc-900/40 p-4">
        <p className="font-semibold text-white">Why connect GitHub?</p>
        <ul className="mt-2 list-inside list-disc space-y-1.5 text-xs text-zinc-400">
          <li>
            <strong className="text-zinc-200">Private repos</strong> — without a token, GitHub hides commits, PRs, and
            issues from our API. Public repos work for basic sync; private repos need you to authorize access.
          </li>
          <li>
            <strong className="text-zinc-200">Build feed & Copilot</strong> — we read recent{' '}
            <em>commit messages</em> (not your full source tree) to suggest build updates and answer “what changed this
            week?”
          </li>
          <li>
            <strong className="text-zinc-200">Agents → GitHub issues</strong> — Product Manager / Builder can{' '}
            <em>publish</em> queued tasks as real issues on your linked repo (optional toggle in settings).
          </li>
          <li>
            <strong className="text-zinc-200">Founder OS memory files</strong> — we sync small markdown/JSON files under{' '}
            <code className="text-violet-300">.github/founder-os/</code> in your repo (goal, roadmap, task list) so
            Cursor and Copilot stay aligned when your laptop is off.
          </li>
        </ul>
      </div>

      <div className="rounded-lg border border-cyan-500/25 bg-cyan-950/15 p-4">
        <p className="font-semibold text-cyan-100">How to connect (two steps)</p>
        <ol className="mt-2 list-inside list-decimal space-y-2 text-xs text-cyan-100/85">
          <li>
            <strong className="text-cyan-50">Link your repository</strong> in{' '}
            <Link href="/founder-den?tab=build" className="font-medium text-emerald-400 underline hover:text-white">
              Founder Copilot (Mission Control → Build)
            </Link>
            : enter <code className="text-cyan-200">owner/repo</code> (e.g.{' '}
            <code className="text-cyan-200">you/my-startup</code>) and click connect, or use{' '}
            <strong>Connect with GitHub</strong> OAuth there first.
            {repoLinked ? (
              <span className="mt-1 block text-emerald-300">✓ Linked: {repoLinked}</span>
            ) : (
              <span className="mt-1 block text-amber-200/90">
                Repo not linked yet — do this before saving a token below.
              </span>
            )}
          </li>
          <li>
            <strong className="text-cyan-50">Create a Personal Access Token (PAT)</strong> on GitHub:
            <ul className="mt-1.5 ml-4 list-disc space-y-1 text-cyan-100/75">
              <li>
                GitHub → Settings → Developer settings →{' '}
                <a
                  href="https://github.com/settings/tokens"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="underline hover:text-white"
                >
                  Personal access tokens
                </a>{' '}
                → <em>Fine-grained</em> or <em>Classic</em>
              </li>
              <li>
                Scope: <strong>repo</strong> (classic) or repository access with Contents + Issues read/write
                (fine-grained) for the repos you trust
              </li>
              <li>Paste the token below → <strong>Save token</strong>. We encrypt it before storing.</li>
            </ul>
          </li>
        </ol>
        {githubTokenConnected && (
          <p className="mt-3 text-xs text-emerald-300">✓ Token saved — you can update or remove it anytime.</p>
        )}
      </div>

      <div className="rounded-lg border border-emerald-500/30 bg-emerald-950/20 p-4">
        <p className="font-semibold text-emerald-200">Privacy — what we access vs what stays yours</p>
        <div className="mt-2 space-y-2 text-xs text-emerald-100/90">
          <p>
            <strong className="text-emerald-100">Honest answer:</strong> a GitHub <code>repo</code> token{' '}
            <em>can</em> read private repository content through GitHub&apos;s API. We do{' '}
            <strong className="text-emerald-50">not</strong> bulk-download your codebase, scan every file, or use your
            private source for platform-wide training.
          </p>
          <p>
            <strong className="text-emerald-100">What our servers actually request</strong> for your linked repo only:
          </p>
          <ul className="ml-4 list-disc space-y-1">
            <li>Recent commit metadata (message, date, short SHA)</li>
            <li>Pull request titles and status</li>
            <li>Files under <code className="text-emerald-200">.github/founder-os/</code> that Founder OS creates or
              updates (project context, roadmap, tasks, decisions)</li>
            <li>Creating issues when you or an agent explicitly publishes a task</li>
          </ul>
          <p>
            <strong className="text-emerald-100">What stays private by design</strong>
          </p>
          <ul className="ml-4 list-disc space-y-1">
            <li>
              <strong>Founder Node vault</strong> (private notes, full task bodies, local markdown) — encrypted on your
              PC; we only receive optional metadata snapshots, not decrypted vault files (
              <Link href="/settings/builder" className="underline hover:text-white">
                Founder Vault mode
              </Link>
              ).
            </li>
            <li>Other folders in your repo — we do not crawl <code>src/</code>, <code>.env</code>, etc. unless you
              later opt into a feature that reads a specific path.</li>
            <li>Your token — stored encrypted; used only for your account; remove it here or revoke on GitHub to cut
              access immediately.</li>
          </ul>
          <p className="text-emerald-200/80">
            If you need stricter isolation (e.g. no server-side GitHub token at all), use Founder Node-only workflow and
            public repos for build-in-public — we can add finer-scoped GitHub App permissions in a future release.
          </p>
        </div>
      </div>
    </div>
  );
}
