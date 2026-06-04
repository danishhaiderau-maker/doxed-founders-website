/**
 * P0 — Public vs private data classes for Neon + Founder OS hybrid model.
 * Used by audit scripts, API privacy endpoints, and response redaction helpers.
 */

export type DataClass =
  | 'public_product'
  | 'founder_private'
  | 'sealed_credential'
  | 'founder_node_relay'
  | 'audit_telemetry'
  | 'platform_identity';

export type DataClassMeta = {
  id: DataClass;
  label: string;
  storage: string;
  description: string;
  neverInBrowser: string[];
};

export const DATA_CLASS_CATALOG: DataClassMeta[] = [
  {
    id: 'public_product',
    label: 'Public product',
    storage: 'Neon Postgres',
    description:
      'Discoverable projects, feed, paper trading aggregates, scout votes, trust center listings.',
    neverInBrowser: ['integration API keys', 'raw vault files', 'decrypted PATs'],
  },
  {
    id: 'founder_private',
    label: 'Founder-private',
    storage: 'Neon (scoped) + Founder Node disk',
    description:
      'Mission state, memory graph, copilot context, device sync relay, build suggestions before publish.',
    neverInBrowser: ['other founders’ memory graphs', 'unpublished draft bodies at scale'],
  },
  {
    id: 'sealed_credential',
    label: 'Sealed credentials',
    storage: 'Neon `integrationCredential` + `gitHubConnection` (encrypted)',
    description: 'AES-256-GCM at rest; unwrap only on server with purpose checks and audit log.',
    neverInBrowser: ['plaintext API keys', 'OAuth refresh tokens'],
  },
  {
    id: 'founder_node_relay',
    label: 'Founder Node relay',
    storage: 'Neon job queue + local ~/FounderVault',
    description: 'Pairing secrets, sync job payloads, Ollama prompts — node pulls; API never decrypts vault.',
    neverInBrowser: ['node pairing secret', 'full vault blob contents'],
  },
  {
    id: 'audit_telemetry',
    label: 'Audit & attestation',
    storage: 'Neon `privacyAttestationLog`',
    description: 'TEE verification receipts, SECRET_ACCESS unwrap events, vault scan summaries.',
    neverInBrowser: ['other users’ attestation logs'],
  },
  {
    id: 'platform_identity',
    label: 'Platform identity',
    storage: 'Neon `user` + auth providers',
    description: 'Login identifiers, WebAuthn credentials, admin roles — never on public product APIs.',
    neverInBrowser: ['password hashes', 'TOTP secrets', 'session tokens'],
  },
];

/** Field names that must never appear in @Public() JSON responses. */
export const FORBIDDEN_PUBLIC_FIELD_NAMES = [
  'token',
  'accessToken',
  'accessTokenEncrypted',
  'refreshToken',
  'webhookSecret',
  'secretHash',
  'apiKey',
  'apiSecret',
  'passphrase',
  'password',
  'passwordHash',
  'totpSecret',
  'privateKey',
  'clientSecret',
] as const;

export type PrismaModelClassification = {
  model: string;
  dataClass: DataClass;
  notes: string;
  sensitiveFields: string[];
};

/** Prisma models → data class (audit registry). */
export const PRISMA_MODEL_CLASSIFICATION: PrismaModelClassification[] = [
  { model: 'Project', dataClass: 'public_product', notes: 'Approved listings are public', sensitiveFields: [] },
  { model: 'FeedPost', dataClass: 'public_product', notes: 'Public feed', sensitiveFields: [] },
  { model: 'PaperTrade', dataClass: 'public_product', notes: 'Public activity cards', sensitiveFields: [] },
  { model: 'ScoutMarket', dataClass: 'public_product', notes: 'Community markets', sensitiveFields: [] },
  { model: 'Founder', dataClass: 'public_product', notes: 'Public founder profile fields only in APIs', sensitiveFields: [] },
  { model: 'IntegrationCredential', dataClass: 'sealed_credential', notes: 'Encrypted token column', sensitiveFields: ['token', 'webhookSecret'] },
  { model: 'GitHubConnection', dataClass: 'sealed_credential', notes: 'Encrypted PAT', sensitiveFields: ['accessTokenEncrypted'] },
  { model: 'FounderBuilderSettings', dataClass: 'founder_private', notes: 'memoryGraph owner-only', sensitiveFields: ['memoryGraph'] },
  { model: 'ProjectMemoryDeviceSync', dataClass: 'founder_node_relay', notes: 'Encrypted/local-first relay', sensitiveFields: ['payload'] },
  { model: 'FounderNode', dataClass: 'founder_node_relay', notes: 'Pairing metadata', sensitiveFields: ['secretHash'] },
  { model: 'FounderNodeSyncJob', dataClass: 'founder_node_relay', notes: 'Vault/agent job payloads', sensitiveFields: ['payload', 'result'] },
  { model: 'FounderNodeInferenceJob', dataClass: 'founder_node_relay', notes: 'Prompts for local Ollama', sensitiveFields: ['system', 'userPrompt', 'result'] },
  { model: 'FounderNodePairingCode', dataClass: 'founder_node_relay', notes: 'Short-lived pairing', sensitiveFields: ['code'] },
  { model: 'PrivacyAttestationLog', dataClass: 'audit_telemetry', notes: 'Per-user audit', sensitiveFields: ['reportSnapshot'] },
  { model: 'SuggestedBuildUpdate', dataClass: 'founder_private', notes: 'Pre-publish drafts', sensitiveFields: ['body', 'devSummary'] },
  { model: 'User', dataClass: 'platform_identity', notes: 'Auth only', sensitiveFields: ['email'] },
  { model: 'Account', dataClass: 'platform_identity', notes: 'OAuth linkage', sensitiveFields: [] },
];

export type ApiRouteClassification = {
  path: string;
  auth: 'public' | 'jwt' | 'founder_node' | 'webhook';
  allowedClasses: DataClass[];
  notes: string;
};

export const API_ROUTE_CLASSIFICATION: ApiRouteClassification[] = [
  { path: 'GET /feed/*', auth: 'public', allowedClasses: ['public_product'], notes: 'No credentials' },
  { path: 'GET /projects/*', auth: 'public', allowedClasses: ['public_product'], notes: 'Listing detail' },
  { path: 'GET /founder-den/discover/*', auth: 'public', allowedClasses: ['public_product'], notes: 'Universe map' },
  { path: 'GET /builder/settings', auth: 'jwt', allowedClasses: ['founder_private', 'sealed_credential'], notes: 'Status only — no raw keys' },
  { path: 'GET /copilot/memory-graph', auth: 'jwt', allowedClasses: ['founder_private'], notes: 'Owner memory graph' },
  { path: 'GET /attestation/dashboard', auth: 'jwt', allowedClasses: ['audit_telemetry', 'sealed_credential'], notes: 'Summaries only' },
  { path: 'GET /vault/cvm-capabilities', auth: 'public', allowedClasses: ['audit_telemetry'], notes: 'Platform CVM config flags only' },
  { path: 'GET /vault/cvm-status', auth: 'jwt', allowedClasses: ['founder_node_relay', 'audit_telemetry'], notes: 'Relay metadata + backup receipts' },
  { path: 'POST /vault/cvm-backup-request', auth: 'jwt', allowedClasses: ['founder_node_relay', 'audit_telemetry'], notes: 'Encrypted blob hash to CVM — never plaintext vault' },
  { path: 'POST /founder-node/v1/*', auth: 'founder_node', allowedClasses: ['founder_node_relay'], notes: 'Node guard' },
];

export function getDataClassMeta(id: DataClass): DataClassMeta {
  return DATA_CLASS_CATALOG.find((c) => c.id === id) ?? DATA_CLASS_CATALOG[0];
}

export function isForbiddenPublicFieldName(key: string): boolean {
  const lower = key.toLowerCase();
  return FORBIDDEN_PUBLIC_FIELD_NAMES.some(
    (f) => lower === f.toLowerCase() || lower.endsWith(f.toLowerCase()),
  );
}

/** Deep-redact forbidden field names (for defensive public serializers). */
export function redactForbiddenFields<T>(value: T, depth = 0): T {
  if (depth > 12 || value == null) return value;
  if (Array.isArray(value)) {
    return value.map((item) => redactForbiddenFields(item, depth + 1)) as T;
  }
  if (typeof value !== 'object') return value;
  const out: Record<string, unknown> = {};
  for (const [key, child] of Object.entries(value as Record<string, unknown>)) {
    if (isForbiddenPublicFieldName(key)) {
      out[key] = '[redacted]';
    } else if (child && typeof child === 'object') {
      out[key] = redactForbiddenFields(child, depth + 1);
    } else {
      out[key] = child;
    }
  }
  return out as T;
}

export type DataClassAuditFinding = {
  severity: 'ok' | 'info' | 'warn';
  code: string;
  message: string;
};

export function runStaticDataClassAudit(): {
  modelCount: number;
  routeCount: number;
  findings: DataClassAuditFinding[];
  compliant: boolean;
} {
  const findings: DataClassAuditFinding[] = [];

  const sealed = PRISMA_MODEL_CLASSIFICATION.filter((m) => m.dataClass === 'sealed_credential');
  if (sealed.length < 2) {
    findings.push({
      severity: 'warn',
      code: 'SEALED_REGISTRY_THIN',
      message: 'Sealed credential model registry should include IntegrationCredential and GitHubConnection.',
    });
  } else {
    findings.push({
      severity: 'ok',
      code: 'SEALED_MODELS',
      message: `${sealed.length} sealed credential models documented.`,
    });
  }

  const publicRoutes = API_ROUTE_CLASSIFICATION.filter((r) => r.auth === 'public');
  const badPublic = publicRoutes.filter((r) =>
    r.allowedClasses.some((c) =>
      ['sealed_credential', 'founder_private', 'founder_node_relay', 'platform_identity'].includes(c),
    ),
  );
  if (badPublic.length) {
    findings.push({
      severity: 'warn',
      code: 'PUBLIC_ROUTE_CLASS',
      message: `Public routes must not allow private classes: ${badPublic.map((r) => r.path).join(', ')}`,
    });
  } else {
    findings.push({
      severity: 'ok',
      code: 'PUBLIC_ROUTES',
      message: `${publicRoutes.length} public route patterns restricted to public_product.`,
    });
  }

  findings.push({
    severity: 'info',
    code: 'REDACT_HELPER',
    message: `Use redactForbiddenFields() on any new @Public() serializer; blocked keys: ${FORBIDDEN_PUBLIC_FIELD_NAMES.join(', ')}.`,
  });

  const compliant = !findings.some((f) => f.severity === 'warn');
  return {
    modelCount: PRISMA_MODEL_CLASSIFICATION.length,
    routeCount: API_ROUTE_CLASSIFICATION.length,
    findings,
    compliant,
  };
}
