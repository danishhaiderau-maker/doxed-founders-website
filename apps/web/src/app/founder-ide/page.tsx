'use client';

import { Suspense, useCallback, useEffect, useState } from 'react';
import { useSession } from 'next-auth/react';
import Link from 'next/link';
import { SiteBrand, SiteNav } from '@/components/site-nav';
import { FounderIdePair } from '@/components/founder-ide-pair';
import { FounderIdeChat } from '@/components/founder-ide-chat';
import { fetchFounderNodeStatus } from '@/lib/api';

export default function FounderIdePage() {
  const { data: session } = useSession();
  const [showPair, setShowPair] = useState(false);
  // Post-pairing chat dispatch UX — non-null when a paired node exists.
  // On mount we check the user's node status so page reloads land in the chat
  // state instead of the pairing block.
  const [pairedNodeId, setPairedNodeId] = useState<string | null>(null);
  const [checkedPair, setCheckedPair] = useState(false);

  const checkPair = useCallback(async () => {
    if (!session?.accessToken) {
      setCheckedPair(true);
      return;
    }
    try {
      const status = await fetchFounderNodeStatus(session.accessToken);
      const first = status.nodes?.[0];
      if (first) setPairedNodeId(first.nodeId);
    } catch {
      // ignore — user may not be signed in or backend down; show landing.
    } finally {
      setCheckedPair(true);
    }
  }, [session?.accessToken]);

  useEffect(() => {
    void checkPair();
  }, [checkPair]);

  const handlePaired = useCallback((nodeId: string) => {
    setPairedNodeId(nodeId);
    // Smooth-scroll the chat dispatch panel into view after the transition.
    setTimeout(() => {
      document.getElementById('founder-ide-chat')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }, 100);
  }, []);

  return (
    <main className="min-h-screen bg-[#050508] text-zinc-100">
      {/* LAUNCH PROMO RIBBON */}
      <div className="w-full border-b border-violet-700/40 bg-gradient-to-r from-violet-700/30 via-violet-600/20 to-emerald-700/20">
        <div className="mx-auto flex max-w-6xl flex-col items-center gap-1 px-6 py-3 text-center sm:flex-row sm:justify-center sm:gap-6 sm:text-left">
          <p className="text-sm font-semibold text-white">
            <span className="mr-2 rounded bg-violet-600 px-2 py-0.5 text-xs uppercase tracking-wide">Launch Promo</span>
            10 BYOK models on Free. Cursor only gives you 1.
          </p>
          <p className="text-xs text-zinc-300">
            Bring keys from OpenAI, Anthropic, Google, DeepSeek, GLM, Mistral, Groq, xAI, Cohere, Together — all 10 work on the Free plan.
          </p>
        </div>
      </div>

      <header className="border-b border-zinc-800">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-4 px-6 py-5">
          <div>
            <SiteBrand className="text-sm" />
            <h1 className="mt-1 text-3xl font-bold text-white">Founder IDE</h1>
            <p className="text-sm text-zinc-400">
              Your sovereign AI coding environment. Runs locally. Your compute, your models, your code.
            </p>
          </div>
          <SiteNav />
        </div>
      </header>

      <div className="mx-auto max-w-6xl px-6 py-12">
        {/* HERO */}
        <section className="mb-16 text-center">
          <h2 className="text-4xl font-bold tracking-tight text-white sm:text-5xl">
            Code with your own AI. Locally.
          </h2>
          <p className="mt-4 text-lg text-zinc-400">
            Founder IDE runs on your laptop, uses your own API keys or local models,
            and pairs with doxxedcrypto.digital so you can drive it from anywhere.
          </p>
        </section>

        {/* DOWNLOAD SECTION */}
        <section className="mb-16">
          <div className="rounded-2xl border border-zinc-800 bg-zinc-950/50 p-8">
            <h3 className="text-xl font-semibold text-white">Get Founder IDE</h3>
            <p className="mt-2 text-sm text-zinc-400">Latest version · Windows 10/11 · ~850 MB installer</p>

            <div className="mt-6 flex flex-wrap gap-3">
              <a
                href="https://github.com/danishhaiderau-maker/founder-next/releases/latest"
                className="rounded-lg bg-violet-600 px-6 py-3 text-sm font-semibold text-white hover:bg-violet-500"
                target="_blank"
                rel="noreferrer"
              >
                Download for Windows
              </a>
              <button
                type="button"
                onClick={() => setShowPair(true)}
                className="rounded-lg border border-zinc-700 px-6 py-3 text-sm font-semibold text-zinc-200 hover:border-zinc-500"
              >
                I already have it — Pair my device
              </button>
            </div>

            <p className="mt-4 text-xs text-zinc-500">
              Mac and Linux: not yet supported. The Windows installer is signed with Ed25519 manifest verification.
            </p>
          </div>
        </section>

        {/* PAIR SECTION */}
        {showPair && !pairedNodeId && (
          <section className="mb-16" id="pair">
            <div className="rounded-2xl border border-emerald-900/40 bg-emerald-950/10 p-8">
              <h3 className="text-xl font-semibold text-white">Pair your device</h3>
              <p className="mt-2 text-sm text-zinc-400">
                Generate a pairing code here, then paste it into Founder IDE → Settings → Founder Node → Pair.
              </p>
              <div className="mt-6">
                {session?.accessToken ? (
                  <Suspense fallback={<p className="text-sm text-zinc-500">Loading…</p>}>
                    <FounderIdePair accessToken={session.accessToken} onPaired={handlePaired} />
                  </Suspense>
                ) : (
                  <div className="rounded-lg border border-amber-500/30 bg-amber-950/15 p-4 text-sm text-amber-100">
                    <Link href="/login?callbackUrl=/founder-ide" className="font-semibold underline">
                      Sign in
                    </Link>{' '}
                    to generate a pairing code.
                  </div>
                )}
              </div>
            </div>
          </section>
        )}

        {/* POST-PAIR CHAT DISPATCH — replaces the pair block once a node is paired.
            This is the remote-control surface: messages typed here are dispatched
            to the user's paired Founder IDE (NOT Cursor) via /ide-bridge dispatch. */}
        {session?.accessToken && pairedNodeId && (
          <section className="mb-16" id="founder-ide-chat">
            <div className="mb-4 flex items-center justify-between gap-4">
              <div>
                <h3 className="text-xl font-semibold text-white">Drive your Founder IDE</h3>
                <p className="mt-1 text-sm text-zinc-400">
                  Pick an open project, type a message, and it lands in your Founder IDE chat box on your laptop — ready for the agent to act on.
                </p>
              </div>
              <button
                type="button"
                onClick={() => setPairedNodeId(null)}
                className="shrink-0 rounded-lg border border-zinc-700 px-3 py-1.5 text-xs font-semibold text-zinc-300 hover:border-zinc-500"
                title="Hide chat dispatch and show the download / pair section again"
              >
                Hide chat
              </button>
            </div>
            <FounderIdeChat accessToken={session.accessToken} nodeId={pairedNodeId} />
          </section>
        )}

        {/* If not signed in / no paired node yet, keep the original landing flow visible. */}
        {!session?.accessToken && checkedPair && (
          <section className="mb-16 rounded-2xl border border-zinc-800 bg-zinc-950/40 p-6 text-center">
            <p className="text-sm text-zinc-400">
              <Link href="/login?callbackUrl=/founder-ide" className="font-semibold text-violet-400 underline">
                Sign in
              </Link>{' '}
              to pair your Founder IDE and unlock the remote-control chat.
            </p>
          </section>
        )}

        {/* FEATURES */}
        <section className="mb-16 grid gap-6 md:grid-cols-2 lg:grid-cols-4">
          <div className="rounded-xl border border-emerald-700/40 bg-emerald-950/10 p-6">
            <h4 className="font-semibold text-white">10 BYOK Models</h4>
            <p className="mt-2 text-sm text-zinc-400">
              Bring keys from OpenAI, Anthropic, Google, DeepSeek, GLM, Mistral, Groq, xAI, Cohere, Together — all 10 work on the Free plan. <span className="text-zinc-500">(Cursor caps you at 1.)</span>
            </p>
          </div>
          <div className="rounded-xl border border-violet-900/40 bg-violet-950/10 p-6">
            <h4 className="font-semibold text-white">Borrow Forward</h4>
            <p className="mt-2 text-sm text-zinc-400">
              Hit your weekly cap mid-flow? Auto-borrow up to 50% of next week, up to 2 weeks. Codex-style — never lose a Friday night to a hard stop.
            </p>
          </div>
          <div className="rounded-xl border border-zinc-800 bg-zinc-950/30 p-6">
            <h4 className="font-semibold text-white">Auto Router</h4>
            <p className="mt-2 text-sm text-zinc-400">
              We route each request to the cheapest model that can do the job. Cursor-style cost-optimized routing — you pay API rates with 0% markup.
            </p>
          </div>
          <div className="rounded-xl border border-zinc-800 bg-zinc-950/30 p-6">
            <h4 className="font-semibold text-white">Local + Tab Always Work</h4>
            <p className="mt-2 text-sm text-zinc-400">
              Run Qwen, Llama, or GGUF models on your laptop. Windsurf-style degraded mode: Tab autocomplete and local models never stop working — even offline or at zero quota.
            </p>
          </div>
        </section>

        {/* PRICING — research-backed, Codex-borrow + Windsurf-degraded + Auto Router */}
        <section className="mb-16" id="pricing">
          <div className="mb-8 text-center">
            <h3 className="text-2xl font-bold text-white">Pricing</h3>
            <p className="mt-2 text-lg text-zinc-400 italic">
              "Borrow next week. Keep coding. We'll settle the math on Sunday."
            </p>
          </div>
          <div className="mt-6 grid gap-6 md:grid-cols-2 lg:grid-cols-4">
            {/* FREE */}
            <div className="rounded-xl border border-zinc-800 p-6">
              <h4 className="font-semibold text-white">Free</h4>
              <p className="mt-1 text-3xl font-bold text-white">$0</p>
              <p className="mt-1 text-xs text-zinc-500">forever</p>
              <ul className="mt-4 space-y-2 text-sm text-zinc-300">
                <li>Founder IDE app</li>
                <li><strong className="text-white">10 BYOK cloud models</strong> (OpenAI, Anthropic, GLM, +7)</li>
                <li><strong className="text-white">Unlimited local models</strong> (Qwen, Llama, GGUF)</li>
                <li>Tab autocomplete (always works)</li>
                <li>Limited daily cloud quota</li>
                <li>Pair with doxxedcrypto.digital</li>
              </ul>
              <p className="mt-3 text-xs text-zinc-500">
                <span className="text-zinc-300">vs Cursor:</span> Free is capped at 1 BYOK model.
              </p>
              <p className="mt-2 text-xs text-zinc-500">
                At limit: cloud pauses, <strong className="text-zinc-300">local mode keeps working</strong>.
              </p>
            </div>

            {/* PRO */}
            <div className="rounded-xl border border-violet-700 p-6 ring-1 ring-violet-700/50">
              <h4 className="font-semibold text-white">Pro</h4>
              <p className="mt-1 text-3xl font-bold text-white">$20<span className="text-base font-normal text-zinc-400">/mo</span></p>
              <p className="mt-1 text-xs text-violet-300">most popular</p>
              <ul className="mt-4 space-y-2 text-sm text-zinc-300">
                <li>Everything in Free</li>
                <li>5h burst window (~50–200 msgs)</li>
                <li>Weekly ceiling (~1,500 msgs)</li>
                <li><strong className="text-white">Auto Router</strong> (cost-optimized model routing)</li>
                <li><strong className="text-white">Borrow next week</strong> (50%, max 2 wks, auto)</li>
                <li>Signed update channel</li>
              </ul>
              <p className="mt-4 text-xs text-zinc-500">
                At limit: auto-borrow → degraded mode → upgrade nudge.
              </p>
            </div>

            {/* MAX */}
            <div className="rounded-xl border border-zinc-800 p-6">
              <h4 className="font-semibold text-white">Max</h4>
              <p className="mt-1 text-3xl font-bold text-white">$60<span className="text-base font-normal text-zinc-400">/mo</span></p>
              <p className="mt-1 text-xs text-zinc-500">power users</p>
              <ul className="mt-4 space-y-2 text-sm text-zinc-300">
                <li>Everything in Pro</li>
                <li><strong className="text-white">3× Pro quota</strong></li>
                <li>Priority routing</li>
                <li>Signed update channel</li>
                <li>Early access to new models</li>
              </ul>
              <p className="mt-4 text-xs text-zinc-500">
                Same borrow rules, 3× headroom.
              </p>
            </div>

            {/*
              TEAM — strategic pricing decision: every Free-tier feature is
              included PER SEAT on Team. This intentionally habituates users on
              the Free feature set inside their org (10 BYOK models, unlimited
              local models + Tab, pair with doxxedcrypto.digital). We are
              earning mindshare first; pricing power comes later once teams are
              locked in on the workflow. Do NOT strip these per-seat benefits
              without a deliberate go-to-market review.
            */}
            <div className="rounded-xl border border-zinc-800 p-6">
              <h4 className="font-semibold text-white">Team</h4>
              <p className="mt-1 text-3xl font-bold text-white">$40<span className="text-base font-normal text-zinc-400">/seat/mo</span></p>
              <p className="mt-1 text-xs text-zinc-500">for orgs</p>
              <ul className="mt-4 space-y-2 text-sm text-zinc-300">
                {/* Per-seat: every Free-tier benefit applies to each seat. */}
                <li><strong className="text-white">10 BYOK models</strong> (per seat)</li>
                <li><strong className="text-white">Unlimited local models + Tab</strong> (per seat)</li>
                <li><strong className="text-white">Pair with doxxedcrypto.digital</strong> (per seat)</li>
                <li>Per-seat Pro quota</li>
                <li>Org-wide credit pool</li>
                <li>Admin controls</li>
                <li>Spend caps &amp; alerts</li>
                <li>SSO + audit log</li>
              </ul>
              <p className="mt-3 text-xs text-emerald-300/80">
                Same Free-tier features, per seat. We&apos;re earning mindshare first.
              </p>
              <p className="mt-2 text-xs text-zinc-500">
                Admin-set org cap, per-seat overrides.
              </p>
            </div>
          </div>

          {/* On-demand overage line */}
          <div className="mt-8 rounded-lg border border-zinc-800 bg-zinc-950/40 p-4 text-center">
            <p className="text-sm text-zinc-400">
              <strong className="text-zinc-200">All plans:</strong> on-demand overage at API rates with{' '}
              <strong className="text-zinc-200">0% markup</strong> + user-set spend cap +{' '}
              <span className="text-zinc-200">75 / 90 / 100% alerts</span>.
            </p>
          </div>

          {/* VS CURSOR mini-comparison */}
          <div className="mt-6 overflow-hidden rounded-lg border border-zinc-800">
            <div className="border-b border-zinc-800 bg-zinc-950/60 px-4 py-2">
              <p className="text-xs font-semibold uppercase tracking-wide text-zinc-400">
                Founder IDE vs Cursor — honest, as of Aug 2026
              </p>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-left text-sm">
                <thead>
                  <tr className="border-b border-zinc-800 text-xs uppercase tracking-wide text-zinc-500">
                    <th className="px-4 py-2 font-medium">Capability</th>
                    <th className="px-4 py-2 font-medium text-violet-300">Founder</th>
                    <th className="px-4 py-2 font-medium text-zinc-400">Cursor</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-zinc-900 text-zinc-300">
                  <tr>
                    <td className="px-4 py-2">BYOK models on Free</td>
                    <td className="px-4 py-2 font-semibold text-white">10</td>
                    <td className="px-4 py-2 text-zinc-400">1</td>
                  </tr>
                  <tr>
                    <td className="px-4 py-2">Local models</td>
                    <td className="px-4 py-2 font-semibold text-white">Unlimited</td>
                    <td className="px-4 py-2 text-zinc-400">None</td>
                  </tr>
                  <tr>
                    <td className="px-4 py-2">Tab at zero quota</td>
                    <td className="px-4 py-2 font-semibold text-white">Yes</td>
                    <td className="px-4 py-2 text-zinc-400">No</td>
                  </tr>
                  <tr>
                    <td className="px-4 py-2">Borrow forward</td>
                    <td className="px-4 py-2 font-semibold text-white">Yes</td>
                    <td className="px-4 py-2 text-zinc-400">No</td>
                  </tr>
                  <tr>
                    <td className="px-4 py-2">Auto Router (cost-optimized)</td>
                    <td className="px-4 py-2 font-semibold text-white">Yes</td>
                    <td className="px-4 py-2 text-zinc-400">Yes</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>

          {/* Honest unit economics callout */}
          <div className="mt-4 text-center text-xs text-zinc-500">
            Founder Pro at $20/mo is viable because of Auto Router + local fallback.
            Free is viable because BYOK means users pay their own providers — we don't subsidize your tokens.
          </div>
        </section>

        {/* DECISION LOG LINK */}
        <section className="border-t border-zinc-800 pt-8 text-sm">
          <Link href="/founder-ide/decisions" className="text-violet-400 hover:underline">
            View decision log →
          </Link>
        </section>
      </div>
    </main>
  );
}
