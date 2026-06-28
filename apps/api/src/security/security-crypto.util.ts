import { createCipheriv, createDecipheriv, randomBytes, scryptSync } from 'crypto';

function deriveKey(): Buffer {
  // Prefer a dedicated credential encryption key so a JWT_SECRET leak alone cannot decrypt
  // stored tokens. Falls back to JWT_SECRET when CREDENTIAL_ENCRYPTION_KEY is unset (dev or
  // pre-migration). decryptSecret also tries the legacy JWT-derived key for already-stored
  // ciphertext encrypted before the split.
  const secret =
    process.env.CREDENTIAL_ENCRYPTION_KEY?.trim() ||
    process.env.JWT_SECRET?.trim() ||
    'dev-secret-change-in-production';
  return scryptSync(secret, 'dcf-security-v1', 32);
}

function deriveLegacyKey(): Buffer | null {
  // Only relevant when a dedicated credential key is set AND it differs from JWT_SECRET —
  // in that case JWT-derived ciphertext is legacy and must still decrypt.
  const cred = process.env.CREDENTIAL_ENCRYPTION_KEY?.trim();
  const jwt = process.env.JWT_SECRET?.trim();
  if (!cred || !jwt || cred === jwt) return null;
  return scryptSync(jwt, 'dcf-security-v1', 32);
}

export function encryptSecret(plain: string): string {
  const key = deriveKey();
  const iv = randomBytes(12);
  const cipher = createCipheriv('aes-256-gcm', key, iv);
  const encrypted = Buffer.concat([cipher.update(plain, 'utf8'), cipher.final()]);
  const tag = cipher.getAuthTag();
  return Buffer.concat([iv, tag, encrypted]).toString('base64');
}

export function decryptSecret(payload: string): string {
  const buf = Buffer.from(payload, 'base64');
  const iv = buf.subarray(0, 12);
  const tag = buf.subarray(12, 28);
  const encrypted = buf.subarray(28);
  try {
    return decryptWith(deriveKey(), iv, tag, encrypted);
  } catch (err) {
    const legacy = deriveLegacyKey();
    if (legacy) {
      return decryptWith(legacy, iv, tag, encrypted);
    }
    throw err;
  }
}

function decryptWith(key: Buffer, iv: Buffer, tag: Buffer, encrypted: Buffer): string {
  const decipher = createDecipheriv('aes-256-gcm', key, iv);
  decipher.setAuthTag(tag);
  return Buffer.concat([decipher.update(encrypted), decipher.final()]).toString('utf8');
}

/** Returns null when ciphertext was encrypted with a different JWT_SECRET. */
export function tryDecryptSecret(payload: string): string | null {
  try {
    return decryptSecret(payload);
  } catch {
    return null;
  }
}

export function hashToken(token: string): string {
  return scryptSync(token, 'dcf-auth-challenge', 32).toString('hex');
}

export function randomToken(bytes = 32): string {
  return randomBytes(bytes).toString('base64url');
}

export function generateRecoveryCode(): string {
  const part = () => randomBytes(2).toString('hex').toUpperCase();
  return `${part()}-${part()}-${part()}-${part()}`;
}
