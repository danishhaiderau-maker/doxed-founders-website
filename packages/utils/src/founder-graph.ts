/** Phase 4 — Universal Founder Graph (initiative → commit → PR → deploy → update). */

import { getPlatformToggles, normalizePlatformConnections, type PlatformConnectionsMap } from './platform-connections';
import type { CommitSignal } from './commit-intelligence';
import { filterCommitsForIntelligence } from './commit-intelligence';
import type { FounderMemoryGraph } from './founder-memory-graph';
import type { FounderDecisionEntry } from './founder-decision-log';

export type FounderGraphNodeType =
  | 'initiative'
  | 'task'
  | 'commit'
  | 'pr'
  | 'deploy'
  | 'founder_update'
  | 'decision'
  | 'agent_run'
  | 'vault_doc';

export type FounderGraphNode = {
  id: string;
  type: FounderGraphNodeType;
  label: string;
  detail?: string;
  href?: string;
  at?: string;
  source?: string;
};

export type FounderGraphEdgeRel = 'contains' | 'led_to' | 'merged_into' | 'deployed' | 'published' | 'informed';

export type FounderGraphEdge = {
  from: string;
  to: string;
  rel: FounderGraphEdgeRel;
};

export type FounderGraph = {
  version: 1;
  nodes: FounderGraphNode[];
  edges: FounderGraphEdge[];
  focusInitiativeId?: string | null;
  updatedAt: string;
};

export type FounderGraphBuildInput = {
  projectName: string;
  memoryGraph: FounderMemoryGraph | null;
  currentInitiative?: string | null;
  commits: CommitSignal[];
  pullRequests: { title: string; state: string; url: string; number: number }[];
  recentDeploys: { title: string; at: string; source?: string }[];
  founderUpdates: { headline: string; at: string; id?: string }[];
  decisions: FounderDecisionEntry[];
  agentRun?: {
    task: string;
    status: string;
    prUrl?: string | null;
    startedAt?: string;
  } | null;
  vaultDocs?: { label: string; at?: string }[];
  platformConnections?: PlatformConnectionsMap;
};

function aiContextOn(map: PlatformConnectionsMap, provider: string): boolean {
  return getPlatformToggles(map, provider).aiContext !== false;
}

function nodeId(type: FounderGraphNodeType, key: string): string {
  return `${type}:${key}`;
}

function link(edges: FounderGraphEdge[], from: string, to: string, rel: FounderGraphEdgeRel) {
  if (from === to) return;
  if (edges.some((e) => e.from === from && e.to === to && e.rel === rel)) return;
  edges.push({ from, to, rel });
}

export function buildFounderGraph(input: FounderGraphBuildInput): FounderGraph {
  const nodes: FounderGraphNode[] = [];
  const edges: FounderGraphEdge[] = [];
  const toggles = input.platformConnections ?? {};
  const githubOn = aiContextOn(toggles, 'github');
  const hostOn =
    aiContextOn(toggles, 'vercel') ||
    aiContextOn(toggles, 'railway') ||
    aiContextOn(toggles, 'render') ||
    aiContextOn(toggles, 'neon');
  const vaultOn = aiContextOn(toggles, 'founder_node');

  const initiativeLabel =
    input.currentInitiative?.trim() ||
    input.memoryGraph?.current_sprint?.trim() ||
    input.memoryGraph?.active_goal?.trim() ||
    input.projectName;
  const initiativeId = nodeId('initiative', initiativeLabel.slice(0, 80));
  nodes.push({
    id: initiativeId,
    type: 'initiative',
    label: initiativeLabel.slice(0, 120),
    detail: input.memoryGraph?.current_task ?? undefined,
    source: 'brain',
    at: input.memoryGraph?.updated_at,
  });

  if (input.memoryGraph?.current_task?.trim()) {
    const taskId = nodeId('task', input.memoryGraph.current_task.slice(0, 60));
    nodes.push({
      id: taskId,
      type: 'task',
      label: input.memoryGraph.current_task.slice(0, 120),
      source: 'memory_graph',
    });
    link(edges, initiativeId, taskId, 'contains');
  }

  if (githubOn) {
    const signalCommits = filterCommitsForIntelligence(input.commits);
    for (const c of signalCommits.slice(0, 12)) {
      const id = nodeId('commit', c.sha?.slice(0, 12) ?? c.message.slice(0, 20));
      nodes.push({
        id,
        type: 'commit',
        label: c.message.slice(0, 100),
        detail: c.sha?.slice(0, 7),
        at: c.date,
        source: 'github',
      });
      link(edges, initiativeId, id, 'led_to');
    }

    for (const pr of input.pullRequests.slice(0, 8)) {
      const id = nodeId('pr', String(pr.number));
      nodes.push({
        id,
        type: 'pr',
        label: `#${pr.number} ${pr.title}`.slice(0, 120),
        href: pr.url,
        detail: pr.state,
        source: 'github',
      });
      link(edges, initiativeId, id, 'led_to');
      const relatedCommit = signalCommits.find((c) =>
        c.message.toLowerCase().includes(`#${pr.number}`),
      );
      if (relatedCommit?.sha) {
        link(edges, nodeId('commit', relatedCommit.sha.slice(0, 12)), id, 'merged_into');
      }
    }
  }

  if (hostOn) {
    for (const d of input.recentDeploys.slice(0, 6)) {
      const id = nodeId('deploy', d.at + d.title.slice(0, 20));
      nodes.push({
        id,
        type: 'deploy',
        label: d.title.slice(0, 120),
        at: d.at,
        source: d.source ?? 'deploy',
      });
      link(edges, initiativeId, id, 'deployed');
      const openPr = input.pullRequests.find((p) => p.state === 'open');
      if (openPr) {
        link(edges, nodeId('pr', String(openPr.number)), id, 'deployed');
      }
    }
  }

  for (const u of input.founderUpdates.slice(0, 5)) {
    const id = nodeId('founder_update', u.id ?? u.headline.slice(0, 24));
    nodes.push({
      id,
      type: 'founder_update',
      label: u.headline.slice(0, 120),
      at: u.at,
      source: 'founder_os',
    });
    link(edges, initiativeId, id, 'published');
  }

  for (const d of input.decisions.slice(0, 6)) {
    const id = nodeId('decision', d.id);
    nodes.push({
      id,
      type: 'decision',
      label: d.decision.slice(0, 120),
      detail: d.reason?.slice(0, 80),
      at: d.date,
      source: d.source ?? 'decision_log',
    });
    link(edges, initiativeId, id, 'informed');
  }

  if (input.agentRun?.task) {
    const id = nodeId('agent_run', input.agentRun.startedAt ?? 'active');
    nodes.push({
      id,
      type: 'agent_run',
      label: input.agentRun.task.slice(0, 120),
      detail: input.agentRun.status,
      href: input.agentRun.prUrl ?? undefined,
      at: input.agentRun.startedAt,
      source: 'agent_runtime',
    });
    link(edges, initiativeId, id, 'led_to');
  }

  if (vaultOn) {
    for (const doc of input.vaultDocs?.slice(0, 4) ?? []) {
      const id = nodeId('vault_doc', doc.label.slice(0, 30));
      nodes.push({
        id,
        type: 'vault_doc',
        label: doc.label.slice(0, 120),
        at: doc.at,
        source: 'founder_vault',
      });
      link(edges, initiativeId, id, 'informed');
    }
  }

  return {
    version: 1,
    nodes,
    edges,
    focusInitiativeId: initiativeId,
    updatedAt: new Date().toISOString(),
  };
}

export function parseFounderGraph(raw: unknown): FounderGraph | null {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null;
  const o = raw as Record<string, unknown>;
  if (!Array.isArray(o.nodes) || !Array.isArray(o.edges)) return null;
  return {
    version: 1,
    nodes: o.nodes as FounderGraphNode[],
    edges: o.edges as FounderGraphEdge[],
    focusInitiativeId: typeof o.focusInitiativeId === 'string' ? o.focusInitiativeId : null,
    updatedAt: typeof o.updatedAt === 'string' ? o.updatedAt : new Date().toISOString(),
  };
}

export function formatFounderGraphForPrompt(graph: FounderGraph | null, maxNodes = 14): string | null {
  if (!graph || graph.nodes.length === 0) return null;
  const focusId = graph.focusInitiativeId;
  const ordered: FounderGraphNode[] = [];
  const seen = new Set<string>();

  function walk(id: string | undefined | null) {
    if (!id || seen.has(id)) return;
    const node = graph!.nodes.find((n) => n.id === id);
    if (!node) return;
    seen.add(id);
    ordered.push(node);
    for (const e of graph!.edges.filter((x) => x.from === id)) {
      walk(e.to);
    }
  }

  walk(focusId);
  for (const n of graph.nodes) {
    if (!seen.has(n.id) && ordered.length < maxNodes) {
      ordered.push(n);
      seen.add(n.id);
    }
  }

  const chain = ordered.slice(0, maxNodes).map((n) => {
    const when = n.at ? ` (${n.at.slice(0, 10)})` : '';
    const src = n.source ? ` · ${n.source}` : '';
    return `- **${n.type}** ${n.label}${when}${src}`;
  });

  return ['## Founder Graph (connected chain)', ...chain].join('\n');
}

export function getFounderGraphMiniChain(graph: FounderGraph | null, limit = 8): FounderGraphNode[] {
  if (!graph) return [];
  const excerpt = formatFounderGraphForPrompt(graph, limit);
  if (!excerpt) return [];
  const focusId = graph.focusInitiativeId;
  const out: FounderGraphNode[] = [];
  const seen = new Set<string>();
  function walk(id: string | undefined | null) {
    if (!id || seen.has(id) || out.length >= limit) return;
    const node = graph!.nodes.find((n) => n.id === id);
    if (!node) return;
    seen.add(id);
    out.push(node);
    for (const e of graph!.edges.filter((x) => x.from === id)) walk(e.to);
  }
  walk(focusId);
  return out;
}

export function mergePlatformConnectionsFromSettings(raw: unknown): PlatformConnectionsMap {
  return normalizePlatformConnections(raw);
}
