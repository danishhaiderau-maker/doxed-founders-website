'use client';

import { Suspense, useCallback, useEffect, useRef, useState } from 'react';
import { useSession } from 'next-auth/react';
import Link from 'next/link';
import { SiteBrand, SiteNav } from '@/components/site-nav';
import { FounderIdePair } from '@/components/founder-ide-pair';
import { FounderIdeChat } from '@/components/founder-ide-chat';
import { fetchFounderNodeStatus, revokeFounderNode } from '@/lib/api';
import { FounderIdeDownload } from './founder-ide-download';
import {
  applyFounderIdePairCompletion,
  applyFounderIdeTokenUpdate,
  checkFounderIdePairStatus,
  createFounderIdeTokenState,
  getFounderIdeTokenState,
  type FounderIdeTokenState,
} from './founder-ide-pair-status';

export default function FounderIdePage() {
  const { data: session } = useSession();
  const accessToken = session?.accessToken ?? null;
  const [storedTokenState, setStoredTokenState] = useState<FounderIdeTokenState>(() =>
    createFounderIdeTokenState(null),
  );
  const tokenState = getFounderIdeTokenState(storedTokenState, accessToken);
  const {
    showPair,
    pairIntentGeneration,
    pairedNodeId,
    checkedPair,
    pairStatusError,
    replacingNode,
    pairError,
  } = tokenState;
  const pairStatusGeneration = useRef(0);
  const currentAccessToken = useRef<string | null>(accessToken);
  const currentPairIntentGeneration = useRef(pairIntentGeneration);
  currentAccessToken.current = accessToken;
  currentPairIntentGeneration.current = pairIntentGeneration;

  const updateCurrentTokenState = useCallback(
    (update: (current: FounderIdeTokenState) => FounderIdeTokenState) => {
      setStoredTokenState((previous) => update(getFounderIdeTokenState(previous, accessToken)));
    },
    [accessToken],
  );

  const updateOperationTokenState = useCallback(
    (
      operationToken: string | null,
      update: (current: FounderIdeTokenState) => FounderIdeTokenState,
    ) => {
      setStoredTokenState((previous) =>
        applyFounderIdeTokenUpdate(previous, operationToken, currentAccessToken.current, update),
      );
    },
    [],
  );

  const checkPair = useCallback(async () => {
    const operationToken = accessToken;
    const generation = ++pairStatusGeneration.current;
    updateCurrentTokenState((current) => ({ ...current, checkedPair: false }));

    if (!operationToken) {
      updateOperationTokenState(operationToken, (current) => ({
        ...current,
        pairedNodeId: null,
        pairStatusError: null,
        checkedPair: true,
      }));
      return;
    }

    const result = await checkFounderIdePairStatus({
      accessToken: operationToken,
      fetchStatus: fetchFounderNodeStatus,
      isCurrent: () =>
        pairStatusGeneration.current === generation && currentAccessToken.current === operationToken,
    });

    if (result.kind === 'stale') return;

    if (result.kind === 'error') {
      updateOperationTokenState(operationToken, (current) => ({
        ...current,
        pairStatusError: 'Could not verify Founder Node pairing status.',
        checkedPair: true,
      }));
      return;
    }

    updateOperationTokenState(operationToken, (current) => ({
      ...current,
      pairedNodeId: result.nodeId,
      pairStatusError: null,
      checkedPair: true,
    }));
  }, [accessToken, updateCurrentTokenState, updateOperationTokenState]);

  useEffect(() => {
    // Render already scopes state to accessToken. This reset commits that
    // fail-closed view and invalidates any request from the previous account.
    pairStatusGeneration.current += 1;
    setStoredTokenState(createFounderIdeTokenState(accessToken));
    void checkPair();

    return () => {
      pairStatusGeneration.current += 1;
    };
  }, [accessToken, checkPair]);

  const handlePaired = useCallback((nodeId: string) => {
    const operationToken = accessToken;
    const operationPairIntentGeneration = pairIntentGeneration;
    if (
      !operationToken ||
      currentAccessToken.current !== operationToken ||
      currentPairIntentGeneration.current !== operationPairIntentGeneration
    ) return;
    updateOperationTokenState(operationToken, (current) =>
      applyFounderIdePairCompletion(current, operationPairIntentGeneration, nodeId),
    );
    // Smooth-scroll the chat dispatch panel into view after the transition.
    setTimeout(() => {
      if (
        currentAccessToken.current !== operationToken ||
        currentPairIntentGeneration.current !== operationPairIntentGeneration
      ) return;
      document.getElementById('founder-ide-chat')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }, 100);
  }, [accessToken, pairIntentGeneration, updateOperationTokenState]);

  const disconnectFounderNode = useCallback(async () => {
    const operationToken = accessToken;
    if (!operationToken || !pairedNodeId) return;
    const confirmed = window.confirm(
      'Disconnect this Founder Node? Remote requests will stop immediately. You can pair this computer again later.',
    );
    if (!confirmed) return;
    updateOperationTokenState(operationToken, (current) => ({
      ...current,
      replacingNode: true,
      pairError: null,
    }));
    try {
      await revokeFounderNode(pairedNodeId, operationToken);
      updateOperationTokenState(operationToken, (current) => ({
        ...current,
        pairedNodeId: null,
        showPair: true,
        pairIntentGeneration: current.pairIntentGeneration + 1,
      }));
    } catch (error) {
      updateOperationTokenState(operationToken, (current) => ({
        ...current,
        pairError: error instanceof Error ? error.message : 'Could not disconnect the Founder Node.',
      }));
    } finally {
      updateOperationTokenState(operationToken, (current) => ({ ...current, replacingNode: false }));
    }
  }, [accessToken, pairedNodeId, updateOperationTokenState]);

  return (
    <main className='min-h-screen bg-[#050508] text-zinc-100'>
      <header className='border-b border-zinc-800'>
        <div className='mx-auto flex max-w-5xl flex-col items-stretch gap-4 px-6 py-5'>
          <div className='min-w-0'>
            <SiteBrand className='text-sm' />
            <h1 className='mt-1 text-2xl font-semibold tracking-tight text-white'>Founder IDE</h1>
            <p className='text-sm text-zinc-400'>
              Your sovereign AI coding environment. Runs locally. Your compute, your models, your code.
            </p>
          </div>
          <div className='min-w-0 w-full'>
            <SiteNav />
          </div>
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

        {accessToken && !checkedPair && !pairStatusError && (
          <section className='mb-12 rounded-2xl border border-zinc-800 bg-zinc-950/40 p-6' role='status'>
            <p className='text-sm text-zinc-400'>Checking Founder Node pairing status…</p>
          </section>
        )}

        {accessToken && pairStatusError && (
          <section className='mb-12 rounded-2xl border border-amber-500/30 bg-amber-950/15 p-6' role='alert'>
            <h3 className='text-sm font-semibold text-amber-100'>Founder Node status unavailable</h3>
            <p className='mt-2 text-sm text-amber-100/75'>
              {pairStatusError} No device was disconnected or changed. Retry to refresh this account&apos;s status.
            </p>
            <button
              type='button'
              onClick={() => void checkPair()}
              disabled={!checkedPair}
              className='mt-4 rounded-xl border border-amber-400/40 px-4 py-2 text-sm font-semibold text-amber-100 transition hover:border-amber-300/70 hover:bg-amber-950/30 disabled:cursor-wait disabled:opacity-60'
            >
              {checkedPair ? 'Retry status check' : 'Checking…'}
            </button>
          </section>
        )}

        {/* DOWNLOAD / PAIR — primary surface when no node is paired yet. */}
        {(!accessToken || (checkedPair && !pairStatusError)) && !(accessToken && pairedNodeId) && (
          <section className='mb-12'>
            <div className='rounded-2xl border border-zinc-800 bg-zinc-950/50 p-8'>
              <h3 className='text-lg font-semibold text-white'>Get started</h3>
              <p className='mt-2 text-sm text-zinc-400'>
                Pair an existing Founder IDE installation with this device. New installs will be available here
                after a public Windows release is verified.
              </p>

              <div className='mt-6 flex flex-wrap items-start gap-3'>
                <FounderIdeDownload />
                <button
                  type='button'
                  onClick={() =>
                    updateCurrentTokenState((current) => ({
                      ...current,
                      showPair: !current.showPair,
                      pairIntentGeneration: current.pairIntentGeneration + 1,
                    }))
                  }
                  className='inline-flex items-center gap-1.5 rounded-xl border border-zinc-700 px-5 py-2.5 text-sm font-semibold text-zinc-200 transition hover:border-zinc-500 hover:bg-white/5'
                >
                  {showPair ? 'Hide pairing' : 'I already have it — Pair my device'}
                </button>
              </div>
            </div>
          </section>
        )}

        {/* PAIR SECTION */}
        {showPair && checkedPair && !pairStatusError && !pairedNodeId && (
          <section className='mb-12' id='pair'>
            <div className='rounded-2xl border border-zinc-800 bg-zinc-950/50 p-8'>
              <h3 className='text-lg font-semibold text-white'>Pair your device</h3>
              <p className='mt-2 text-sm text-zinc-400'>
                Generate a pairing code here, then paste it into Founder IDE → Settings → Founder Node → Pair.
              </p>
              <div className='mt-6'>
                {accessToken ? (
                  <Suspense fallback={<p className='text-sm text-zinc-500'>Loading…</p>}>
                    <FounderIdePair accessToken={accessToken} onPaired={handlePaired} />
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
        {accessToken && pairedNodeId && (
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
                  onClick={() => void disconnectFounderNode()}
                  disabled={replacingNode}
                  className='mt-3 text-xs text-red-300 underline underline-offset-4 hover:text-red-200 disabled:cursor-not-allowed disabled:opacity-60'
                >
                  {replacingNode ? 'Disconnecting…' : 'Disconnect this Founder Node'}
                </button>
                {pairError && <p role='alert' className='mt-2 text-xs text-red-300'>{pairError}</p>}
              </div>
              <button
                type='button'
                onClick={() =>
                  updateCurrentTokenState((current) => ({ ...current, pairedNodeId: null }))
                }
                className='shrink-0 rounded-xl border border-zinc-700 px-3 py-1.5 text-xs font-semibold text-zinc-300 transition hover:border-zinc-500 hover:bg-white/5'
                title='Hide chat dispatch and show the download / pair section again'
              >
                Hide chat
              </button>
            </div>
            <FounderIdeChat accessToken={accessToken} nodeId={pairedNodeId} />
          </section>
        )}

        {/* If not signed in / no paired node yet, keep the original landing flow visible. */}
        {!accessToken && checkedPair && (
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
              <h4 className='text-sm font-semibold text-white'>Your verified provider connections</h4>
              <p className='mt-1.5 text-sm text-zinc-400'>
                Configure providers in the desktop Connections page. Supported providers, connection limits and
                model roles are shown there; this website does not save or transfer your API keys.
              </p>
            </div>
            <div className='rounded-xl border border-zinc-800 bg-zinc-950/40 p-5'>
              <h4 className='text-sm font-semibold text-white'>Compatible local models</h4>
              <p className='mt-1.5 text-sm text-zinc-400'>
                Reuse installed models or import a compatible GGUF in the desktop app. Verify the model and its
                role before use; hardware and runtime support determine what runs locally.
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
