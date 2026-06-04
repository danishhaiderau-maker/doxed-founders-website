import { createServer } from 'http';
import { randomBytes } from 'crypto';
import { decryptSecret } from './crypto.mjs';

const PORT = Number(process.env.PORT || 8787);
const backups = new Map();

function authOk(req) {
  const header = req.headers.authorization || '';
  const token = header.startsWith('Bearer ') ? header.slice(7).trim() : '';
  const expected =
    process.env.CVM_WORKLOAD_AUTH_TOKEN?.trim() ||
    process.env.PHALA_CVM_API_KEY?.trim() ||
    process.env.PHALA_API_KEY?.trim() ||
    '';
  if (!expected) return { ok: false, error: 'CVM_WORKLOAD_AUTH_TOKEN not configured' };
  if (!token || token !== expected) return { ok: false, error: 'Unauthorized' };
  return { ok: true };
}

function readJson(req) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    req.on('data', (c) => chunks.push(c));
    req.on('end', () => {
      try {
        const raw = Buffer.concat(chunks).toString('utf8');
        resolve(raw ? JSON.parse(raw) : {});
      } catch (e) {
        reject(e);
      }
    });
    req.on('error', reject);
  });
}

function json(res, status, body) {
  res.writeHead(status, { 'Content-Type': 'application/json' });
  res.end(JSON.stringify(body));
}

const server = createServer(async (req, res) => {
  const url = new URL(req.url || '/', `http://127.0.0.1`);

  if (req.method === 'GET' && url.pathname === '/health') {
    const jwt = Boolean(process.env.JWT_SECRET?.trim());
    return json(res, jwt ? 200 : 503, {
      ok: jwt,
      service: 'dcf-phala-cvm-workload',
      jwtSecretSet: jwt,
    });
  }

  const auth = authOk(req);
  if (!auth.ok && url.pathname !== '/health') {
    return json(res, 401, { ok: false, error: auth.error });
  }

  if (req.method === 'POST' && url.pathname === '/vault/backup') {
    try {
      const body = await readJson(req);
      const blobHash = typeof body.blobHash === 'string' ? body.blobHash : '';
      if (!blobHash) return json(res, 400, { ok: false, error: 'blobHash required' });

      const backupId = randomBytes(16).toString('hex');
      const signingAddress = `0x${randomBytes(20).toString('hex')}`;
      backups.set(backupId, {
        backupId,
        blobHash,
        relayUpdatedAt: body.relayUpdatedAt ?? null,
        memoryMode: body.memoryMode ?? null,
        deviceLabel: body.deviceLabel ?? null,
        taskCount: body.taskCount ?? 0,
        workloadId: body.workloadId ?? process.env.PHALA_CVM_WORKLOAD_ID ?? null,
        createdAt: new Date().toISOString(),
        signing_address: signingAddress,
      });

      return json(res, 200, { ok: true, backupId, signing_address: signingAddress });
    } catch (e) {
      return json(res, 400, { ok: false, error: e instanceof Error ? e.message : 'Invalid JSON' });
    }
  }

  if (req.method === 'POST' && url.pathname === '/secrets/unwrap') {
    try {
      const body = await readJson(req);
      const encryptedToken =
        typeof body.encryptedToken === 'string' ? body.encryptedToken : '';
      if (!encryptedToken) {
        return json(res, 400, { ok: false, error: 'encryptedToken required' });
      }
      if (!process.env.JWT_SECRET?.trim()) {
        return json(res, 503, { ok: false, error: 'JWT_SECRET not set on CVM workload' });
      }

      let plaintext;
      try {
        plaintext = decryptSecret(encryptedToken);
      } catch {
        return json(res, 400, { ok: false, error: 'Decrypt failed — JWT_SECRET must match Railway API' });
      }

      return json(res, 200, {
        ok: true,
        plaintext,
        unwrapPath: 'cvm_sealed',
        purpose: body.purpose ?? null,
        provider: body.provider ?? null,
      });
    } catch (e) {
      return json(res, 400, { ok: false, error: e instanceof Error ? e.message : 'Invalid JSON' });
    }
  }

  return json(res, 404, { ok: false, error: 'Not found' });
});

server.listen(PORT, () => {
  console.log(`dcf-phala-cvm-workload listening on :${PORT}`);
});
