export type IntegrationConnectField = {
  key: string;
  label: string;
  placeholder: string;
  required: boolean;
  secret?: boolean;
};

export type IntegrationProviderConfig = {
  key: string;
  label: string;
  reputationBoost: number;
  connectType: 'repo' | 'oauth' | 'token' | 'toggle';
  billTip: string;
  fields: IntegrationConnectField[];
};

export const INTEGRATION_PROVIDERS: IntegrationProviderConfig[] = [
  {
    key: 'github',
    label: 'GitHub',
    reputationBoost: 5,
    connectType: 'repo',
    billTip: 'Free — sync commits into build updates automatically.',
    fields: [{ key: 'repoFullName', label: 'Repository', placeholder: 'owner/repo', required: true }],
  },
  {
    key: 'cursor',
    label: 'Founder Copilot (Cursor)',
    reputationBoost: 8,
    connectType: 'toggle',
    billTip: 'Uses Founder Credits instead of separate AI subscriptions for update drafts.',
    fields: [],
  },
  {
    key: 'x',
    label: 'X',
    reputationBoost: 3,
    connectType: 'oauth',
    billTip: 'Sign in with X — one-click publish, no third-party scheduler needed.',
    fields: [],
  },
  {
    key: 'vercel',
    label: 'Vercel',
    reputationBoost: 4,
    connectType: 'token',
    billTip: 'Deploy webhooks → auto-suggested updates. Keep one dashboard instead of extra monitoring tools.',
    fields: [
      { key: 'token', label: 'API token', placeholder: 'vercel_…', required: true, secret: true },
      { key: 'projectName', label: 'Project name', placeholder: 'my-startup', required: false },
    ],
  },
  {
    key: 'railway',
    label: 'Railway',
    reputationBoost: 4,
    connectType: 'token',
    billTip: 'Connect API + deploy hook — ship logs flow into Founder OS.',
    fields: [
      { key: 'token', label: 'Account token', placeholder: 'Railway token', required: true, secret: true },
      { key: 'projectName', label: 'Project name', placeholder: 'api-production', required: false },
    ],
  },
  {
    key: 'neon',
    label: 'Neon',
    reputationBoost: 3,
    connectType: 'token',
    billTip: 'Verify Postgres project — stack visible to supporters in one place.',
    fields: [
      { key: 'token', label: 'API key', placeholder: 'neon_…', required: true, secret: true },
      { key: 'projectName', label: 'Project name', placeholder: 'main-db', required: false },
    ],
  },
  {
    key: 'digitalocean',
    label: 'DigitalOcean',
    reputationBoost: 3,
    connectType: 'token',
    billTip: 'Unified infra visibility — less context-switching between dashboards.',
    fields: [
      { key: 'token', label: 'Personal access token', placeholder: 'dop_v1_…', required: true, secret: true },
    ],
  },
  {
    key: 'supabase',
    label: 'Supabase',
    reputationBoost: 3,
    connectType: 'token',
    billTip: 'Link backend project — founders show full stack transparency.',
    fields: [
      { key: 'token', label: 'Access token', placeholder: 'sbp_…', required: true, secret: true },
      { key: 'projectName', label: 'Project ref', placeholder: 'abcdefghij', required: false },
    ],
  },
];

/** @deprecated use INTEGRATION_PROVIDERS */
export const CONNECTED_APP_PROVIDERS = INTEGRATION_PROVIDERS.map((p) => ({
  key: p.key,
  label: p.label,
  reputationBoost: p.reputationBoost,
}));
