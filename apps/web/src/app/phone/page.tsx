'use client';

import { useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import { useSession } from 'next-auth/react';
import { SiteBrand } from '@/components/site-nav';
import { apiUrl } from '@/lib/api-base';
import { ConnectedIdes } from '@/components/phone/connected-ides';
import { IdeSwitchButton } from '@/components/phone/ide-switch-button';
import { PhoneChat } from '@/components/phone/phone-chat';
import type { ConnectedNode, NodeStatusResponse } from '@/components/phone/types';

/**
 * Founder OS Phone Remote.
 *
 * A phone-accessible remote control for the founder's desktop IDE. The founder
 * authenticates with their NextAuth session JWT (same auth as the rest of the
 * web app), sees a list of connected IDEs / Founder Nodes, and can switch
 * which IDE the phone is "controlling" via the switch button at the top. The
 * chat panel streams responses from the AI Gateway (`/api/v1/chat/phone-completions`)
 * using the same routing path as the desktop IDE, with per-turn route metadata
 * (tier / provider / model / DDollar cost) shown under each reply.
 *
 * Architecture (Phase 2 = first half of the bridge):
 *   Phone Browser ←(SSE)→ Founder OS Cloud API ←(WebSocket)→ Founder Node ←→ IDE
 *
 * The Founder Node ↔ IDE WebSocket bridge is a later phase. See
 * docs/FOUNDER-IDE-FORK-PLAN.md §8.
 */
export default function PhoneRemotePage() {
  const { data: session, status } = useSession();
  const accessToken = session?.accessToken ?? null;

  const [nodes, setNodes] = useState<ConnectedNode[]>([]);
  const [activeNode, setActiveNode] = useState<ConnectedNode | null>(null);
  const [nodesLoading, setNodesLoading] = useState(true);

  const loadNodes = useCallback(async () => {
    if (!accessToken) return;
    try {
      const res = await fetch(apiUrl('/api/founder-node/status'), {
        headers: { Authorization: `Bearer ${accessToken}` },
      });
      if (!res.ok) return;
      const data = (await res.json()) as NodeStatusResponse;
      const next = Array.isArray(data?.nodes) ? data.nodes : [];
      setNodes(next);
      setActiveNode((prev) => {
        if (prev) {
          const refreshed = next.find((n) => n.nodeId === prev.nodeId);
          return refreshed ?? prev;
        }
        // Auto-select the first online node so the phone has a default IDE.
        const firstOnline = next.find((n) => n.status === 'online');
        return firstOnline ?? next[0] ?? null;
      });
    } catch {
      // surfaced by empty state below
    } finally {
      setNodesLoading(false);
    }
  }, [accessToken]);

  useEffect(() => {
    void loadNodes();
    const timer = setInterval(() => void loadNodes(), 20_000);
    return () => clearInterval(timer);
  }, [loadNodes]);

  if (status === 'loading') {
    return (
      <main className="flex min-h-screen items-center justify-center bg-[#050508] text-white">
        <p className="text-sm text-zinc-500">Loading Phone Remote…</p>
      </main>
    );
  }

  if (!accessToken) {
    return (
      <main className="flex min-h-screen flex-col items-center justify-center gap-3 bg-[#050508] px-6 text-center text-white">
        <SiteBrand className="text-sm" />
        <h1 className="text-xl font-bold">Phone Remote</h1>
        <p className="max-w-xs text-sm text-zinc-500">
          Sign in to control your Founder IDE from your phone.
        </p>
        <Link
          href="/login?callbackUrl=/phone"
          className="mt-2 rounded-lg bg-emerald-500 px-5 py-2.5 text-sm font-semibold text-black hover:bg-emerald-400"
        >
          Sign in
        </Link>
      </main>
    );
  }

  return (
    <main className="min-h-screen bg-[#050508] text-white">
      <header className="sticky top-0 z-40 border-b border-zinc-800/80 bg-[#050508]/95 backdrop-blur">
        <div className="mx-auto flex w-full max-w-md items-center justify-between gap-3 px-4 py-3">
          <div className="min-w-0">
            <SiteBrand className="text-xs" />
            <h1 className="truncate text-sm font-bold text-white">Phone Remote</h1>
          </div>
          <Link
            href="/founder-os"
            className="shrink-0 rounded-lg border border-zinc-700 px-2.5 py-1.5 text-[11px] text-zinc-300 hover:border-zinc-500 hover:text-white"
          >
            Founder OS →
          </Link>
        </div>
      </header>

      <div className="mx-auto w-full max-w-md space-y-4 px-4 py-4">
        {/* Switch button — toggles which IDE the phone is controlling. */}
        <IdeSwitchButton
          nodes={nodes}
          activeNode={activeNode}
          onSelect={(node) => {
            if (node.status !== 'online') return;
            setActiveNode(node);
          }}
        />

        {/* Active IDE status strip. */}
        {activeNode && (
          <div className="flex flex-wrap items-center gap-2 rounded-xl border border-zinc-800 bg-zinc-950/40 px-3 py-2 text-[11px]">
            <span
              className={[
                'inline-block h-1.5 w-1.5 rounded-full',
                activeNode.status === 'online' ? 'bg-emerald-400' : 'bg-zinc-600',
              ].join(' ')}
              aria-hidden
            />
            <span className="font-medium text-zinc-200">{activeNode.label || 'Unnamed machine'}</span>
            <span className="text-zinc-600">·</span>
            <span className="text-zinc-500">{activeNode.platform ?? 'desktop'}</span>
            {activeNode.appVersion && (
              <>
                <span className="text-zinc-600">·</span>
                <span className="text-zinc-500">v{activeNode.appVersion}</span>
              </>
            )}
            <span className="ml-auto text-zinc-600">{activeNode.status}</span>
          </div>
        )}

        {/* Connected IDEs list — switch or connect another. */}
        {!nodesLoading && nodes.length > 1 && (
          <ConnectedIdes
            accessToken={accessToken}
            activeNodeId={activeNode?.nodeId ?? null}
            onSelect={(node) => {
              if (node.status !== 'online') return;
              setActiveNode(node);
            }}
          />
        )}
        {!nodesLoading && nodes.length === 0 && (
          <div className="rounded-xl border border-amber-500/30 bg-amber-950/15 p-4 text-xs text-amber-100">
            <p className="font-semibold">No connected IDEs yet</p>
            <p className="mt-1 text-amber-200/80">
              Install{' '}
              <Link href="/downloads#founder-node" className="underline">
                Founder IDE
              </Link>{' '}
              on your desktop to connect Cursor, Founder IDE, or another editor. Then it will appear here and you can
              switch to it from your phone.
            </p>
          </div>
        )}

        {/* Chat — SSE streaming through the AI Gateway. */}
        <PhoneChat accessToken={accessToken} activeNode={activeNode} />

        <p className="pb-6 text-center text-[10px] text-zinc-600">
          Phone Remote · Phase 2 · same AI routing as your desktop IDE
        </p>
      </div>
    </main>
  );
}
