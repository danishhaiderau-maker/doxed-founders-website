import { createCipheriv, createDecipheriv, randomBytes, scryptSync } from 'crypto';

function deriveKey(): Buffer {
  const secret = process.env.JWT_SECRET ?? 'dev-secret-change-in-production';
  return scryptSync(secret, 'dcf-security-v1', 32);
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
  const key = deriveKey();
  const buf = Buffer.from(payload, 'base64');
  const iv = buf.subarray(0, 12);
  const tag = buf.subarray(12, 28);
  const encrypted = buf.subarray(28);
  const decipher = createDecipheriv('aes-256-gcm', key, iv);
  decipher.setAuthTag(tag);
  return Buffer.concat([decipher.update(encrypted), decipher.final()]).toString('utf8');
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
