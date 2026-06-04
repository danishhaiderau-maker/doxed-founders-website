/** Sprint 6 — sealed credential tiers and unwrap purpose guards (shared API + web). */

export type SecretsSealTier = 'platform_encrypted' | 'phala_inference_only' | 'cvm_sealed';

/** Runtime unwrap path (P2): Neon AES vs Phala CVM HTTP. */
export type SecretsUnwrapPath = 'platform_encrypted' | 'cvm_sealed';

export type SecretUnwrapPurpose =
  | 'phala_inference'
  | 'phala_attestation'
  | 'copilot_llm'
  | 'openhands_dispatch'
  | 'cursor_dispatch'
  | 'github_token'
  | 'connect_verify'
  | 'status_probe';

export type SecretsSealMetadata = {
  version: 1;
  tier: SecretsSealTier;
  storedAt: string;
};

export const SECRETS_SEAL_METADATA_KEY = 'secretsSeal';

export const PHALA_SEALED_ALLOWED_PURPOSES: ReadonlySet<SecretUnwrapPurpose> = new Set([
  'phala_inference',
  'phala_attestation',
  'connect_verify',
  'status_probe',
]);

export function secretsTierForProvider(provider: string): SecretsSealTier {
  return provider === 'phala' ? 'phala_inference_only' : 'platform_encrypted';
}

export function readSecretsSeal(metadata: unknown): SecretsSealMetadata | null {
  if (!metadata || typeof metadata !== 'object') return null;
  const seal = (metadata as Record<string, unknown>)[SECRETS_SEAL_METADATA_KEY];
  if (!seal || typeof seal !== 'object') return null;
  const row = seal as Record<string, unknown>;
  const tier = row.tier;
  if (
    tier !== 'platform_encrypted' &&
    tier !== 'phala_inference_only' &&
    tier !== 'cvm_sealed'
  ) {
    return null;
  }
  const storedAt = typeof row.storedAt === 'string' ? row.storedAt : '';
  return { version: 1, tier, storedAt };
}

export function resolveSecretsTier(provider: string, metadata: unknown): SecretsSealTier {
  return readSecretsSeal(metadata)?.tier ?? secretsTierForProvider(provider);
}

export function withSecretsSeal(
  metadata: Record<string, unknown> | null | undefined,
  tier: SecretsSealTier,
): Record<string, unknown> {
  const base =
    metadata && typeof metadata === 'object' && !Array.isArray(metadata)
      ? { ...metadata }
      : {};
  const seal: SecretsSealMetadata = {
    version: 1,
    tier,
    storedAt: new Date().toISOString(),
  };
  return { ...base, [SECRETS_SEAL_METADATA_KEY]: seal };
}

export class SecretsAccessDeniedError extends Error {
  constructor(
    message: string,
    readonly tier: SecretsSealTier,
    readonly purpose: SecretUnwrapPurpose,
  ) {
    super(message);
    this.name = 'SecretsAccessDeniedError';
  }
}

export function assertUnwrapAllowed(tier: SecretsSealTier, purpose: SecretUnwrapPurpose): void {
  if (tier === 'phala_inference_only' && !PHALA_SEALED_ALLOWED_PURPOSES.has(purpose)) {
    throw new SecretsAccessDeniedError(
      'This Phala API key is sealed for confidential inference only — it cannot be used for other integrations.',
      tier,
      purpose,
    );
  }
}

export type SecretsStorageModeKey = 'PLATFORM_ENCRYPTED' | 'PHALA_SEALED';

export function secretsStorageModeLabel(mode: SecretsStorageModeKey | string | null | undefined): string {
  if (mode === 'PHALA_SEALED') return 'Phala-sealed preference';
  return 'Platform encrypted (AES-256)';
}

export function secretsTierLabel(tier: SecretsSealTier): string {
  if (tier === 'phala_inference_only') return 'Phala inference only';
  if (tier === 'cvm_sealed') return 'CVM-sealed preference';
  return 'Encrypted at rest';
}

export function secretsTierForStorageMode(
  provider: string,
  mode: SecretsStorageModeKey | string | null | undefined,
): SecretsSealTier {
  if (provider === 'phala') return 'phala_inference_only';
  if (mode === 'PHALA_SEALED') return 'cvm_sealed';
  return secretsTierForProvider(provider);
}
