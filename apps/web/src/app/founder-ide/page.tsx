'use client';

import { Suspense, useCallback, useEffect, useState } from 'react';
import { useSession } from 'next-auth/react';
import Link from 'next/link';
import { SiteBrand, SiteNav } from '@/components/site-nav';
import { FounderIdePair } from '@/components/founder-ide-pair';
import { FounderIdeChat } from '@/components/founder-ide-chat';
import { fetchFounderNodeStatus, revokeFounderNode } from '@/lib/api';

export default function FounderIdePage() {
  const { data: session } = useSession();
  const [showPair, setShowPair] = useState(false);
  // Post-pairing chat dispatch UX — non-null when a paired node exists.
  // On mount we check the user's node status so page reloads land in the chat
  // state instead of the pairing block.
  const [pairedNodeId, setPairedNodeId] = useState<string | null>(null);
  const [checkedPair, setCheckedPair] = useState(false);
  const [replacingNode, setReplacingNode] = useState(false);
  const [pairError, setPairError] = useState<string | null>(null);

  const checkPair = useCallback(async () => {
    if (!session?.accessToken) {
      setCheckedPair(true);
      return;
    }
    try {
      const status = await fetchFounderNodeStatus(session.accessToken);
      const firstOnline = status.nodes?.find((node) => node.status === 'online');
      setPairedNodeId(firstOnline?.nodeId ?? null);
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

  const replaceStaleNode = useCallback(async () => {
    if (!session?.accessToken || !pairedNodeId) return;
    setReplacingNode(true);
    setPairError(null);
    try {
      await revokeFounderNode(pairedNodeId, session.accessToken);
      setPairedNodeId(null);
      setShowPair(true);
    } catch (error) {
      setPairError(error instanceof Error ? error.message : 'Could not replace the stale Founder Node connection.');
    } finally {
      setReplacingNode(false);
    }
  }, [pairedNodeId, session?.accessToken]);

  return (
    <main className='min-h-screen bg-[#050508] text-zinc-100'>
      <header className='border-b border-zinc-800'>
        <div className='mx-auto flex max-w-5xl flex-wrap items-center justify-between gap-4 px-6 py-5'>
          <div>
            <SiteBrand className='text-sm' />
            <h1 className='mt-1 text-2xl font-semibold tracking-tight text-white'>Founder IDE</h1>
            <p className='text-sm text-zinc-400'>
              Your sovereign AI coding environment. Runs locally. Your compute, your models, your code.
            </p>
          </div>
          <SiteNav />
        </div>
      </header>

      <div className='mx-auto max-w-5xl px-6 py-12'>
        {/* HERO */}
        <section className='mb-12'>
          <h2 className='text-3xl font-semibold tracking-tight text-white sm:text-4xl'>
            Drive your Founder IDE from anywhere.
          </h2>
          <p className='mt-3 max-w-2xl text-base text-zinc-400'>
            Founder IDE runs on your laptop and pairs with this site so you can dispatch prompts, switch projects,
            and keep work moving without sitting at the computer. This page is the remote-control surface —
            billing and plan info live on the{' '}
            <Link href='/pricing' className='font-medium text-violet-400 underline-offset-4 hover:underline'>
              pricing page
            </Link>
            .
          </p>
        </section>

        {/* DOWNLOAD / PAIR — primary surface when no node is paired yet. */}
        {!(session?.accessToken && pairedNodeId) && (
          <section className='mb-12'>
            <div className='rounded-2xl border border-zinc-800 bg-zinc-950/50 p-8'>
              <h3 className='text-lg font-semibold text-white'>Get started</h3>
              <p className='mt-2 text-sm text-zinc-400'>
                Install Founder IDE on your computer, then pair it with this device.
              </p>

              <div className='mt-6 flex flex-wrap gap-3'>
                <a
                  href='https://github.com/danishhaiderau-maker/founder-next/releases/latest'
                  className='inline-flex items-center gap-1.5 rounded-xl bg-violet-600 px-5 py-2.5 text-sm font-semibold text-white transition hover:bg-violet-500'
                  target='_blank'
                  rel='noreferrer'
                >
                  Download for Windows
                </a>
                <button
                  type='button'
                  onClick={() => setShowPair((v) => !v)}
                  className='inline-flex items-center gap-1.5 rounded-xl border border-zinc-700 px-5 py-2.5 text-sm font-semibold text-zinc-200 transition hover:border-zinc-500 hover:bg-white/5'
                >
                  {showPair ? 'Hide pairing' : 'I already have it — Pair my device'}
                </button>
              </div>

              <p className='mt-4 text-xs text-zinc-500'>
                Windows 10/11 · ~850 MB installer · Mac and Linux coming soon.
              </p>
            </div>
          </section>
        )}

        {/* PAIR SECTION */}
        {showPair && !pairedNodeId && (
          <section className='mb-12' id='pair'>
            <div className='rounded-2xl border border-zinc-800 bg-zinc-950/50 p-8'>
              <h3 className='text-lg font-semibold text-white'>Pair your device</h3>
              <p className='mt-2 text-sm text-zinc-400'>
                Generate a pairing code here, then paste it into Founder IDE → Settings → Founder Node → Pair.
              </p>
              <div className='mt-6'>
                {session?.accessToken ? (
                  <Suspense fallback={<p className='text-sm text-zinc-500'>Loading…</p>}>
                    <FounderIdePair accessToken={session.accessToken} onPaired={handlePaired} />
                  </Suspense>
                ) : (
                  <div className='rounded-xl border border-amber-500/30 bg-amber-950/15 p-4 text-sm text-amber-100'>
                    <Link href='/login?callbackUrl=/founder-ide' className='font-semibold underline'>
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
          <section className='mb-12' id='founder-ide-chat'>
            <div className='mb-5 flex items-start justify-between gap-4'>
              <div>
                <h3 className='text-lg font-semibold text-white'>Drive your Founder IDE</h3>
                <p className='mt-1 max-w-2xl text-sm text-zinc-400'>
                  Pick an open project, type a message, and it lands in your Founder IDE chat box — ready for the
                  agent to act on.
                </p>
                <button
                  type='button'
                  onClick={() => void replaceStaleNode()}
                  disabled={replacingNode}
                  className='mt-3 text-xs text-zinc-400 underline underline-offset-4 hover:text-white disabled:cursor-not-allowed disabled:opacity-60'
                >
                  {replacingNode ? 'Replacing connection…' : 'Replace this Founder Node'}
                </button>
                {pairError && <p role='alert' className='mt-2 text-xs text-red-300'>{pairError}</p>}
              </div>
              <button
                type='button'
                onClick={() => setPairedNodeId(null)}
                className='shrink-0 rounded-xl border border-zinc-700 px-3 py-1.5 text-xs font-semibold text-zinc-300 transition hover:border-zinc-500 hover:bg-white/5'
                title='Hide chat dispatch and show the download / pair section again'
              >
                Hide chat
              </button>
            </div>
            <FounderIdeChat accessToken={session.accessToken} nodeId={pairedNodeId} />
          </section>
        )}

        {/* If not signed in / no paired node yet, keep the original landing flow visible. */}
        {!session?.accessToken && checkedPair && (
          <section className='mb-12 rounded-2xl border border-zinc-800 bg-zinc-950/40 p-6 text-center'>
            <p className='text-sm text-zinc-400'>
              <Link href='/login?callbackUrl=/founder-ide' className='font-semibold text-violet-400 underline-offset-4 hover:underline'>
                Sign in
              </Link>{' '}
              to pair your Founder IDE and unlock the remote-control chat.
            </p>
          </section>
        )}

        {/* FEATURES — terse, scannable, no clutter. */}
        <section className='mb-12'>
          <h3 className='text-xs font-semibold uppercase tracking-[0.18em] text-zinc-500'>
            What you get
          </h3>
          <div className='mt-5 grid gap-4 sm:grid-cols-2'>
            <div className='rounded-xl border border-zinc-800 bg-zinc-950/40 p-5'>
              <h4 className='text-sm font-semibold text-white'>5 BYOK models on Free · 10 on paid</h4>
              <p className='mt-1.5 text-sm text-zinc-400'>
                Bring keys from OpenAI, Anthropic, Google, DeepSeek, Mistral, Groq, xAI, Cohere, Together (GLM is BYOK-only).
                Free includes up to 5; Pro, Max, and Team unlock all 10.
              </p>
            </div>
            <div className='rounded-xl border border-zinc-800 bg-zinc-950/40 p-5'>
              <h4 className='text-sm font-semibold text-white'>Unlimited local models</h4>
              <p className='mt-1.5 text-sm text-zinc-400'>
                Run Qwen, Llama, or GGUF models on your laptop. Tab autocomplete and local inference always work —
                even offline.
              </p>
            </div>
            <div className='rounded-xl border border-zinc-800 bg-zinc-950/40 p-5'>
              <h4 className='text-sm font-semibold text-white'>Auto Router</h4>
              <p className='mt-1.5 text-sm text-zinc-400'>
                Each request is routed to the cheapest model that can do the job. API rates with 0% markup.
              </p>
            </div>
            <div className='rounded-xl border border-zinc-800 bg-zinc-950/40 p-5'>
              <h4 className='text-sm font-semibold text-white'>Borrow forward</h4>
              <p className='mt-1.5 text-sm text-zinc-400'>
                Hit your cap mid-flow? Auto-borrow up to 50% of next week, up to 2 weeks. Never lose a session to a
                hard stop.
              </p>
            </div>
          </div>

          <div className='mt-6'>
            <Link
              href='/pricing'
              className='inline-flex items-center gap-1.5 text-sm font-medium text-violet-400 underline-offset-4 hover:underline'
            >
              See full plans &amp; pricing →
            </Link>
          </div>
        </section>

        {/* DECISION LOG LINK */}
        <section className='border-t border-zinc-800 pt-8'>
          <Link href='/founder-ide/decisions' className='text-sm text-violet-400 underline-offset-4 hover:underline'>
            View routing decision log →
          </Link>
        </section>
      </div>
    </main>
  );
}
