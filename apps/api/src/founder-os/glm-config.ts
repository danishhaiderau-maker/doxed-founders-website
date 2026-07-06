/** GLM z.ai Coding Plan — OpenAI-compatible endpoint (not the general /api/paas/v4 URL). */
export const GLM_PROMO_BASE_URL = 'https://api.z.ai/api/coding/paas/v4';
export const GLM_PROMO_DEFAULT_MODEL = 'glm-5.2';

export function getGlmApiBaseUrl(): string {
  const raw = process.env.GLM_API_BASE?.trim();
  return raw && raw.length > 0 ? raw.replace(/\/$/, '') : GLM_PROMO_BASE_URL;
}

export function getGlmDefaultModel(): string {
  const raw = process.env.AI_RUNTIME_CODE_MODEL?.trim();
  return raw && raw.length > 0 ? raw : GLM_PROMO_DEFAULT_MODEL;
}
