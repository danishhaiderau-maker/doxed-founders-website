'use client';

import { useCallback, useEffect, useState } from 'react';
import {
  createFounderNodePairingCode,
  fetchFounderNodeStatus,
  revokeFounderNode,
  type FounderNodeStatusRow,
} from '@/lib/api';
import { CollapsibleInfo } from '@/components/ui/collapsible-info';
import { FOUNDER_NODE_MIN_VERSION_LABEL } from '@/lib/founder-node-requirements';

type Props = {
  accessToken: string;
  active: boolean;
};

function formatExpiry(expiresAt: string) {
  const exp = new Date(expiresAt);
  const mins = Math.max(0, Math.round((exp.getTime() - Date.now()) / 60_000));
  return { absolute: exp.toLocaleString(), mins };
}

function formatLastSeen(iso: string | null | undefined): string {
  if (!iso) return 'never';
  const ms = Date.now() - new Date(iso).getTime();
  if (ms < 60_000) return 'just now';
  const mins = Math.floor(ms / 60_000);
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  return `${hrs}h ago`;
}

export function FounderNodePairingPanel({ accessToken, active }: Props) {
  const [nodes, setNodes] = useState<FounderNodeStatusRow[]>([]);
  const [pairingCode, setPairingCode] = useState<string | null>(null);
  const [pairingTarget, setPairingTarget] = useState<'desktop' | 'mobile' | null>(null);
  const [expiresAt, setExpiresAt] = useState<string | null>(null);
  const [showNewPairing, setShowNewPairing] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const isPaired = nodes.length > 0;
  const anyOnline = nodes.some((n) => n.status === 'online');

  const refresh = useCallback(async () => {
    try {
      const status = await fetchFounderNodeStatus(accessToken);
      const list = status.nodes;
      setNodes(list);
      if (list.length > 0) {
        setPairingCode(null);
        setPairingTarget(null);
        setExpiresAt(null);
        setShowNewPairing(false);
        const online = list.some((n) => n.status === 'online');
        setMsg(
          online
            ? 'Vault connected on your desktop. The pairing code stays hidden until you disconnect or pair another device.'
            : 'Account linked in the cloud, but your PC is not heartbeating. Open Founder Node from the Start Menu (tray icon), click Sync now, or generate a new code below and enter it in the tray app. Your old desktop token only stops working after you pair again with a new code — generating a code alone does not break the link.',
        );
      }
      setErr(null);
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Could not load node status');
    }
  }, [accessToken]);

  useEffect(() => {
    if (!active) return;
    refresh();
    const interval = setInterval(refresh, 15_000);
    return () => clearInterval(interval);
  }, [active, refresh]);

  async function generateCode(target: 'desktop' | 'mobile') {
    setBusy(target);
    setErr(null);
    setMsg(null);
    try {
      const result = await createFounderNodePairingCode(accessToken, target);
      setPairingCode(result.code);
      setPairingTarget(target);
      setExpiresAt(result.expiresAt);
      const { mins, absolute } = formatExpiry(result.expiresAt);
      setMsg(
        target === 'mobile'
          ? `Enter this code in the Doxxed Crypto Android app (Settings → vault) within ${mins} minute${mins === 1 ? '' : 's'} (by ${absolute}).`
          : `Enter this one-time code in Founder Node on desktop within ${mins} minute${mins === 1 ? '' : 's'} (by ${absolute}). After pairing succeeds, the code is cleared automatically.`,
      );
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Could not create pairing code');
    } finally {
      setBusy(null);
    }
  }

  async function revoke(nodeId: string) {
    setBusy(nodeId);
    setErr(null);
    try {
      await revokeFounderNode(nodeId, accessToken);
      setMsg('Node disconnected. Generate a new code only if you pair a device again.');
      setShowNewPairing(true);
      await refresh();
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Could not revoke node');
    } finally {
      setBusy(null);
    }
  }

  const desktopNeedsRePair = isPaired && !anyOnline;
  const showPairingFlow = !isPaired || showNewPairing || desktopNeedsRePair;

  return (
    <div className="mt-4 rounded-lg border border-cyan-500/30 bg-cyan-950/20 p-4">
      <h4 className="text-sm font-semibold text-cyan-100">Founder Node pairing</h4>
      <p className="mt-1 text-xs text-cyan-100/70">
        Install Founder Node {FOUNDER_NODE_MIN_VERSION_LABEL} on your PC. Your vault stays local — Founder OS only receives tiny metadata
        snapshots.
      </p>

      {isPaired && !showNewPairing && anyOnline && (
        <div className="mt-3 rounded-lg border border-emerald-500/35 bg-emerald-950/25 px-3 py-2 text-xs text-emerald-200">
          ✓ Desktop vault connected — tray app must keep running (icon near the clock).
        </div>
      )}

      {isPaired && !showNewPairing && !anyOnline && (
        <div className="mt-3 rounded-lg border border-amber-500/40 bg-amber-950/25 px-3 py-2 text-xs text-amber-200">
          Paired in cloud, but desktop offline — generate a new code below or open the tray app.
        </div>
      )}

      {/* Primary pairing actions — always visible */}
      {showPairingFlow && (
        <div className="mt-4 flex flex-wrap gap-2">
          <button
            type="button"
            disabled={busy === 'desktop'}
            onClick={() => void generateCode('desktop')}
            className="rounded-lg bg-cyan-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
          >
            {busy === 'desktop' ? 'Generating…' : 'Code for desktop'}
          </button>
          <button
            type="button"
            disabled={busy === 'mobile'}
            onClick={() => void generateCode('mobile')}
            className="rounded-lg border border-violet-500/50 bg-violet-950/40 px-4 py-2 text-sm font-medium text-violet-100 disabled:opacity-50"
          >
            {busy === 'mobile' ? 'Generating…' : 'Code for Android'}
          </button>
          {isPaired && (
            <button
              type="button"
              onClick={() => {
                setShowNewPairing(false);
                setPairingCode(null);
                setPairingTarget(null);
                setExpiresAt(null);
              }}
              className="rounded-lg border border-zinc-600 px-3 py-2 text-xs text-zinc-400"
            >
              Cancel
            </button>
          )}
        </div>
      )}

      {pairingCode && pairingTarget === 'desktop' && (
        <div className="mt-3 rounded-lg border border-cyan-400/40 bg-black/30 p-3 text-center">
          <p className="text-xs text-zinc-400">Paste in Founder Node tray app (desktop)</p>
          <p className="mt-1 font-mono text-2xl font-bold tracking-[0.3em] text-cyan-300">{pairingCode}</p>
          {expiresAt && (
            <p className="mt-1 text-[10px] text-zinc-500">
              Valid for {formatExpiry(expiresAt).mins} min (until {formatExpiry(expiresAt).absolute})
            </p>
          )}
        </div>
      )}

      {pairingCode && pairingTarget === 'mobile' && (
        <div className="mt-3 rounded-lg border border-violet-400/40 bg-black/30 p-3 text-center">
          <p className="text-xs text-zinc-400">Enter in Android app → Founder Node → Android vault</p>
          <p className="mt-1 font-mono text-2xl font-bold tracking-[0.3em] text-violet-200">{pairingCode}</p>
          {expiresAt && (
            <p className="mt-1 text-[10px] text-zinc-500">
              Valid for {formatExpiry(expiresAt).mins} min (until {formatExpiry(expiresAt).absolute})
            </p>
          )}
        </div>
      )}

      {isPaired && !showNewPairing && (
        <button
          type="button"
          onClick={() => setShowNewPairing(true)}
          className="mt-3 text-[11px] text-cyan-400/90 underline hover:text-cyan-300"
        >
          Pair another desktop device (new PC code)
        </button>
      )}

      {/* Connected nodes — always visible */}
      {nodes.length > 0 && (
        <div className="mt-4 space-y-2">
          <p className="text-xs font-medium text-zinc-300">Connected nodes</p>
          {nodes.map((node) => (
            <div
              key={node.nodeId}
              className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-zinc-700 bg-zinc-900/50 px-3 py-2 text-xs"
            >
              <div>
                <span className="font-medium text-white">{node.label}</span>
                <span
                  className={`ml-2 rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase ${
                    node.status === 'online'
                      ? 'bg-emerald-950 text-emerald-300'
                      : 'bg-zinc-800 text-zinc-400'
                  }`}
                >
                  {node.status}
                </span>
                {node.status === 'offline' && (
                  <span className="ml-2 text-zinc-500">
                    — last heartbeat {formatLastSeen(node.lastSeenAt)}
                    {node.lastSeenAt
                      ? ' · tray lost its link — generate a new code above and paste in Founder Node'
                      : ' · start Founder Node tray app'}
                  </span>
                )}
                {node.ramGb != null && <span className="ml-2 text-zinc-500">{node.ramGb} GB RAM</span>}
                <span
                  className={`ml-2 ${
                    node.status === 'online' && node.vaultHealthy
                      ? 'text-emerald-400'
                      : node.status === 'online'
                        ? 'text-amber-400'
                        : 'text-zinc-500'
                  }`}
                >
                  {node.status === 'online'
                    ? `Vault ${node.vaultHealthy ? 'healthy' : 'issue'}`
                    : 'Vault on PC · sync offline'}
                </span>
              </div>
              <button
                type="button"
                disabled={busy === node.nodeId}
                onClick={() => revoke(node.nodeId)}
                className="rounded border border-zinc-600 px-2 py-1 text-zinc-400 hover:text-white disabled:opacity-50"
              >
                Disconnect
              </button>
            </div>
          ))}
        </div>
      )}

      {msg && (
        <p
          className={`mt-3 text-xs ${
            isPaired && !anyOnline && !showNewPairing ? 'text-amber-200' : 'text-emerald-300'
          }`}
        >
          {msg}
        </p>
      )}
      {err && <p className="mt-3 text-xs text-red-300">{err}</p>}

      {/* Instructional sections — collapsed by default */}
      <div className="mt-4 space-y-2">
        <CollapsibleInfo title="What stays private" hint="Vault vs cloud" accent="emerald">
          <p className="text-[11px] leading-relaxed text-emerald-100/90">
            Full company memory (private notes, raw task bodies, vault markdown) stays on your machine and in
            encrypted blobs we cannot read. The website sees progress metadata and what you publish to GitHub or
            the public feed.
          </p>
        </CollapsibleInfo>

        <CollapsibleInfo title="How to pair" hint="3 steps">
          <ol className="list-inside list-decimal space-y-1 text-xs text-zinc-300">
            <li>Download and open Founder Node (tray app).</li>
            <li>
              Generate a pairing code above and paste it once into the <strong>Founder Node tray popup</strong>{' '}
              (Pair this machine — not a browser tab).
            </li>
            <li>When connected, this code disappears — you will not re-enter it every 15 minutes.</li>
          </ol>
        </CollapsibleInfo>

        {isPaired && !showNewPairing && (
          <CollapsibleInfo title="Pair Android phone" hint="Mobile vault" accent="violet">
            <p className="text-[11px] leading-relaxed text-violet-100/80">
              Install the APK from{' '}
              <a href="/mobile" className="text-violet-200 underline">
                doxxedcrypto.digital/mobile
              </a>
              , open <strong className="text-white">Founder Node</strong> in the app (or{' '}
              <a href="/settings/builder" className="text-violet-200 underline">
                /settings/builder
              </a>
              ), paste the code under <strong className="text-white">Android vault</strong>.
            </p>
          </CollapsibleInfo>
        )}

        {isPaired && !showNewPairing && !anyOnline && (
          <CollapsibleInfo title="Paired on website but desktop not syncing" hint="Troubleshooting" accent="emerald">
            <p className="text-xs leading-relaxed text-zinc-300">
              The cloud only knows your machine was linked before. Sync stays offline until the{' '}
              <strong className="text-white">Founder Node tray app</strong> is open and heartbeating (~every 60s).
            </p>
            <ol className="mt-2 list-decimal space-y-1 pl-4 text-xs text-zinc-300">
              <li>
                Open <strong className="text-white">Founder Node</strong> from the Start Menu — quit extra tray copies
                if you see more than one icon.
              </li>
              <li>
                Click <strong className="text-white">Pair another desktop device</strong> above, generate a{' '}
                <strong className="text-white">new</strong> code, and paste it in the tray app.
              </li>
              <li>
                Right-click the tray icon → <strong className="text-white">Sync now</strong>, then wait for{' '}
                <strong className="text-emerald-300">● online</strong> in status.
              </li>
            </ol>
          </CollapsibleInfo>
        )}
      </div>
    </div>
  );
}
