import { randomBytes, scryptSync, timingSafeEqual } from 'crypto';

const KEY_LEN = 64;

export function hashProfileLockPassword(password: string): string {
  const salt = randomBytes(16).toString('hex');
  const hash = scryptSync(password.normalize('NFKC'), salt, KEY_LEN).toString('hex');
  return `${salt}:${hash}`;
}

export function verifyProfileLockPassword(password: string, stored: string): boolean {
  const parts = stored.split(':');
  if (parts.length !== 2) return false;
  const [salt, hash] = parts;
  try {
    const test = scryptSync(password.normalize('NFKC'), salt, KEY_LEN);
    const expected = Buffer.from(hash, 'hex');
    if (expected.length !== test.length) return false;
    return timingSafeEqual(expected, test);
  } catch {
    return false;
  }
}
