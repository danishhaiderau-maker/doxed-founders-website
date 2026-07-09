/**
 * Shared types for the Founder OS Phone Remote UI.
 *
 * The phone UI lets a founder control / mirror their desktop IDE from a phone
 * browser. Phase 2 wires the first half of the bridge:
 *
 *   Phone Browser ←(SSE)→ Founder OS Cloud API ←(WebSocket)→ Founder Node ←→ IDE
 *
 * The Founder Node ↔ IDE WebSocket bridge is a later phase, so for now the
 * phone UI talks directly to the AI Gateway (`/api/v1/chat/phone-completions`)
 * and lists connected nodes from `/api/founder-node/status`. See
 * docs/FOUNDER-IDE-FORK-PLAN.md §8 and apps/web/src/app/phone/page.tsx.
 */

/** A single connected Founder Node / IDE host, from GET /api/founder-node/status. */
export type ConnectedNode = {
  id: string;
  nodeId: string;
  label: string;
  status: 'online' | 'offline';
  lastSeenAt: string | null;
  ramGb?: number | null;
  storageGb?: number | null;
  storageFreeGb?: number | null;
  vaultHealthy?: boolean | null;
  platform?: string | null;
  appVersion?: string | null;
};

export type NodeStatusResponse = {
  nodes: ConnectedNode[];
};

/**
 * The `founderOs` metadata emitted as a leading SSE `data:` line by
 * /api/v1/chat/phone-completions (and /v1/chat/completions when
 * `founder_os_metadata: true`). Carries the route decision + DDollar cost so
 * the phone UI can render per-turn route transparency. See
 * ai-proxy.controller.ts §streamChat.
 */
export type FounderOsRouteMetadata = {
  requestId: string;
  tier: string;
  provider: string;
  model: string;
  ddollarCost: number;
};

/** A chat message in the phone conversation. */
export type PhoneChatMessage = {
  id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  /** Route metadata for assistant turns (provider / model / tier / DDollar cost). */
  route?: FounderOsRouteMetadata;
  /** True while an assistant turn is streaming. */
  streaming?: boolean;
};

/** The model aliases the AI Gateway exposes (apps/api ai-proxy.constants.ts). */
export const PHONE_MODEL_ALIASES = [
  { id: 'founder-os-auto', label: 'Auto', hint: 'Let the Routing Engine decide' },
  { id: 'founder-os-code', label: 'Code', hint: 'Architect — best for code' },
  { id: 'founder-os-reasoning', label: 'Reasoning', hint: 'Balanced — step-by-step' },
  { id: 'founder-os-fast', label: 'Fast', hint: 'Turbo — quick answers' },
] as const;

export type PhoneModelId = (typeof PHONE_MODEL_ALIASES)[number]['id'];
