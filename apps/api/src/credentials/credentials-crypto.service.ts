import { Injectable } from '@nestjs/common';
import { decryptSecret, encryptSecret } from '../security/security-crypto.util';

@Injectable()
export class CredentialsCryptoService {
  encrypt(plain: string): string {
    return encryptSecret(plain);
  }

  /** Decrypt stored token; supports legacy plaintext rows until re-saved. */
  decrypt(stored: string | null | undefined): string | null {
    if (!stored?.trim()) return null;
    try {
      return decryptSecret(stored);
    } catch {
      return stored;
    }
  }
}
