'use client';

import { useCallback, useEffect, useState } from 'react';
import { apiUrl } from '@/lib/api-base';
import type { ConnectedNode, NodeStatusResponse } from './types';

type Props = {
  accessToken: string;
  /** Currently selected node id (controls highlight). */
  activeNodeId: string | null;
  /** Called when the founder picks a node to switch the phone context to. */
  onSelect: (node: ConnectedNode) => void;
  /** Auto-refresh interval (ms). Defaults to 20s — matches heartbeat cadence. */
  refreshMs?: number;
};

/**
 * Lists connected IDEs / Founder Nodes from /api/founder-node/status.
 *
 * Each row is a paired desktop that can be the "active" IDE the phone is
 * controlling. Online nodes are selectable; offline nodes are greyed out (you
 * can't switch to an IDE that isn't running). Per the user's vision, if no
 * other IDE is connected we prompt to connect one in Founder IDE on desktop.
 */
export function ConnectedIdes({
  accessToken,
  activeNodeId,
  onSelect,
  refreshMs = 20_000,
}: Props) {
  const [nodes, setNodes] = useState<ConnectedNode[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const res = await fetch(apiUrl('/api/founder-node/status'), {
        headers: { Authorization: `Bearer ${accessToken}` },
      });
      if (!res.ok) {
        setError(`Failed to load connected IDEs (${res.status})`);
        return;
      }
      const data = (await res.json()) as NodeStatusResponse;
      setNodes(Array.isArray(data?.nodes) ? data.nodes : []);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load connected IDEs');
    } finally {
      setLoading(false);
    }
  }, [accessToken]);

  useEffect(() => {
    void load();
    const timer = setInterval(() => void load(), refreshMs);
    return () => clearInterval(timer);
  }, [load, refreshMs]);

  const onlineCount = nodes.filter((n) => n.status === 'online').length;

  if (loading) {
    return (
      <div className="rounded-xl border border-zinc-800 bg-zinc-950/40 p-4 text-xs text-zinc-500">
        Checking connected IDEs…
      </div>
    );
  }

  if (error) {
    return (
      <div className="rounded-xl border border-rose-500/30 bg-rose-950/15 p-4 text-xs text-rose-200">
        {error}
      </div>
    );
  }

  if (nodes.length === 0) {
    return (
      <div className="rounded-xl border border-amber-500/30 bg-amber-950/15 p-4 text-xs text-amber-100">
        <p className="font-semibold">No connected IDEs yet</p>
        <p className="mt-1 text-amber-200/80">
          Install Founder IDE on your desktop to connect its workspace or another supported editor. Then it will appear
          here and you can switch to it from your phone.
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <p className="text-[10px] font-semibold uppercase tracking-[0.18em] text-zinc-500">
          Connected IDEs
        </p>
        <span className="text-[10px] text-zinc-600">
          {onlineCount} online · {nodes.length} total
        </span>
      </div>
      <ul className="space-y-1.5">
        {nodes.map((node) => {
          const online = node.status === 'online';
          const active = activeNodeId === node.nodeId;
          return (
            <li key={node.nodeId}>
              <button
                type="button"
                disabled={!online}
                onClick={() => onSelect(node)}
                className={[
                  'flex w-full items-center gap-3 rounded-xl border px-3 py-2.5 text-left transition',
                  active
                    ? 'border-emerald-400/50 bg-emerald-500/10 text-emerald-50'
                    : online
                      ? 'border-zinc-800 bg-zinc-950/50 text-zinc-200 hover:border-zinc-600'
                      : 'border-zinc-800/60 bg-zinc-950/30 text-zinc-600 cursor-not-allowed',
                ].join(' ')}
                title={
                  online
                    ? `Switch to ${node.label}`
                    : 'Offline — open Founder Node on this machine to switch to it'
                }
              >
                <span
                  className={[
                    'inline-block h-2 w-2 shrink-0 rounded-full',
                    online ? 'bg-emerald-400' : 'bg-zinc-600',
                  ].join(' ')}
                  aria-hidden
                />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm font-medium">
                    {node.label || 'Unnamed machine'}
                  </span>
                  <span className="block truncate text-[10px] text-zinc-500">
                    {describeNode(node)}
                  </span>
                </span>
                {active && (
                  <span className="rounded-full bg-emerald-500/20 px-2 py-0.5 text-[9px] font-bold uppercase tracking-wider text-emerald-200">
                    Active
                  </span>
                )}
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function describeNode(node: ConnectedNode): string {
  const parts: string[] = [];
  if (node.platform) parts.push(prettyPlatform(node.platform));
  if (node.appVersion) parts.push(`v${node.appVersion}`);
  if (node.status === 'online') {
    parts.push('online');
  } else if (node.lastSeenAt) {
    parts.push(`seen ${timeAgo(node.lastSeenAt)}`);
  } else {
    parts.push('offline');
  }
  return parts.join(' · ');
}

function prettyPlatform(p: string): string {
  const lower = p.toLowerCase();
  if (lower.includes('win')) return 'Windows';
  if (lower.includes('mac') || lower.includes('darwin')) return 'macOS';
  if (lower.includes('linux')) return 'Linux';
  return p;
}

function timeAgo(iso: string): string {
  const ms = Date.now() - new Date(iso).getTime();
  if (Number.isNaN(ms)) return '—';
  if (ms < 60_000) return 'just now';
  const mins = Math.floor(ms / 60_000);
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 48) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  return `${days}d ago`;
}
