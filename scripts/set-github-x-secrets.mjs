import { execSync } from 'child_process';
import sodium from 'tweetsodium';

const REPO = 'danishhaiderau-maker/doxed-founders-website';

function ghToken() {
  const out = execSync('git credential fill', {
    input: 'protocol=https\nhost=github.com\n\n',
    encoding: 'utf8',
  });
  const match = out.match(/^password=(.+)$/m);
  return match?.[1]?.trim();
}

async function setSecret(token, name, value) {
  const keyRes = await fetch(`https://api.github.com/repos/${REPO}/actions/secrets/public-key`, {
    headers: {
      Authorization: `Bearer ${token}`,
      Accept: 'application/vnd.github+json',
      'X-GitHub-Api-Version': '2022-11-28',
    },
  });
  if (!keyRes.ok) throw new Error(`public-key ${keyRes.status}`);
  const { key, key_id } = await keyRes.json();
  const encryptedBytes = sodium.seal(Buffer.from(value), Buffer.from(key, 'base64'));
  const encrypted = Buffer.from(encryptedBytes).toString('base64');
  const putRes = await fetch(`https://api.github.com/repos/${REPO}/actions/secrets/${name}`, {
    method: 'PUT',
    headers: {
      Authorization: `Bearer ${token}`,
      Accept: 'application/vnd.github+json',
      'X-GitHub-Api-Version': '2022-11-28',
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ encrypted_value: encrypted, key_id }),
  });
  if (!putRes.ok) throw new Error(`${name} ${putRes.status} ${await putRes.text()}`);
  console.log(`Set GitHub secret: ${name}`);
}

const token = ghToken();
if (!token) {
  console.error('No GitHub token');
  process.exit(1);
}

const apiUrl = 'https://doxed-founders-website-production.up.railway.app';
const loginRes = await fetch(`${apiUrl}/api/auth/login`, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    email: 'admin@doxedcryptofounder.local',
    password: process.env.ADMIN_PASSWORD || 'DcfSyncTest2026!X',
  }),
});
if (!loginRes.ok) {
  console.error('Admin login failed', await loginRes.text());
  process.exit(1);
}
const { accessToken } = await loginRes.json();
await setSecret(token, 'API_URL', apiUrl);
await setSecret(token, 'ADMIN_SYNC_JWT', accessToken);
console.log('Done');
