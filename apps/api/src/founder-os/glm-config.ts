/** GLM z.ai Coding Plan — OpenAI-compatible endpoint (not the general /api/paas/v4 URL). */
export const GLM_PROMO_BASE_URL = 'https://api.z.ai/api/coding/paas/v4';
export const GLM_PROMO_DEFAULT_MODEL = 'glm-5.2';

/**
 * GLM-4V vision preprocessing endpoint. The z.ai **general** API hosts the
 * multimodal models; the Coding Plan endpoint above does NOT accept image
 * inputs. Defaults can be overridden by env. See docs/PRODUCTION-AI-KEYS.md §3.
 */
export const GLM_VISION_DEFAULT_BASE_URL = 'https://api.z.ai/api/paas/v4';
export const GLM_VISION_DEFAULT_MODEL = 'glm-4v';

export function getGlmApiBaseUrl(): string {
  const raw = process.env.GLM_API_BASE?.trim();
  return raw && raw.length > 0 ? raw.replace(/\/$/, '') : GLM_PROMO_BASE_URL;
}

export function getGlmDefaultModel(): string {
  const raw = process.env.AI_RUNTIME_CODE_MODEL?.trim();
  return raw && raw.length > 0 ? raw : GLM_PROMO_DEFAULT_MODEL;
}

/**
 * Base URL for GLM-4V vision preprocessing. Falls back to the general z.ai
 * endpoint unless FOUNDER_VISION_BASE_URL is set. Reuses GLM_API_KEY unless
 * FOUNDER_VISION_API_KEY is set (rare — only if vision should bill elsewhere).
 */
export function getVisionApiBaseUrl(): string {
  const raw = process.env.FOUNDER_VISION_BASE_URL?.trim();
  return raw && raw.length > 0 ? raw.replace(/\/$/, '') : GLM_VISION_DEFAULT_BASE_URL;
}

/** GLM-4V model id, overridable via FOUNDER_VISION_MODEL. */
export function getVisionModel(): string {
  const raw = process.env.FOUNDER_VISION_MODEL?.trim();
  return raw && raw.length > 0 ? raw : GLM_VISION_DEFAULT_MODEL;
}
