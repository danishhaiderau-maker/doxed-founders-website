/**
 * Deploy workers/phala-cvm-workload to Phala Cloud and write INGRESS to vault/.env.phala.
 *
 * Requires in vault/.env.phala (or env):
 *   PHALA_CLOUD_API_KEY=phak_...
 *
 * Optional: PHALA_CVM_DEPLOY_NAME (default doxxedcrypto-founder-cvm)
 */
import { existsSync, readFileSync, writeFileSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';
import { spawnSync } from 'child_process';
import { loadPhalaCvmEnv, repoRoot, vaultDir } from './lib/read-vault-env.mjs';

const workloadDir = join(repoRoot, 'workers', 'phala-cvm-workload');
const deployName = process.env.PHALA_CVM_DEPLOY_NAME?.trim() || 'doxxedcrypto-founder-cvm';

function readPhalaCloudKey(merged) {
  const fromVault = merged.PHALA_CLOUD_API_KEY?.trim();
  if (fromVault) return fromVault;
  const fromEnv = process.env.PHALA_CLOUD_API_KEY?.trim();
  if (fromEnv) return fromEnv;
  const phala = merged.PHALA_API_KEY?.trim() ?? '';
  return phala.startsWith('phak_') ? phala : '';
}

function parseIngressFromDeployOutput(text) {
  const urls = text.match(/https:\/\/[a-z0-9-]+-\d+\.dstack[^\s)"']+/gi) ?? [];
  return urls.find((u) => u.includes('-8080')) ?? urls[0] ?? null;
}

function upsertEnvPhalaBaseUrl(baseUrl) {
  const target = join(vaultDir, '.env.phala');
  let body = existsSync(target) ? readFileSync(target, 'utf8') : '';
  const line = `PHALA_CVM_BASE_URL=${baseUrl.replace(/\/$/, '')}`;
  if (/^PHALA_CVM_BASE_URL=/m.test(body)) {
    body = body.replace(/^PHALA_CVM_BASE_URL=.*$/m, line);
  } else {
    body += `${body.endsWith('\n') || !body ? '' : '\n'}${line}\n`;
  }
  writeFileSync(target, body, 'utf8');
  console.log(`Updated ${target}`);
}

async function fetchIngressViaCli(apiKey) {
  const res = spawnSync('npx', ['phala', 'cvms', 'get', deployName, '--json'], {
    cwd: workloadDir,
    encoding: 'utf8',
    shell: process.platform === 'win32',
    env: { ...process.env, PHALA_CLOUD_API_KEY: apiKey },
  });
  if (res.status !== 0) return null;
  try {
    const json = JSON.parse(res.stdout);
    const endpoints = json?.endpoints ?? json?.ingress ?? json?.urls;
    if (Array.isArray(endpoints)) {
      const hit = endpoints.find((e) => String(e).includes('8080'));
      return hit ? String(hit).replace(/\/$/, '') : null;
    }
    const url = json?.url ?? json?.ingress_url ?? json?.public_url;
    return typeof url === 'string' ? url.replace(/\/$/, '') : null;
  } catch {
    return parseIngressFromDeployOutput(res.stdout);
  }
}

async function main() {
  const { merged, phalaPath } = loadPhalaCvmEnv();
  const apiKey = readPhalaCloudKey(merged);

  if (!merged.JWT_SECRET?.trim()) {
    console.error('JWT_SECRET missing — run: npm run bootstrap:phala-cvm-env');
    process.exit(1);
  }
  if (!merged.PHALA_CVM_API_KEY?.trim()) {
    console.error('PHALA_CVM_API_KEY missing in vault/.env.phala');
    process.exit(1);
  }
  if (!apiKey) {
    console.error('PHALA_CLOUD_API_KEY (or phak_ PHALA_API_KEY) required in vault/.env.phala');
    console.error('Create at https://cloud.phala.network/dashboard/tokens');
    process.exit(1);
  }

  const envFile = join(vaultDir, '.phala-cvm-deploy.env');
  const envLines = [
    `JWT_SECRET=${merged.JWT_SECRET.trim()}`,
    `CVM_WORKLOAD_AUTH_TOKEN=${merged.PHALA_CVM_API_KEY.trim()}`,
    `PORT=8787`,
  ];
  writeFileSync(envFile, `${envLines.join('\n')}\n`, 'utf8');
  console.log(`Wrote encrypted-secret template: ${envFile}`);
  console.log('(Phala CLI -e passes these into the CVM)\n');

  console.log(`Deploying ${deployName} from ${workloadDir} ...\n`);
  const deploy = spawnSync(
    'npx',
    [
      'phala',
      'deploy',
      '-n',
      deployName,
      '-c',
      'docker-compose.yml',
      '-t',
      'tdx.small',
      '--wait',
      '-e',
      envFile,
      '--json',
    ],
    {
      cwd: workloadDir,
      encoding: 'utf8',
      shell: process.platform === 'win32',
      env: { ...process.env, PHALA_CLOUD_API_KEY: apiKey },
      timeout: 600_000,
    },
  );

  const combined = [deploy.stdout, deploy.stderr].filter(Boolean).join('\n');
  if (deploy.status !== 0) {
    console.error(combined || `phala deploy exited ${deploy.status}`);
    process.exit(deploy.status || 1);
  }
  console.log(combined);

  let baseUrl = parseIngressFromDeployOutput(combined);
  if (!baseUrl) {
    console.log('\nFetching INGRESS via phala cvms get ...');
    baseUrl = await fetchIngressViaCli(apiKey);
  }

  if (!baseUrl) {
    console.error('\nDeploy succeeded but INGRESS URL not detected.');
    console.error('Open Phala Cloud → CVM → INGRESS and set PHALA_CVM_BASE_URL in .env.phala');
    process.exit(1);
  }

  upsertEnvPhalaBaseUrl(baseUrl);
  console.log(`\nCVM base URL: ${baseUrl}`);
  console.log('\nNext: npm run apply:railway:phala-cvm && npm run probe:phala-cvm\n');
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
