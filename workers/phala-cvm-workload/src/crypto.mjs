/** Must match apps/api/src/security/security-crypto.util.ts (JWT_SECRET + scrypt salt). */
import { createDecipheriv, scryptSync } from 'crypto';

function deriveKey() {
  const secret = process.env.JWT_SECRET?.trim() || 'dev-secret-change-in-production';
  return scryptSync(secret, 'dcf-security-v1', 32);
}

export function decryptSecret(payload) {
  const key = deriveKey();
  const buf = Buffer.from(payload, 'base64');
  const iv = buf.subarray(0, 12);
  const tag = buf.subarray(12, 28);
  const encrypted = buf.subarray(28);
  const decipher = createDecipheriv('aes-256-gcm', key, iv);
  decipher.setAuthTag(tag);
  return Buffer.concat([decipher.update(encrypted), decipher.final()]).toString('utf8');
}
