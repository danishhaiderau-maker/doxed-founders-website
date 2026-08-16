import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const source = readFileSync(join(dirname(fileURLToPath(import.meta.url)), 'sync-bot-admin-token-railway.mjs'), 'utf8');
assert.match(source, /readDotEnv\(join\(vaultDir, 'home-bot\.env'\)\)/);
assert.match(source, /syncRailwayServiceVars\(railwayToken, \{ BOT_ADMIN_TOKEN: adminToken \}\)/);
assert.doesNotMatch(source, /console\.log\([^\n]*adminToken/);
assert.doesNotMatch(source, /process\.argv/);
assert.match(source, /'BOT_ADMIN_TOKEN', '--stdin'/);
assert.match(source, /input: adminToken/);
assert.doesNotMatch(source, /BOT_ADMIN_TOKEN=\$\{|BOT_ADMIN_TOKEN=' \+ adminToken/);
console.log(JSON.stringify({ ok: true, checks: 7 }));
