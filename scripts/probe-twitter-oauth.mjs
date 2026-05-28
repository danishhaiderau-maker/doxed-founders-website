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
const callback = 'https://doxxedcrypto.digital/api/auth/callback/twitter';
const params = new URLSearchParams({
  client_id: clientId,
  redirect_uri: callback,
  response_type: 'code',
  scope: 'users.read tweet.read offline.access',
  state: 'test',
  code_challenge: 'challenge',
  code_challenge_method: 'plain',
});

const url = `https://twitter.com/i/oauth2/authorize?${params}`;
const res = await fetch(url, { redirect: 'manual' });
const body = res.status === 200 ? await res.text() : '';
const title = body.match(/<title>([^<]+)/i)?.[1] ?? '';
console.log(JSON.stringify({
  status: res.status,
  location: res.headers.get('location')?.slice(0, 80) ?? null,
  title: title.slice(0, 120),
  looksLikePhoneGate: /phone|verification/i.test(body),
  looksLikeInvalidClient: /invalid|something went wrong|error/i.test(body + title),
}, null, 2));
