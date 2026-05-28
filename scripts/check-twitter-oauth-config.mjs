import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const secretsPath = path.join(root, '.env.x.secrets');

function readEnv(file) {
  const map = {};
  if (!fs.existsSync(file)) return map;
  for (const line of fs.readFileSync(file, 'utf8').split('\n')) {
    const t = line.trim();
    if (!t || t.startsWith('#')) continue;
    const i = t.indexOf('=');
    if (i < 1) continue;
    map[t.slice(0, i).trim()] = t.slice(i + 1).trim().replace(/^"|"$/g, '');
  }
  return map;
}

const env = readEnv(secretsPath);
const clientId = env.TWITTER_CLIENT_ID ?? '';
const clientSecret = env.TWITTER_CLIENT_SECRET ?? '';
const apiKey = env.TWITTER_API_KEY ?? '';
const apiSecret = env.TWITTER_API_SECRET ?? '';

console.log(JSON.stringify({
  hasClientId: Boolean(clientId),
  hasClientSecret: Boolean(clientSecret),
  clientIdLen: clientId.length,
  apiKeyLen: apiKey.length,
  clientIdLooksOAuth2: clientId.length > 20 && clientId.includes('-'),
  clientIdEqualsApiKey: clientId === apiKey,
  clientSecretEqualsApiSecret: clientSecret === apiSecret,
}, null, 2));
