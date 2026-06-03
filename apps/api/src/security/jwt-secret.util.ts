const DEV_JWT_SECRET = 'dev-secret-change-in-production';
const MIN_PRODUCTION_SECRET_LENGTH = 32;

export function resolveJwtSecret(): string {
  const secret = process.env.JWT_SECRET?.trim();
  if (process.env.NODE_ENV === 'production') {
    if (!secret || secret.length < MIN_PRODUCTION_SECRET_LENGTH || secret === DEV_JWT_SECRET) {
      throw new Error('Set a strong JWT_SECRET (32+ chars) in production.');
    }
    return secret;
  }
  return secret || DEV_JWT_SECRET;
}

export function assertProductionJwtSecret(): void {
  resolveJwtSecret();
}
