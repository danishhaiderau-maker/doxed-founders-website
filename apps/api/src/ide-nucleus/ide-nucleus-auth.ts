import { parseFounderNodeAuthHeader } from '@dcf/founder-vault';

export type NucleusAuthClassification =
  | { kind: 'founder-node'; nodeId: string; nodeToken: string }
  | { kind: 'jwt'; token: string }
  | { kind: 'none' };

/**
 * Which credential can read Nucleus (`GET /api/ide/nucleus`):
 *
 * - `Authorization: FounderNode {nodeId}:{nodeToken}` — extension / Founder Node
 * - `Authorization: Bearer fos_{nodeId}:{nodeToken}` — OpenAI-compat node bearer
 * - `Authorization: Bearer <session JWT>` — browser session on `/founder-ide/nucleus`
 *
 * A normal browser JWT is not a Founder Node token. `GET /api/copilot/founder-graph`
 * stays JWT-only; this classifier is only for the IDE read path.
 */
export function classifyNucleusAuthorization(
  header: string | undefined,
): NucleusAuthClassification {
  const trimmed = header?.trim() ?? '';
  const node = parseFounderNodeAuthHeader(trimmed);
  if (node) {
    return { kind: 'founder-node', nodeId: node.nodeId, nodeToken: node.nodeToken };
  }
  if (/^Bearer\s+/i.test(trimmed)) {
    const token = trimmed.replace(/^Bearer\s+/i, '').trim();
    if (token) return { kind: 'jwt', token };
  }
  return { kind: 'none' };
}

export const NUCLEUS_AUTH_HELP =
  'Sign in with a session JWT (Authorization: Bearer) or send Founder Node credentials (Authorization: FounderNode {nodeId}:{token}, or Bearer fos_{nodeId}:{token}).';
