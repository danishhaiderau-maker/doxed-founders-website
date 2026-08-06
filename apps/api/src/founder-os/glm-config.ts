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
 * Base URL for vision preprocessing. Falls back to the general z.ai
 * endpoint unless FOUNDER_VISION_BASE_URL is set. Used for both GLM-4V and
 * the Gemini OpenAI-compatible endpoint when FOUNDER_VISION_PROVIDER=gemini.
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
 * Resolve the vision preprocessor API key from env, independent of the GLM
 * text-model credential. Precedence (matches docs/PRODUCTION-AI-KEYS.md §3):
 *   1. FOUNDER_VISION_API_KEY — explicit override for vision billing.
 *   2. GEMINI_API_KEY         — used when FOUNDER_VISION_PROVIDER=gemini.
 *   3. GLM_API_KEY            — legacy fallback (preserves prior behaviour).
 *
 * Returns null when none are set; the caller then falls through to its own
 * promo / routing resolvers (see VisionPreprocessorService.describeImage).
 */
export function getVisionApiKey(): string | null {
  const explicit = process.env.FOUNDER_VISION_API_KEY?.trim();
  if (explicit && explicit.length > 0) return explicit;
  const gemini = process.env.GEMINI_API_KEY?.trim();
  if (gemini && gemini.length > 0) return gemini;
  const glm = process.env.GLM_API_KEY?.trim();
  if (glm && glm.length > 0) return glm;
  return null;
}
