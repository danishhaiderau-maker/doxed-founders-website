/**
 * Quick check: OAuth 1.0a request token (uses API Key + Secret, not OAuth2 client secret).
 */
import crypto from 'node:crypto';
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

function pct(s) {
  return encodeURIComponent(s).replace(/[!'()*]/g, (c) => `%${c.charCodeAt(0).toString(16).toUpperCase()}`);
}

function oauth1Header(method, url, creds, extra = {}) {
  const params = {
    oauth_consumer_key: creds.key,
    oauth_nonce: crypto.randomBytes(16).toString('hex'),
    oauth_signature_method: 'HMAC-SHA1',
    oauth_timestamp: Math.floor(Date.now() / 1000).toString(),
    oauth_version: '1.0',
    ...extra,
  };
  const paramString = Object.keys(params)
    .sort()
    .map((k) => `${pct(k)}=${pct(params[k])}`)
    .join('&');
  const base = [method.toUpperCase(), pct(url), pct(paramString)].join('&');
  const signingKey = `${pct(creds.secret)}&`;
  const signature = crypto.createHmac('sha1', signingKey).update(base).digest('base64');
  const headerParams = { ...params, oauth_signature: signature };
  const auth = Object.keys(headerParams)
    .sort()
    .map((k) => `${pct(k)}="${pct(headerParams[k])}"`)
    .join(', ');
  return `OAuth ${auth}`;
}

const url = 'https://api.twitter.com/oauth/request_token';
const auth = oauth1Header('POST', url, {
  key: env.TWITTER_API_KEY,
  secret: env.TWITTER_API_SECRET,
});

const res = await fetch(url, {
  method: 'POST',
  headers: { Authorization: auth },
});
const text = await res.text();
console.log('OAuth 1.0a request_token:', res.status, text.slice(0, 200));
