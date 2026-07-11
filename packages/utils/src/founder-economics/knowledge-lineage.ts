/**
 * Knowledge Lineage — track knowledge creation + downstream reuse + compounding.
 *
 * The Knowledge Graph records every reusable knowledge node a founder
 * contributes (playbook, research note, pattern, Founder Memory entry). When
 * another founder builds on a node, the original contributor earns an Impact
 * DDollar grant (KNOWLEDGE_REUSED_IMPACT), and that reuse is recorded as a
 * child node so lineage is queryable.
 *
 * Compounding rule: when a child node is itself reused, the original parent
 * contributor earns a reduced Impact grant (decay = 50% per hop). This rewards
 * foundational knowledge without flooding the ledger.
 */

export type KnowledgeType =
  | 'PLAYBOOK'
  | 'RESEARCH_NOTE'
  | 'PATTERN'
  | 'FOUNDER_MEMORY_NODE'
  | 'POST_MORTEM';

export type KnowledgeNode = {
  id: string;
  founderId: string;
  knowledgeType: KnowledgeType;
  content: string;
  /** Optional parent — non-null when this node is a reuse of another. */
  parentNodeId?: string | null;
  /** Compound impact score — increments on each downstream reuse. */
  impactScore: number;
  createdAt: string;
};

export type KnowledgeReuseEvent = {
  childNodeId: string;
  parentNodeId: string;
  reusingFounderId: string;
  /** Hop distance from the original root node (1 = direct reuse, 2 = grandchild, ...). */
  hop: number;
  /** DDollar granted to the parent's contributor for this reuse. */
  ddollarGrant: number;
  createdAt: string;
};

/** Decay per hop — grandchild reuse grants 50% of child reuse to the root. */
export const KNOWLEDGE_IMPACT_DECAY_PER_HOP = 0.5;

/** Range for the KNOWLEDGE_REUSED_IMPACT DDollar grant (per ddollar-scoring.ts). */
export const KNOWLEDGE_REUSE_GRANT_MIN = 1_000;
export const KNOWLEDGE_REUSE_GRANT_MAX = 10_000;

/**
 * Compute the Impact DDollar grant for a reuse event.
 *
 * Direct reuse (hop 1) → full grant in the spec range.
 * Each additional hop decays by KNOWLEDGE_IMPACT_DECAY_PER_HOP.
 * Returns 0 once the grant would drop below 100 DDollar (noise floor).
 */
export function computeReuseDdollarGrant(
  hop: number,
  baseGrant: number,
): number {
  if (hop < 1) return 0;
  const decayed = baseGrant * Math.pow(KNOWLEDGE_IMPACT_DECAY_PER_HOP, hop - 1);
  if (decayed < 100) return 0;
  return Math.round(decayed);
}

/**
 * Walk a lineage (parent → child → grandchild ...) and return the ordered
 * list of node ids from the leaf up to the root. Used by the dashboard to
 * render the knowledge graph visualization.
 */
export function lineagePath(
  nodeId: string,
  nodesById: Map<string, KnowledgeNode>,
): string[] {
  const path: string[] = [];
  let current: string | null | undefined = nodeId;
  const guard = new Set<string>();
  while (current && !guard.has(current)) {
    guard.add(current);
    path.push(current);
    const node = nodesById.get(current);
    if (!node) break;
    current = node.parentNodeId;
  }
  return path;
}

/** Count direct + transitive reuses rooted at `nodeId`. */
export function countDownstreamReuses(
  nodeId: string,
  reuseEvents: KnowledgeReuseEvent[],
): number {
  return reuseEvents.filter((e) => e.parentNodeId === nodeId).length;
}

/**
 * Sum all Impact DDollar granted to a founder across their knowledge nodes.
 * Used by the dashboard to show "Knowledge Impact" as a GDP component.
 */
export function sumImpactDdollarForFounder(
  founderId: string,
  nodes: KnowledgeNode[],
  reuseEvents: KnowledgeReuseEvent[],
  nodesById: Map<string, KnowledgeNode>,
): number {
  const owned = nodes.filter((n) => n.founderId === founderId);
  let total = 0;
  for (const node of owned) {
    for (const event of reuseEvents) {
      if (event.parentNodeId === node.id) {
        total += event.ddollarGrant;
      }
    }
  }
  void nodesById; // kept on signature for future lineage lookups
  return total;
}
