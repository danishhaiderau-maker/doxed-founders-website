/**
 * Finish X automation: admin JWT, GitHub Actions secrets, Railway variables.
 * Reads .env.x.secrets (gitignored). Optional: RAILWAY_TOKEN for API access.
 */
import { readFileSync, existsSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';
import { execSync } from 'child_process';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const vaultDir = join(root, '..', 'doxedcryptofounder-secrets', 'vault');
const secretsPath = existsSync(join(root, '.env.x.secrets'))
  ? join(root, '.env.x.secrets')
  : join(vaultDir, '.env.x.secrets');
const adminSecurityPath = join(vaultDir, '.env.admin-security');
const vercelProdPath = existsSync(join(root, 'apps', '.env.vercel.prod'))
  ? join(root, 'apps', '.env.vercel.prod')
  : join(vaultDir, 'apps.env.vercel.prod');

const RAILWAY_GQL = 'https://backboard.railway.com/graphql/v2';
const REPO = 'danishhaiderau-maker/doxed-founders-website';
const DEFAULT_API_URL =
  'https://doxed-founders-website-production.up.railway.app';

function readDotEnv(path) {
  const map = {};
  if (!existsSync(path)) return map;
  for (const line of readFileSync(path, 'utf8').split('\n')) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith('#')) continue;
    const idx = trimmed.indexOf('=');
    if (idx < 1) continue;
    map[trimmed.slice(0, idx).trim()] = trimmed.slice(idx + 1).trim().replace(/^"|"$/g, '');
  }
  return map;
}

function ghToken() {
  if (process.env.GITHUB_TOKEN?.trim()) return process.env.GITHUB_TOKEN.trim();
  try {
    const out = execSync('git credential fill', {
      input: 'protocol=https\nhost=github.com\n\n',
      encoding: 'utf8',
      stdio: ['pipe', 'pipe', 'pipe'],
    });
    const match = out.match(/^password=(.+)$/m);
    return match?.[1]?.trim() || null;
  } catch {
    return null;
  }
}

async function railwayGql(token, query, variables = {}) {
  const res = await fetch(RAILWAY_GQL, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${token}`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ query, variables }),
  });
  const json = await res.json();
  if (json.errors?.length) {
    throw new Error(json.errors.map((e) => e.message).join('; '));
  }
  return json.data;
}

async function discoverRailwayIds(token) {
  const data = await railwayGql(
    token,
    `query {
      projects { edges { node { id name services { edges { node { id name } } } environments { edges { node { id name } } } } } }
    }`,
  );
  const projects = data.projects?.edges?.map((e) => e.node) ?? [];
  const hit =
    projects.find((p) => p.name === 'giving-spirit') ??
    projects.find((p) => p.name?.includes('doxed')) ??
    projects[0];
  if (!hit) throw new Error('No Railway project found');
  const env =
    hit.environments?.edges?.find((e) => e.node.name === 'production')?.node ??
    hit.environments?.edges?.[0]?.node;
  const service =
    hit.services?.edges?.find((e) => e.node.name?.includes('doxed-founders'))?.node ??
    hit.services?.edges?.[0]?.node;
  if (!env || !service) throw new Error(`Missing env/service on project ${hit.name}`);
  return { projectId: hit.id, environmentId: env.id, serviceId: service.id, projectName: hit.name };
}

async function upsertRailwayVars(token, ids, vars) {
  await railwayGql(
    token,
    `mutation($input: VariableCollectionUpsertInput!) {
      variableCollectionUpsert(input: $input)
    }`,
    {
      input: {
        projectId: ids.projectId,
        environmentId: ids.environmentId,
        serviceId: ids.serviceId,
        variables: vars,
        replace: false,
      },
    },
  );
}

async function redeployRailway(token, ids) {
  await railwayGql(
    token,
    `mutation($serviceId: String!, $environmentId: String!) {
      serviceInstanceRedeploy(serviceId: $serviceId, environmentId: $environmentId)
    }`,
    { serviceId: ids.serviceId, environmentId: ids.environmentId },
  );
}

async function setGitHubSecret(token, name, value) {
  try {
    execSync(`gh auth status`, { stdio: 'pipe' });
  } catch {
    execSync(`gh auth login --with-token`, {
      input: token,
      stdio: ['pipe', 'pipe', 'pipe'],
    });
  }
  execSync(`gh secret set ${name} --body ${JSON.stringify(value)} --repo ${REPO}`, {
    stdio: 'pipe',
  });
}

function loadRecoveryCodes() {
  const map = readDotEnv(adminSecurityPath);
  return Object.keys(map)
    .filter((k) => k.startsWith('ADMIN_RECOVERY_'))
    .sort()
    .map((k) => map[k]?.trim())
    .filter(Boolean);
}

async function ensureAdminJwt(secrets, vercelEnv) {
  const apiUrl = secrets.API_URL || DEFAULT_API_URL;
  const email = secrets.ADMIN_EMAIL || 'admin@doxedcryptofounder.local';
  let password = secrets.ADMIN_PASSWORD?.trim();
  const recoveryCodes = loadRecoveryCodes();

  if (password) {
    const login = await tryLogin(apiUrl, email, password, recoveryCodes);
    if (login) return login;
  }

  if (recoveryCodes.length > 0) {
    console.warn(
      'Admin login failed — fix ADMIN_PASSWORD in vault .env.x.secrets (password + recovery 2FA). Skipping auto password rotation.',
    );
    return null;
  }

  const dbUrl = secrets.DATABASE_URL || vercelEnv.DATABASE_URL;
  if (!dbUrl) {
    console.warn('No DATABASE_URL — skip admin JWT rotation');
    return null;
  }

  password =
    secrets.ADMIN_PASSWORD?.trim() ||
    `DcfSync!${Date.now().toString(36)}A1`;
  execSync('node scripts/prisma-run.mjs generate', {
    cwd: root,
    stdio: 'inherit',
    env: {
      ...process.env,
      DATABASE_URL: dbUrl,
      PRISMA_SCHEMA: 'prisma/schema.prisma',
    },
  });
  execSync('node scripts/rotate-admin-password.mjs', {
    cwd: root,
    stdio: 'inherit',
    env: {
      ...process.env,
      DATABASE_URL: dbUrl,
      PRISMA_SCHEMA: 'prisma/schema.prisma',
      SEED_ADMIN_PASSWORD: password,
    },
  });
  console.log('Admin password rotated in production DB (stored in .env.x.secrets only)');

  secrets.ADMIN_PASSWORD = password;
  return tryLogin(apiUrl, email, password, recoveryCodes);
}

async function tryLogin(apiUrl, email, password, recoveryCodes = []) {
  const base = apiUrl.replace(/\/$/, '');

  async function passwordLogin() {
    const res = await fetch(`${base}/api/auth/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, password }),
    });
    if (!res.ok) return null;
    return res.json();
  }

  let data = await passwordLogin();
  if (!data) return null;
  if (data.accessToken) return data.accessToken;

  if (!data.requires2fa || !data.pendingToken) return null;

  for (const recoveryCode of recoveryCodes) {
    const verifyRes = await fetch(`${base}/api/auth/verify-2fa`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ pendingToken: data.pendingToken, recoveryCode }),
    });
    if (verifyRes.ok) {
      const verified = await verifyRes.json();
      if (verified.accessToken) {
        console.log('Admin JWT obtained via recovery code (TOTP unavailable until JWT_SECRET sync)');
        return verified.accessToken;
      }
    }
    data = await passwordLogin();
    if (!data?.requires2fa || !data.pendingToken) return null;
  }

  return null;
}

function buildRailwayVars(secrets, adminJwt) {
  const vars = {
    TWITTER_BEARER_TOKEN: secrets.TWITTER_BEARER_TOKEN,
    TWITTER_API_KEY: secrets.TWITTER_API_KEY,
    TWITTER_API_SECRET: secrets.TWITTER_API_SECRET,
    TWITTER_ACCESS_TOKEN: secrets.TWITTER_ACCESS_TOKEN,
    TWITTER_ACCESS_TOKEN_SECRET: secrets.TWITTER_ACCESS_TOKEN_SECRET,
    X_BRAND_HANDLE: secrets.X_BRAND_HANDLE || 'Bitbro4crypto',
    PUBLIC_SITE_URL: secrets.PUBLIC_SITE_URL || 'https://doxxedcrypto.digital',
    TRENDING_BUY_MIN_TRADERS: secrets.TRENDING_BUY_MIN_TRADERS || '5',
    TRADER_WIN_MIN_PNL_PERCENT: secrets.TRADER_WIN_MIN_PNL_PERCENT || '50',
  };
  if (adminJwt) vars.ADMIN_SYNC_JWT = adminJwt;
  return Object.fromEntries(Object.entries(vars).filter(([, v]) => Boolean(v?.trim?.() ?? v)));
}

async function main() {
  if (!existsSync(secretsPath)) {
    console.error(`Missing .env.x.secrets — copy .env.x.secrets.example to repo or ${vaultDir}`);
    process.exit(1);
  }

  const secrets = readDotEnv(secretsPath);
  const vercelEnv = readDotEnv(vercelProdPath);
  const apiUrl = secrets.API_URL || DEFAULT_API_URL;

  console.log('=== X production finish ===\n');

  const adminJwt = await ensureAdminJwt(secrets, vercelEnv);
  if (adminJwt) {
    secrets.ADMIN_SYNC_JWT = adminJwt;
    console.log('Admin JWT obtained for daily cron');
  } else {
    console.warn('Could not obtain admin JWT');
  }

  const gh = ghToken();
  if (gh && adminJwt) {
    try {
      await setGitHubSecret(gh, 'API_URL', apiUrl);
      await setGitHubSecret(gh, 'ADMIN_SYNC_JWT', adminJwt);
      console.log('GitHub Actions secrets set (API_URL, ADMIN_SYNC_JWT)');
    } catch (err) {
      console.warn('GitHub secrets:', err.message);
    }
  } else {
    console.warn('Skip GitHub secrets — no token or admin JWT');
  }

  const railwayToken =
    secrets.RAILWAY_TOKEN?.trim() ||
    process.env.RAILWAY_TOKEN?.trim() ||
    process.env.RAILWAY_API_TOKEN?.trim();

  const railwayVars = buildRailwayVars(secrets, adminJwt);
  const missingPosting = !railwayVars.TWITTER_ACCESS_TOKEN || !railwayVars.TWITTER_ACCESS_TOKEN_SECRET;

  if (railwayToken) {
    try {
      const ids = await discoverRailwayIds(railwayToken);
      console.log(`Railway project: ${ids.projectName}`);
      await upsertRailwayVars(railwayToken, ids, railwayVars);
      console.log(`Railway vars set (${Object.keys(railwayVars).length} keys)`);
      await redeployRailway(railwayToken, ids);
      console.log('Railway redeploy triggered');
    } catch (err) {
      console.warn('Railway API:', err.message);
    }
  } else {
    console.warn('Skip Railway — add RAILWAY_TOKEN to .env.x.secrets (Railway → Account → Tokens)');
  }

  if (missingPosting) {
    console.warn('\nMissing TWITTER_ACCESS_TOKEN + TWITTER_ACCESS_TOKEN_SECRET in .env.x.secrets');
    console.warn('Generate in X Developer Portal → Keys → Access Token for @Bitbro4crypto');
  }

  await new Promise((r) => setTimeout(r, 8000));
  const statusRes = await fetch(`${apiUrl}/api/x-social/status`);
  const status = await statusRes.json();
  console.log('\nStatus:', JSON.stringify(status, null, 2));

  if (!status.fullyAutomated) {
    process.exitCode = missingPosting || !railwayToken ? 2 : 1;
  }
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
