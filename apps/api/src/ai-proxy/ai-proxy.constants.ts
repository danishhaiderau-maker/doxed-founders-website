/**
 * Founder OS AI Proxy — server-side constants.
 *
 * The shared aliases + DDollar cost map live in `@dcf/utils` so the founder
 * node, web dashboard, and API all agree. This file holds API-only constants
 * that don't belong in the shared package (env var names, soft caps, prompt
 * boundaries).
 */

export const FOUNDER_OS_AUTO_MODEL = 'founder-os-auto';

/**
 * Models the proxy will accept and route internally. Any other model string
 * is rejected with 400 so founders know they mistyped the alias.
 */
export const MODEL_ALIASES = [
  FOUNDER_OS_AUTO_MODEL,
  'founder-os-code',
  'founder-os-reasoning',
  'founder-os-fast',
] as const;

export type ModelAlias = (typeof MODEL_ALIASES)[number];

/**
 * Soft cap on prompt tokens. We don't hard-reject (some long-context models
 * can handle much more), but we log anything over this so we can spot abuse.
 */
export const MAX_PROMPT_TOKENS_SOFT_CAP = 64_000;

/**
 * Feature flag — when true, the proxy routes through the new Routing Engine
 * v2 (Capability Registry + Flight Recorder). When false (default), it uses
 * the legacy ModelRouterService. Lets us ship Phase 1 without breaking the
 * existing flow.
 */
export const USE_ROUTING_ENGINE_V2 =
  process.env.USE_ROUTING_ENGINE_V2 === 'true';
