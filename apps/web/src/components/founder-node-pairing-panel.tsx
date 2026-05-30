'use client';

import { useCallback, useEffect, useState } from 'react';
import {
  createFounderNodePairingCode,
  fetchFounderNodeStatus,
  revokeFounderNode,
  type FounderNodeStatusRow,
} from '@/lib/api';

type Props = {
  accessToken: string;
  active: boolean;
};

export function FounderNodePairingPanel({ accessToken, active }: Props) {
  const [nodes, setNodes] = useState<FounderNodeStatusRow[]>([]);
  const [pairingCode, setPairingCode] = useState<string | null>(null);
  const [expiresAt, setExpiresAt] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const status = await fetchFounderNodeStatus(accessToken);
      setNodes(status.nodes);
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

  async function generateCode() {
    setBusy('code');
    setErr(null);
    setMsg(null);
    try {
      const result = await createFounderNodePairingCode(accessToken);
      setPairingCode(result.code);
      setExpiresAt(result.expiresAt);
      setMsg('Enter this code in Founder Node on your PC — expires in 15 minutes.');
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
      setMsg('Node disconnected.');
      await refresh();
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Could not revoke node');
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="mt-4 rounded-lg border border-cyan-500/30 bg-cyan-950/20 p-4">
      <h4 className="text-sm font-semibold text-cyan-100">Founder Node pairing</h4>
      <p className="mt-1 text-xs text-cyan-100/70">
        Install Founder Node on your PC, Mac, or Linux. Your vault stays local — Founder OS only
        receives tiny metadata snapshots (goal, progress, tasks count).
      </p>

      <ol className="mt-3 list-inside list-decimal space-y-1 text-xs text-zinc-300">
        <li>
          Run{' '}
          <code className="rounded bg-zinc-800 px-1 py-0.5">npm run dev:founder-node</code> from the
          repo (or the packaged app when available).
        </li>
        <li>Generate a pairing code below and paste it into Founder Node.</li>
        <li>Choose this storage mode — sync runs automatically every minute when online.</li>
      </ol>

      <div className="mt-4 flex flex-wrap gap-2">
        <button
          type="button"
          disabled={busy === 'code'}
          onClick={generateCode}
          className="rounded-lg bg-cyan-600 px-3 py-1.5 text-xs font-medium text-white disabled:opacity-50"
        >
          {busy === 'code' ? 'Generating…' : 'Generate pairing code'}
        </button>
      </div>

      {pairingCode && (
        <div className="mt-3 rounded-lg border border-cyan-400/40 bg-black/30 p-3 text-center">
          <p className="text-xs text-zinc-400">Pairing code</p>
          <p className="mt-1 font-mono text-2xl font-bold tracking-[0.3em] text-cyan-300">
            {pairingCode}
          </p>
          {expiresAt && (
            <p className="mt-1 text-[10px] text-zinc-500">
              Expires {new Date(expiresAt).toLocaleString()}
            </p>
          )}
        </div>
      )}

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
                {node.ramGb != null && (
                  <span className="ml-2 text-zinc-500">{node.ramGb} GB RAM</span>
                )}
                {node.storageFreeGb != null && node.storageGb != null && (
                  <span className="ml-2 text-zinc-500">
                    {node.storageFreeGb} GB free / {node.storageGb} GB
                  </span>
                )}
                <span
                  className={`ml-2 ${node.vaultHealthy ? 'text-emerald-400' : 'text-red-400'}`}
                >
                  Vault {node.vaultHealthy ? 'healthy' : 'issue'}
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

      {msg && <p className="mt-3 text-xs text-emerald-300">{msg}</p>}
      {err && <p className="mt-3 text-xs text-red-300">{err}</p>}
    </div>
  );
}
