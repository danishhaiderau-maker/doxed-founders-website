import { createCipheriv, createDecipheriv, createHash, randomBytes } from 'node:crypto';
import { Injectable } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';

@Injectable()
export class CredentialCryptoService {
  private readonly key: Buffer;
  // Legacy key derived from JWT_SECRET — kept ONLY for decrypting credentials that were
  // encrypted before CREDENTIAL_ENCRYPTION_KEY was introduced. New encryptions always use
  // `key` (the dedicated credential key) so a JWT_SECRET leak alone cannot decrypt stored
  // exchange API keys.
  private readonly legacyKey: Buffer | null;

  constructor(config: ConfigService) {
    const credSecret = config.get<string>('CREDENTIAL_ENCRYPTION_KEY')?.trim();
    const jwtSecret = config.get<string>('JWT_SECRET')?.trim();
    if (!credSecret && !jwtSecret) {
      // Dev-only fallback. In production both CREDENTIAL_ENCRYPTION_KEY and JWT_SECRET
      // must be set — exchange credentials are real-money-sensitive.
      this.key = createHash('sha256').update('dev-credential-key-change-me').digest();
      this.legacyKey = null;
      return;
    }
    this.key = createHash('sha256').update(credSecret ?? jwtSecret ?? '').digest();
    // If a dedicated credential key is set AND it differs from the JWT secret, keep the
    // JWT-derived key as a legacy decrypt fallback for already-stored credentials.
    this.legacyKey =
      credSecret && jwtSecret && credSecret !== jwtSecret
        ? createHash('sha256').update(jwtSecret).digest()
        : null;
  }

  encrypt(plain: string): string {
    const iv = randomBytes(12);
    const cipher = createCipheriv('aes-256-gcm', this.key, iv);
    const enc = Buffer.concat([cipher.update(plain, 'utf8'), cipher.final()]);
    const tag = cipher.getAuthTag();
    return `${iv.toString('base64')}:${tag.toString('base64')}:${enc.toString('base64')}`;
  }

  decrypt(payload: string): string {
    const [ivB64, tagB64, dataB64] = payload.split(':');
    if (!ivB64 || !tagB64 || !dataB64) throw new Error('Invalid credential payload');
    const iv = Buffer.from(ivB64, 'base64');
    const tag = Buffer.from(tagB64, 'base64');
    const data = Buffer.from(dataB64, 'base64');
    // Try the primary (dedicated) key first; fall back to the legacy JWT-derived key for
    // credentials encrypted before the split. GCM auth tag mismatch throws — catch and retry.
    try {
      return this.decryptWith(this.key, iv, tag, data);
    } catch (err) {
      if (this.legacyKey) {
        return this.decryptWith(this.legacyKey, iv, tag, data);
      }
      throw err;
    }
  }

  private decryptWith(key: Buffer, iv: Buffer, tag: Buffer, data: Buffer): string {
    const decipher = createDecipheriv('aes-256-gcm', key, iv);
    decipher.setAuthTag(tag);
    return Buffer.concat([decipher.update(data), decipher.final()]).toString('utf8');
  }
}
