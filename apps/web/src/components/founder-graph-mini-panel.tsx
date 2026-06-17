'use client';

import type { FounderGraphNode } from '@dcf/utils';

type Props = {
  miniChain: FounderGraphNode[];
  nodeCount: number;
  updatedAt?: string;
  loading?: boolean;
};

const TYPE_LABEL: Record<string, string> = {
  initiative: 'Goal',
  task: 'Task',
  commit: 'Commit',
  pr: 'PR',
  deploy: 'Deploy',
  founder_update: 'Update',
  decision: 'Decision',
  agent_run: 'Agent',
  vault_doc: 'Vault',
};

function typeIcon(type: string): string {
  switch (type) {
    case 'initiative':
      return '◎';
    case 'commit':
      return '⎇';
    case 'pr':
      return '⇄';
    case 'deploy':
      return '▲';
    case 'founder_update':
      return '◈';
    case 'decision':
      return '◆';
    case 'agent_run':
      return '⚡';
    case 'vault_doc':
      return '🔒';
    default:
      return '·';
  }
}

export function FounderGraphMiniPanel({ miniChain, nodeCount, updatedAt, loading }: Props) {
  if (loading) {
    return (
      <div className="rounded-xl border border-zinc-800/80 bg-zinc-900/40 p-4">
        <p className="text-xs font-semibold uppercase tracking-wider text-zinc-500">Founder Graph</p>
        <p className="mt-3 text-[11px] text-zinc-600">Assembling chain…</p>
      </div>
    );
  }

  if (miniChain.length === 0) {
    return (
      <div className="rounded-xl border border-zinc-800/80 bg-zinc-900/40 p-4">
        <p className="text-xs font-semibold uppercase tracking-wider text-zinc-500">Founder Graph</p>
        <p className="mt-2 text-[11px] text-zinc-500">
          Connect GitHub or ship an update — Brain will link initiative → commits → deploys here.
        </p>
      </div>
    );
  }

  return (
    <div className="rounded-xl border border-cyan-500/20 bg-gradient-to-br from-cyan-950/15 to-zinc-950/80 p-4">
      <div className="flex items-center justify-between gap-2">
        <p className="text-xs font-semibold uppercase tracking-wider text-cyan-400/90">Founder Graph</p>
        <span className="text-[10px] text-zinc-600">{nodeCount} nodes</span>
      </div>
      <ol className="mt-3 space-y-2">
        {miniChain.map((node, i) => (
          <li key={node.id} className="flex gap-2 text-[11px]">
            <span className="flex w-4 shrink-0 flex-col items-center text-zinc-600">
              <span className="text-[10px]">{typeIcon(node.type)}</span>
              {i < miniChain.length - 1 ? (
                <span className="mt-0.5 h-full w-px flex-1 bg-zinc-800" aria-hidden />
              ) : null}
            </span>
            <div className="min-w-0 flex-1 pb-1">
              <p className="text-[10px] uppercase tracking-wide text-zinc-600">
                {TYPE_LABEL[node.type] ?? node.type}
              </p>
              {node.href ? (
                <a
                  href={node.href}
                  target="_blank"
                  rel="noreferrer"
                  className="mt-0.5 block truncate font-medium text-cyan-100/90 hover:underline"
                >
                  {node.label}
                </a>
              ) : (
                <p className="mt-0.5 line-clamp-2 font-medium text-zinc-200">{node.label}</p>
              )}
              {node.at ? (
                <p className="mt-0.5 text-[10px] text-zinc-600">{node.at.slice(0, 10)}</p>
              ) : null}
            </div>
          </li>
        ))}
      </ol>
      {updatedAt ? (
        <p className="mt-2 text-[10px] text-zinc-600">Updated {updatedAt.slice(0, 16).replace('T', ' ')}</p>
      ) : null}
    </div>
  );
}
