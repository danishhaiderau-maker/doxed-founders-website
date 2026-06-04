'use client';

import Link from 'next/link';

export function PlatformSetupGuide() {
  return (
    <div className="rounded-xl border border-violet-500/30 bg-violet-950/15 p-5 text-sm text-zinc-300">
      <h3 className="text-base font-semibold text-white">How to use Doxxed Crypto without issues</h3>
      <p className="mt-1 text-xs text-zinc-500">
        Follow this order once — then Mission Control, agents, and Copilot stay in sync.
      </p>

      <ol className="mt-4 space-y-4 list-none">
        <li>
          <p className="font-medium text-violet-200">1. GitHub + project</p>
          <p className="mt-1 text-xs text-zinc-400">
            In{' '}
            <Link href="/founder-den" className="text-emerald-400 underline">
              Mission Control
            </Link>{' '}
            link your repo. Founder OS reads public commits and build feed — not your private vault files.
          </p>
        </li>
        <li>
          <p className="font-medium text-violet-200">2. Founder Node + vault (optional, recommended)</p>
          <p className="mt-1 text-xs text-zinc-400">
            Download Founder Node, choose <strong>Founder Vault</strong> storage, generate a pairing code{' '}
            <em>only until paired</em>. After success the code disappears — pairing is permanent until you
            disconnect. Full notes, roadmap, and private context stay encrypted on your PC; we only receive
            tiny metadata snapshots (goal, progress, task counts).
          </p>
        </li>
        <li>
          <p className="font-medium text-violet-200">3. AI on your stack — the “brain”</p>
          <p className="mt-1 text-xs text-zinc-400">
            Connect at least one <strong>LLM</strong> (Jatevo $JTVO gateway, OpenRouter, DeepSeek, Ollama via Node,
            Phala TEE). That
            model powers Copilot <strong>Ask</strong> and every{' '}
            <Link href="/founder-den?tab=agents" className="text-emerald-400 underline">
              project agent
            </Link>{' '}
            when they think, reply to community questions, or draft marketing. Platform code templates handle
            structure; your API key pays inference and keeps prompts on your chosen provider.
          </p>
        </li>
        <li>
          <p className="font-medium text-violet-200">4. Code agent (optional)</p>
          <p className="mt-1 text-xs text-zinc-400">
            <strong>Cursor</strong> or <strong>OpenHands</strong> is separate from the brain — it edits your
            GitHub repo. In Copilot use <strong>Run in Cursor</strong>; output streams in Mission Control.
          </p>
        </li>
        <li>
          <p className="font-medium text-violet-200">5. Project agents (loyal to your project)</p>
          <p className="mt-1 text-xs text-zinc-400">
            Each listed project gets the same agent roster (Community, Marketing, Builder, etc.). When run from{' '}
            <em>your</em> project profile, agents only promote, defend, and mediate for that project — using your
            connected LLM.             See repo doc <code className="text-violet-300">docs/PROJECT_AGENT_ARCHITECTURE.md</code> for the full
            loyalty + LLM brain model.
          </p>
        </li>
        <li>
          <p className="font-medium text-violet-200">6. Ship in public</p>
          <p className="mt-1 text-xs text-zinc-400">
            Publish build updates, enable Autopilot in Remote builder agents if you want hands-free sync +
            deploy. Say &quot;take full control&quot; in Copilot when stack tokens are connected.
          </p>
        </li>
      </ol>

      <div className="mt-4 rounded-lg border border-emerald-500/25 bg-emerald-950/20 p-3 text-xs text-emerald-100/90">
        <p className="font-semibold text-emerald-200">Privacy in one sentence</p>
        <p className="mt-1">
          With Founder Vault + encrypted relay, Doxxed Crypto cannot read what you are building in private notes or
          full vault files — only metadata you choose to sync. Public GitHub and feed posts are visible by design
          (build in public).
        </p>
      </div>
    </div>
  );
}
