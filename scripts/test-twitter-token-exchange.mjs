import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const env = Object.fromEntries(
  fs
    .readFileSync(path.join(root, '.env.x.secrets'), 'utf8')
    .split('\n')
    .filter((l) => l.includes('=') && !l.trim().startsWith('#'))
    .map((l) => {
      const i = l.indexOf('=');
      return [l.slice(0, i).trim(), l.slice(i + 1).trim().replace(/^"|"$/g, '')];
    }),
);

const clientId = env.TWITTER_CLIENT_ID;
const clientSecret = env.TWITTER_CLIENT_SECRET;
const redirectUri = 'https://doxxedcrypto.digital/api/auth/callback/twitter';

console.log('client_id suffix:', clientId.slice(-6));
console.log('secret length:', clientSecret.length);

for (const host of ['https://api.twitter.com', 'https://api.x.com']) {
  const basic = Buffer.from(`${clientId}:${clientSecret}`, 'utf8').toString('base64');
  const body = new URLSearchParams({
    grant_type: 'authorization_code',
    code: 'invalid-test-code',
    redirect_uri: redirectUri,
    code_verifier: 'test',
  });

  const res = await fetch(`${host}/2/oauth2/token`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/x-www-form-urlencoded',
      Authorization: `Basic ${basic}`,
    },
    body,
  });
  const text = await res.text();
  console.log(`\n${host} -> ${res.status}`);
  console.log(text.slice(0, 250));
}
