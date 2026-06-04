/** Phala P2 — CVM-side credential unwrap (shared API + web). */

import type { SecretUnwrapPurpose, SecretsSealTier, SecretsUnwrapPath } from './secrets-storage';

export const PHALA_CVM_SEAL_DOCS_URL = 'https://docs.phala.com/phala-cloud/confidential-ai';

export type { SecretsUnwrapPath };

export type PhalaCvmSealPlatformConfig = {
  configured: boolean;
  unwrapUrlSet: boolean;
  workloadId: string | null;
};

export type PhalaCvmSealCapabilitiesPayload = {
  version: 1;
  platformCvmUnwrapConfigured: boolean;
  unwrapUrlSet: boolean;
  workloadId: string | null;
  docsUrl: string;
};

export type PhalaCvmSealStatusPayload = {
  version: 1;
  cvmUnwrapReady: boolean;
  platformUnwrapConfigured: boolean;
  preferredUnwrapPath: SecretsUnwrapPath;
  activeUnwrapPath: SecretsUnwrapPath;
  checks: Array<{ name: string; ok: boolean; detail: string }>;
  docsUrl: string;
};

/** Server-side env probe (never expose API keys or unwrap URL host secrets). */
export function readPhalaCvmSealPlatformConfig(
  env: NodeJS.ProcessEnv = process.env,
): PhalaCvmSealPlatformConfig {
  const unwrapUrl = env.PHALA_CVM_UNWRAP_URL?.trim() || '';
  const workloadId = env.PHALA_CVM_WORKLOAD_ID?.trim() || null;
  const hasKey = Boolean(env.PHALA_CVM_API_KEY?.trim() || env.PHALA_API_KEY?.trim());
  const unwrapUrlSet = unwrapUrl.length > 0;
  return {
    configured: unwrapUrlSet && hasKey,
    unwrapUrlSet,
    workloadId,
  };
}

export function phalaCvmSealCapabilitiesPayload(
  env: NodeJS.ProcessEnv = process.env,
): PhalaCvmSealCapabilitiesPayload {
  const platform = readPhalaCvmSealPlatformConfig(env);
  return {
    version: 1,
    platformCvmUnwrapConfigured: platform.configured,
    unwrapUrlSet: platform.unwrapUrlSet,
    workloadId: platform.workloadId,
    docsUrl: PHALA_CVM_SEAL_DOCS_URL,
  };
}

/** Purposes allowed to route through CVM unwrap when platform workload is configured. */
export const CVM_UNWRAP_ALLOWED_PURPOSES: ReadonlySet<SecretUnwrapPurpose> = new Set([
  'phala_inference',
  'phala_attestation',
  'copilot_llm',
  'openhands_dispatch',
  'cursor_dispatch',
  'github_token',
  'connect_verify',
  'status_probe',
]);

export function assertCvmUnwrapPurpose(purpose: SecretUnwrapPurpose): void {
  if (!CVM_UNWRAP_ALLOWED_PURPOSES.has(purpose)) {
    throw new Error(`Purpose ${purpose} is not allowed for CVM credential unwrap`);
  }
}

export function resolveActiveUnwrapPath(platformConfigured: boolean): SecretsUnwrapPath {
  return platformConfigured ? 'cvm_sealed' : 'platform_encrypted';
}

export function resolvePreferredUnwrapPath(input: {
  platformConfigured: boolean;
  secretsStorageMode?: string | null;
}): SecretsUnwrapPath {
  if (!input.platformConfigured) return 'platform_encrypted';
  if (input.secretsStorageMode === 'PHALA_SEALED') return 'cvm_sealed';
  return 'cvm_sealed';
}

export function unwrapPathLabel(path: SecretsUnwrapPath): string {
  return path === 'cvm_sealed'
    ? 'Phala CVM (TEE unwrap)'
    : 'Platform AES-256 (Neon relay)';
}

export function assessCvmSealReadiness(input: {
  platformConfigured: boolean;
  jwtSecretSet: boolean;
}): { cvmUnwrapReady: boolean; checks: PhalaCvmSealStatusPayload['checks'] } {
  const checks: PhalaCvmSealStatusPayload['checks'] = [
    {
      name: 'platform_jwt',
      ok: input.jwtSecretSet,
      detail: input.jwtSecretSet
        ? 'JWT_SECRET set — local AES fallback available'
        : 'JWT_SECRET missing — cannot fall back to platform decrypt',
    },
    {
      name: 'cvm_unwrap_url',
      ok: input.platformConfigured,
      detail: input.platformConfigured
        ? 'PHALA_CVM_UNWRAP_URL + API key configured'
        : 'Set PHALA_CVM_UNWRAP_URL on Railway for TEE-side unwrap',
    },
  ];
  const cvmUnwrapReady = input.platformConfigured && input.jwtSecretSet;
  return { cvmUnwrapReady, checks };
}

export function cvmSealTierLabel(tier: SecretsSealTier): string {
  if (tier === 'phala_inference_only') return 'Phala inference only';
  return 'Encrypted at rest';
}
