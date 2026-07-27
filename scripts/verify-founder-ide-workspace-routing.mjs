import { randomUUID } from 'node:crypto';
import fs from 'node:fs';
import net from 'node:net';
import os from 'node:os';
import path from 'node:path';

const workspaceArgument = process.argv[2];
if (!workspaceArgument) {
  throw new Error(
    'Usage: node scripts/verify-founder-ide-workspace-routing.mjs <workspace-path> [expected-file]',
  );
}

const workspacePath = path.resolve(workspaceArgument);
const expectedFile = process.argv[3]?.trim() || null;
const vaultRoot = path.join(os.homedir(), 'FounderVault');
const identity = readJson(path.join(vaultRoot, 'install.json'));
if (!identity?.installId || !identity?.ipcSecret) {
  throw new Error('Founder IDE install identity is missing.');
}

const presenceDir = path.join(vaultRoot, 'ide-sessions');
const matches = fs.readdirSync(presenceDir)
  .filter((name) => name.endsWith('.json'))
  .map((name) => readJson(path.join(presenceDir, name)))
  .filter((presence) =>
    presence?.workspacePath
    && normalizePath(presence.workspacePath) === normalizePath(workspacePath)
    && Date.now() - Date.parse(presence.heartbeatAt) <= 60_000,
  );

if (matches.length !== 1) {
  throw new Error(
    `Expected exactly one live Founder IDE window for ${workspacePath}; found ${matches.length}.`,
  );
}

const presence = matches[0];
const pipePath = process.platform === 'win32'
  ? `\\\\.\\pipe\\founder-ide-${identity.installId}-${presence.endpointId}`
  : `/tmp/founder-ide-${identity.installId}-${presence.endpointId}.sock`;
const requestId = `routing-proof-${randomUUID()}`;
const editRequestId = `routing-edit-boundary-${randomUUID()}`;
const commandRequestId = `routing-command-boundary-${randomUUID()}`;
const lifecycle = [];
let buffer = '';
let requestSent = false;
let workspaceResult = null;
let editBoundaryResult = null;

const proof = await new Promise((resolve, reject) => {
  const socket = net.createConnection(pipePath);
  const timer = setTimeout(() => {
    socket.destroy();
    reject(new Error(`Timed out waiting for workspace read. Lifecycle: ${JSON.stringify(lifecycle)}`));
  }, 15_000);

  const finish = (callback) => {
    clearTimeout(timer);
    socket.destroy();
    callback();
  };

  socket.setEncoding('utf8');
  socket.on('error', (error) => {
    finish(() => reject(new Error(`Pipe transport failed: ${error.message}`)));
  });
  socket.on('connect', () => {
    lifecycle.push({ event: 'transportConnected' });
    socket.write(`${JSON.stringify({
      type: 'hello',
      nonce: randomUUID(),
      ts: new Date().toISOString(),
      protocolVersion: 1,
      installId: identity.installId,
      ipcSecret: identity.ipcSecret,
      capabilities: [
        'workspace',
        'workspaceReadRequest',
        'workspaceReadResult',
        'proposedEdit',
        'editReviewResult',
        'commandRequest',
        'commandReviewResult',
        'heartbeat',
      ],
    })}\n`);
  });
  socket.on('data', (chunk) => {
    buffer += chunk;
    let newline = buffer.indexOf('\n');
    while (newline >= 0) {
      const raw = buffer.slice(0, newline);
      buffer = buffer.slice(newline + 1);
      newline = buffer.indexOf('\n');
      if (!raw.trim()) continue;
      let message;
      try {
        message = JSON.parse(raw);
      } catch {
        continue;
      }
      lifecycle.push({
        event: 'message',
        type: typeof message.type === 'string' ? message.type : 'unknown',
        ...(message.type === 'authState' ? { state: message.state } : {}),
      });
      if (message.type === 'authState' && message.state !== 'connected') {
        finish(() => reject(new Error(
          `Pipe authentication failed (${message.reason ?? message.state}).`,
        )));
        return;
      }
      if (message.type === 'authState' && message.state === 'connected' && !requestSent) {
        requestSent = true;
        socket.write(`${JSON.stringify({
          type: 'workspaceReadRequest',
          requestId,
          path: '.',
          maxEntries: 20,
          nonce: randomUUID(),
          ts: new Date().toISOString(),
        })}\n`);
        continue;
      }
      if (message.type === 'workspaceReadResult' && message.requestId === requestId) {
        workspaceResult = message;
        socket.write(`${JSON.stringify({
          type: 'proposedEdit',
          requestId: editRequestId,
          path: '..\\founder-routing-outside.txt',
          diff: 'Boundary proof only; this must never be applied.',
          creates: true,
          edit: {
            kind: 'create',
            content: 'This file must never be created.',
          },
          nonce: randomUUID(),
          ts: new Date().toISOString(),
        })}\n`);
        continue;
      }
      if (message.type === 'editReviewResult' && message.requestId === editRequestId) {
        editBoundaryResult = message;
        socket.write(`${JSON.stringify({
          type: 'commandRequest',
          requestId: commandRequestId,
          command: 'echo this-command-must-not-run',
          cwd: '..',
          risk: 'readonly',
          timeoutMs: 5_000,
          nonce: randomUUID(),
          ts: new Date().toISOString(),
        })}\n`);
        continue;
      }
      if (
        message.type === 'commandReviewResult'
        && message.requestId === commandRequestId
      ) {
        finish(() => resolve({
          workspaceResult,
          editBoundaryResult,
          commandBoundaryResult: message,
        }));
        return;
      }
    }
  });
});

const result = proof.workspaceResult;
if (!result) {
  throw new Error('Workspace read result was not returned.');
}
if (result.error) {
  throw new Error(`Workspace read failed: ${result.error}`);
}
if (
  expectedFile
  && !result.nodes.some((node) => normalizePath(node.path) === normalizePath(expectedFile))
) {
  throw new Error(`Workspace read did not include expected file ${expectedFile}.`);
}
if (proof.editBoundaryResult?.approved !== false) {
  throw new Error('An edit outside the selected workspace was not rejected.');
}
if (proof.commandBoundaryResult?.approved !== false) {
  throw new Error('A command outside the selected workspace was not rejected.');
}

console.log(JSON.stringify({
  status: 'pass',
  sessionId: `founder-ide:${presence.endpointId}`,
  workspaceId: presence.workspaceId,
  workspacePath: presence.workspacePath,
  responseType: result.type,
  nodeCount: result.nodes.length,
  nodes: result.nodes.slice(0, 20).map((node) => ({
    path: node.path,
    type: node.type,
    sizeBytes: node.sizeBytes,
  })),
  editBoundary: {
    approved: proof.editBoundaryResult.approved,
    reason: proof.editBoundaryResult.reason,
  },
  commandBoundary: {
    approved: proof.commandBoundaryResult.approved,
    reason: proof.commandBoundaryResult.reason,
  },
  lifecycle,
}, null, 2));

function readJson(file) {
  try {
    return JSON.parse(fs.readFileSync(file, 'utf8'));
  } catch {
    return null;
  }
}

function normalizePath(value) {
  const normalized = path.resolve(value).replaceAll('\\', '/').replace(/\/+$/, '');
  return process.platform === 'win32' ? normalized.toLowerCase() : normalized;
}
