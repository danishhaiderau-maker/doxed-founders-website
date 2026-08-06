'use client';

import { Suspense, useState } from 'react';
import { useSession } from 'next-auth/react';
import Link from 'next/link';
import { SiteBrand, SiteNav } from '@/components/site-nav';
import { FounderIdePair } from '@/components/founder-ide-pair';

export default function FounderIdePage() {
  const { data: session } = useSession();
  const [showPair, setShowPair] = useState(false);

  return (
    <main className="min-h-screen bg-[#050508] text-zinc-100">
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
        {showPair && (
          <section className="mb-16" id="pair">
            <div className="rounded-2xl border border-emerald-900/40 bg-emerald-950/10 p-8">
              <h3 className="text-xl font-semibold text-white">Pair your device</h3>
              <p className="mt-2 text-sm text-zinc-400">
                Generate a pairing code here, then paste it into Founder IDE → Settings → Founder Node → Pair.
              </p>
              <div className="mt-6">
                {session?.accessToken ? (
                  <Suspense fallback={<p className="text-sm text-zinc-500">Loading…</p>}>
                    <FounderIdePair accessToken={session.accessToken} />
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

        {/* FEATURES */}
        <section className="mb-16 grid gap-6 md:grid-cols-3">
          <div className="rounded-xl border border-zinc-800 bg-zinc-950/30 p-6">
            <h4 className="font-semibold text-white">Local AI</h4>
            <p className="mt-2 text-sm text-zinc-400">
              Run Qwen or GGUF models on your laptop. No OpenAI bill. Speculative decoding for speed.
            </p>
          </div>
          <div className="rounded-xl border border-zinc-800 bg-zinc-950/30 p-6">
            <h4 className="font-semibold text-white">Chat-first UX</h4>
            <p className="mt-2 text-sm text-zinc-400">
              Conversation-driven, not rigid pipelines. Voice dictation, Draw canvas, Full-control mode.
            </p>
          </div>
          <div className="rounded-xl border border-zinc-800 bg-zinc-950/30 p-6">
            <h4 className="font-semibold text-white">Pair from anywhere</h4>
            <p className="mt-2 text-sm text-zinc-400">
              Connect doxxedcrypto.digital to your laptop. Drive builds from your phone or any browser.
            </p>
          </div>
        </section>

        {/* PRICING (honest) */}
        <section className="mb-16">
          <h3 className="text-2xl font-bold text-white">Pricing</h3>
          <div className="mt-6 grid gap-6 md:grid-cols-3">
            <div className="rounded-xl border border-zinc-800 p-6">
              <h4 className="font-semibold text-white">Free</h4>
              <p className="mt-1 text-2xl font-bold text-white">$0</p>
              <p className="mt-1 text-xs text-zinc-500">forever</p>
              <ul className="mt-4 space-y-2 text-sm text-zinc-300">
                <li>Founder IDE app</li>
                <li>Bring your own API key (GLM, OpenAI, Anthropic, Gemini)</li>
                <li>Built-in local models (Qwen, Llama)</li>
                <li>Pair with doxxedcrypto.digital</li>
              </ul>
            </div>
            <div className="rounded-xl border border-violet-700 p-6">
              <h4 className="font-semibold text-white">Pro <span className="ml-2 rounded bg-violet-600 px-2 py-0.5 text-xs">Soon</span></h4>
              <p className="mt-1 text-2xl font-bold text-white">$19<span className="text-base font-normal text-zinc-400">/mo</span></p>
              <p className="mt-1 text-xs text-zinc-500">when launched</p>
              <ul className="mt-4 space-y-2 text-sm text-zinc-300">
                <li>Everything in Free</li>
                <li>Hosted relay (pair without laptop online)</li>
                <li>1M GLM credits / month</li>
                <li>Signed update channel</li>
              </ul>
            </div>
            <div className="rounded-xl border border-zinc-800 p-6">
              <h4 className="font-semibold text-white">Sovereign</h4>
              <p className="mt-1 text-2xl font-bold text-white">$49<span className="text-base font-normal text-zinc-400">/mo</span></p>
              <p className="mt-1 text-xs text-zinc-500">power users</p>
              <ul className="mt-4 space-y-2 text-sm text-zinc-300">
                <li>Everything in Pro</li>
                <li>Dedicated Neon branch</li>
                <li>Priority Founder Node compute</li>
                <li>Multi-device pairing</li>
              </ul>
            </div>
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
