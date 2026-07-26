/**
 * Founder AI Runtime is the mandatory V1 provider gateway. An explicit
 * `false` remains as an emergency rollback; missing or malformed values do
 * not silently reopen legacy provider paths.
 */
export function isFounderAiRuntimeEnabled(
  env: NodeJS.ProcessEnv = process.env,
): boolean {
  return env.AI_RUNTIME_ENABLED?.trim().toLowerCase() !== 'false';
}

/**
 * Strict mode is intended for release validation and controlled production
 * rollout. It blocks unscoped provider egress before the network request.
 */
export function isProviderEgressEnforcementStrict(
  env: NodeJS.ProcessEnv = process.env,
): boolean {
  return env.PROVIDER_EGRESS_ENFORCEMENT?.trim().toLowerCase() === 'strict';
}
