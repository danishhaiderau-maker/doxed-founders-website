export interface SecurityScoreInput {
  walletConnected: boolean;
  passkeyEnabled: boolean;
  totpEnabled: boolean;
  recoveryCodesActive: boolean;
}

export interface SecurityScoreItem {
  key: 'wallet' | 'passkey' | 'totp' | 'recovery';
  label: string;
  enabled: boolean;
  points: number;
  maxPoints: number;
}

export interface SecurityScoreResult {
  score: number;
  maxScore: number;
  items: SecurityScoreItem[];
}

const FACTORS: { key: SecurityScoreItem['key']; label: string; field: keyof SecurityScoreInput }[] = [
  { key: 'wallet', label: 'Wallet connected', field: 'walletConnected' },
  { key: 'passkey', label: 'Passkey enabled', field: 'passkeyEnabled' },
  { key: 'totp', label: '2FA (Authenticator)', field: 'totpEnabled' },
  { key: 'recovery', label: 'Backup codes generated', field: 'recoveryCodesActive' },
];

export function computeSecurityScore(input: SecurityScoreInput): SecurityScoreResult {
  const pointsEach = 25;
  const items: SecurityScoreItem[] = FACTORS.map(({ key, label, field }) => ({
    key,
    label,
    enabled: input[field],
    points: input[field] ? pointsEach : 0,
    maxPoints: pointsEach,
  }));
  const score = items.reduce((sum, item) => sum + item.points, 0);
  return { score, maxScore: pointsEach * FACTORS.length, items };
}
