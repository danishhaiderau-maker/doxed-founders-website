'use client';

export type KnowledgeNodeRow = {
  id: string;
  founderId: string;
  knowledgeType: string;
  content: string;
  parentNodeId: string | null;
  impactScore: number;
  createdAt: string;
  parent?: { id: string; founderId: string; knowledgeType: string } | null;
};

const TYPE_COLORS: Record<string, string> = {
  PLAYBOOK: 'text-emerald-300',
  RESEARCH_NOTE: 'text-sky-300',
  PATTERN: 'text-amber-300',
  FOUNDER_MEMORY_NODE: 'text-violet-300',
  POST_MORTEM: 'text-rose-300',
};

export function KnowledgeGraphViz({
  nodes,
  loading,
}: {
  nodes: KnowledgeNodeRow[];
  loading: boolean;
}) {
  return (
    <section className="rounded-xl border border-zinc-800 bg-zinc-900/40 p-5">
      <h3 className="text-sm font-semibold uppercase tracking-widest text-zinc-400">Knowledge graph</h3>
      <p className="mt-1 text-[11px] text-zinc-600">
        Contributions + downstream reuse. Parent links form the lineage tree.
      </p>

      {loading && nodes.length === 0 ? (
        <p className="mt-3 text-sm text-zinc-500">Loading…</p>
      ) : nodes.length === 0 ? (
        <p className="mt-3 text-sm text-zinc-500">No knowledge nodes yet.</p>
      ) : (
        <ul className="mt-3 space-y-2">
          {nodes.map((n) => (
            <li
              key={n.id}
              className="rounded-lg border border-zinc-800 bg-zinc-950/40 px-3 py-2"
            >
              <div className="flex flex-wrap items-center gap-2">
                <span className={`text-[11px] font-semibold ${TYPE_COLORS[n.knowledgeType] ?? 'text-zinc-300'}`}>
                  {n.knowledgeType}
                </span>
                <span className="text-[11px] text-zinc-500">impact {n.impactScore}</span>
                {n.parentNodeId && (
                  <span className="text-[11px] text-violet-400">↳ reuses {n.parent?.knowledgeType ?? 'node'}</span>
                )}
              </div>
              <p className="mt-1 line-clamp-2 text-xs text-zinc-300">{n.content}</p>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
