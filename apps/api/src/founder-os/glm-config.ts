/**
 * GLM z.ai Coding Plan — OpenAI-compatible endpoint (not the general /api/paas/v4 URL).
 *
 * IMPORTANT — HARD COST RULE:
 * GLM (Zhipu / z.ai) is cost-prohibitive and is reserved EXCLUSIVELY for the
 * Second Brain critical-review surface (apps/api/src/second-brain/second-brain.service.ts).
 * GLM must NOT be used for general chat traffic, the AI Auto Router, the
 * vision preprocessor fallback, or any other path. DeepSeek handles all
 * general text traffic; Gemini handles vision. See the founder's hard rule:
 * "GLM is very expensive ... it has to be used very carefully, only for the
 * second brain ... otherwise we can not be profitable."
 *
 * This module still exports the GLM base-URL + model helpers so the Second
 * Brain service (and only that service) can reach the z.ai Coding Plan.
 */
export const GLM_PROMO_BASE_URL = 'https://api.z.ai/api/coding/paas/v4';
export const GLM_PROMO_DEFAULT_MODEL = 'glm-5.2';

/**
 * Vision preprocessing endpoint (Gemini by default). The z.ai general API
 * URL is kept only as a base-URL default that can be overridden via
 * FOUNDER_VISION_BASE_URL; it is NOT a GLM fallback. See getVisionApiKey()
 * for the credential precedence.
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
 * Base URL for vision preprocessing. Falls back to the z.ai general endpoint
 * only when FOUNDER_VISION_BASE_URL is unset; in practice the deployment
 * overrides this with the Gemini OpenAI-compatible URL.
 */
export function getVisionApiBaseUrl(): string {
  const raw = process.env.FOUNDER_VISION_BASE_URL?.trim();
  return raw && raw.length > 0 ? raw.replace(/\/$/, '') : GLM_VISION_DEFAULT_BASE_URL;
}

/** Vision model id, overridable via FOUNDER_VISION_MODEL. */
export function getVisionModel(): string {
  const raw = process.env.FOUNDER_VISION_MODEL?.trim();
  return raw && raw.length > 0 ? raw : GLM_VISION_DEFAULT_MODEL;
}

/**
 * Resolve the vision preprocessor API key from env, INDEPENDENT of the GLM
 * text-model credential.
 *
 * Precedence (Gemini-only — GLM is NOT a fallback):
 *   1. FOUNDER_VISION_API_KEY  — explicit override for vision billing.
 *   2. GEMINI_API_KEY          — designated vision provider for general traffic.
 *
 * GLM_API_KEY is intentionally NOT consulted. GLM vision (glm-4v) is not
 * activated on the Zhipu account (HTTP 1211) and GLM is cost-prohibitive.
 * GLM tokens are reserved exclusively for the Second Brain critical-review
 * surface; routing vision traffic through GLM would burn the GLM budget on
 * general requests and break profitability.
 *
 * Returns null when neither key is set; the caller then degrades gracefully
 * (see VisionPreprocessorService.describeImage).
 */
export function getVisionApiKey(): string | null {
  const explicit = process.env.FOUNDER_VISION_API_KEY?.trim();
  if (explicit && explicit.length > 0) return explicit;
  const gemini = process.env.GEMINI_API_KEY?.trim();
  if (gemini && gemini.length > 0) return gemini;
  return null;
}