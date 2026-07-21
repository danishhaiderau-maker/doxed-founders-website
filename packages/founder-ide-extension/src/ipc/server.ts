/**
 * Phase 3 — named-pipe IPC server (hosted by the IDE extension).
 *
 * The IDE hosts the SERVER because that's where the workspace/files live.
 * Founder Node connects as a CLIENT (Task 4). The API adapter connects as
 * a CLIENT via Founder Node's HTTPS relay (Task 5).
 *
 * Security model:
 *   - The pipe is named `\\.\pipe\founder-ide-{installId}` on Windows and
 *     `/tmp/founder-ide-{installId}.sock` on Unix. The `installId` is per
 *     install, so a malicious install can't hijack another install's pipe.
 *   - Every connecting client MUST send `IpcHello` with the install's
 *     `ipcSecret` as the first frame. We compare in constant time and close
 *     the connection on mismatch (after emitting authState=revoked).
 *   - Replay protection: every incoming request nonce is checked against a
 *     `NonceTracker`; duplicates are rejected.
 *
 * **Known gap (Windows ACL):** pure-JS Node cannot set user-only file
 * permissions on a Windows named pipe (that needs the Win32 SECURITY_DESCRIPTOR
 * API). The mitigation is the per-install `installId` (so the pipe name is
 * unguessable) + per-install `ipcSecret` (so even local privilege escalation
 * to "any process that can open the pipe" still can't pass the handshake).
 * Together these defeat cross-install + cross-user hijack on the same machine.
 * A future release should add a native helper to set the pipe ACL to the
 * current user's SID; tracked separately.
 */
import * as net from 'node:net';
import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';
import * as crypto from 'node:crypto';
import type { Socket } from 'node:net';
import {
  IPC_PROTOCOL_VERSION,
  NonceTracker,
  generateNonce,
  isIpcMessage,
  type IpcAuthState,
  type IpcCapability,
  type IpcHello,
  type IpcHeartbeat,
  type IpcMessage,
} from './protocol.js';

/** Heartbeat interval — both sides emit every 15s to keep the pipe alive. */
const HEARTBEAT_INTERVAL_MS = 15_000;
/** Drop a connection if no heartbeat for 60s (4 missed heartbeats). */
const HANDSHAKE_TIMEOUT_MS = 60_000;
/** Drop a connection if `hello` doesn't arrive within 10s of connect. */
const HELLO_TIMEOUT_MS = 10_000;

interface ActiveClient {
  socket: Socket;
  /** Install id presented in hello (must match server's). */
  installId: string;
  /** Capabilities the client advertised in hello. */
  capabilities: Set<IpcCapability>;
  /** Last heartbeat received (ms epoch). */
  lastHeartbeat: number;
  /** Replay-protection tracker for this connection. */
  nonces: NonceTracker;
}

let server: net.Server | null = null;
let installId: string | null = null;
let ipcSecret: string | null = null;
const clients = new Set<ActiveClient>();
let heartbeatTimer: NodeJS.Timeout | null = null;

/**
 * Resolve the install identity from ~/FounderVault/install.json or
 * ~/FounderVault/node-config.json. Returns null if neither is present.
 */
function resolveInstallIdentity(): { installId: string; ipcSecret: string } | null {
  const vault = path.join(os.homedir(), 'FounderVault');
  // Prefer the sidecar install.json (matches the tray's bootstrap).
  const sidecar = path.join(vault, 'install.json');
  try {
    if (fs.existsSync(sidecar)) {
      const parsed = JSON.parse(fs.readFileSync(sidecar, 'utf8')) as {
        installId?: string;
        ipcSecret?: string;
      };
      if (parsed.installId && parsed.ipcSecret) {
        return { installId: parsed.installId, ipcSecret: parsed.ipcSecret };
      }
    }
  } catch {
    /* fall through to node-config.json */
  }
  // Fall back to node-config.json (written post-pair by both the tray + IDE).
  const configFile = path.join(vault, 'node-config.json');
  try {
    if (fs.existsSync(configFile)) {
      const parsed = JSON.parse(fs.readFileSync(configFile, 'utf8')) as {
        installId?: string;
        ipcSecret?: string;
      };
      if (parsed.installId && parsed.ipcSecret) {
        return { installId: parsed.installId, ipcSecret: parsed.ipcSecret };
      }
    }
  } catch {
    /* not paired yet */
  }
  return null;
}

/** Compute the pipe path for the current platform + installId. */
function pipePathFor(installId: string): string {
  if (process.platform === 'win32') {
    return `\\\\.\\pipe\\founder-ide-${installId}`;
  }
  // macOS / Linux: use /tmp — same-machine only, so /tmp is fine. The
  // installId-specific suffix prevents cross-install collisions.
  return `/tmp/founder-ide-${installId}.sock`;
}

/** Constant-time string comparison. */
function constantTimeEquals(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  const ab = Buffer.from(a, 'utf8');
  const bb = Buffer.from(b, 'utf8');
  return crypto.timingSafeEqual(ab, bb);
}

/**
 * Start the IPC server. Resolves once the server is listening. No-op if
 * already running. Resolves with `null` (and logs) if no install identity
 * is on disk yet — the extension should call startIpcServer() again after
 * pairing completes.
 */
export async function startIpcServer(): Promise<net.Server | null> {
  if (server) return server;
  const identity = resolveInstallIdentity();
  if (!identity) {
    console.warn('Founder OS IPC server: no install identity yet (not paired).');
    return null;
  }
  installId = identity.installId;
  ipcSecret = identity.ipcSecret;

  const pipePath = pipePathFor(installId);
  // On Unix, remove a stale socket file from a previous run.
  if (process.platform !== 'win32') {
    try {
      if (fs.existsSync(pipePath)) fs.unlinkSync(pipePath);
    } catch {
      /* best-effort */
    }
  }

  return new Promise((resolve, reject) => {
    const srv = net.createServer((socket) => onClientConnected(socket));
    const onStartupError = (err: NodeJS.ErrnoException) => {
      if (process.platform === 'win32' && err.code === 'EADDRINUSE') {
        console.info('Founder OS IPC is already served by another Founder IDE window.');
        resolve(null);
        return;
      }
      reject(err);
    };
    srv.once('error', onStartupError);
    srv.listen(pipePath, () => {
      srv.off('error', onStartupError);
      srv.on('error', (err) => {
        console.error('Founder OS IPC server error:', err);
      });
      server = srv;
      console.log(`Founder OS IPC server listening on ${pipePath}`);
      // Heartbeat sweep — drop dead clients every 15s.
      heartbeatTimer = setInterval(() => sweepDeadClients(), HEARTBEAT_INTERVAL_MS);
      resolve(srv);
    });
  });
}

/** Stop the server + close all client connections. Safe to call multiple times. */
export function stopIpcServer(): void {
  if (heartbeatTimer) {
    clearInterval(heartbeatTimer);
    heartbeatTimer = null;
  }
  for (const client of clients) {
    try {
      client.socket.destroy();
    } catch {
      /* ignore */
    }
  }
  clients.clear();
  if (server) {
    try {
      server.close();
    } catch {
      /* ignore */
    }
    server = null;
  }
  installId = null;
  ipcSecret = null;
}

/** Number of currently-authenticated clients. Exposed for diagnostics + tests. */
export function connectedClientCount(): number {
  return clients.size;
}

/** True if at least one client has an active handshake (used by isConnected). */
export function hasActiveHandshake(): boolean {
  return clients.size > 0;
}

function onClientConnected(socket: Socket): void {
  // Hello timeout — drop if we don't get a valid hello within HELLO_TIMEOUT_MS.
  const helloTimer = setTimeout(() => {
    if (!clients.has((socket as unknown as { _client?: ActiveClient })._client!)) {
      try {
        socket.destroy();
      } catch {
        /* ignore */
      }
    }
  }, HELLO_TIMEOUT_MS);

  let incomingBuffer = '';

  socket.setEncoding('utf8');
  socket.on('data', (chunk: Buffer | string) => {
    incomingBuffer += typeof chunk === 'string' ? chunk : chunk.toString('utf8');
    // Messages are newline-delimited JSON. Walk through any complete frames.
    let nl = incomingBuffer.indexOf('\n');
    while (nl >= 0) {
      const raw = incomingBuffer.slice(0, nl);
      incomingBuffer = incomingBuffer.slice(nl + 1);
      nl = incomingBuffer.indexOf('\n');
      if (!raw.trim()) continue;
      let msg: unknown;
      try {
        msg = JSON.parse(raw);
      } catch {
        continue;
      }
      handleMessage(socket, msg, helloTimer);
    }
  });
  socket.on('error', () => {
    /* swallow — close handler cleans up */
  });
  socket.on('close', () => {
    clearTimeout(helloTimer);
    // Remove from clients if present.
    for (const c of clients) {
      if (c.socket === socket) {
        clients.delete(c);
        break;
      }
    }
  });
}

function handleMessage(socket: Socket, raw: unknown, helloTimer: NodeJS.Timeout): void {
  if (!isIpcMessage(raw)) return;
  const msg = raw as IpcMessage;

  // The very first message MUST be hello. Pre-hello messages are ignored.
  const preHelloClient = findClientBySocket(socket);
  if (msg.type !== 'hello' && !preHelloClient) {
    return;
  }

  if (msg.type === 'hello') {
    clearTimeout(helloTimer);
    handleHello(socket, msg);
    return;
  }

  // After hello: heartbeat refreshes the liveness clock; everything else is
  // gated on the per-connection nonce tracker.
  const client = preHelloClient!;
  if (msg.type === 'heartbeat') {
    client.lastHeartbeat = Date.now();
    return;
  }

  if (!client.nonces.check(msg.nonce)) {
    // Duplicate — drop silently. (Replay attempt or genuine retransmit; both
    // are defeated by the tracker.)
    return;
  }

  if (
    msg.type !== 'chatPrompt' &&
    msg.type !== 'workspaceReadRequest' &&
    msg.type !== 'proposedEdit' &&
    msg.type !== 'commandRequest' &&
    msg.type !== 'cancel'
  ) {
    return;
  }

  // Keep vscode out of the transport-only test process. The action module is
  // loaded only after a peer has passed the install identity + nonce checks.
  void import('./action-handlers.js')
    .then(({ handleAuthenticatedAction }) =>
      handleAuthenticatedAction(msg, (response) => send(socket, response)),
    )
    .catch((error) => {
      console.error('Founder OS IPC action failed:', error);
      const reason = error instanceof Error ? error.message : String(error);
      const failure =
        msg.type === 'chatPrompt'
          ? {
              type: 'chatPromptResult' as const,
              requestId: msg.requestId,
              delivered: false,
              error: reason,
            }
          : msg.type === 'workspaceReadRequest'
            ? {
                type: 'workspaceReadResult' as const,
                requestId: msg.requestId,
                nodes: [],
                error: reason,
              }
            : msg.type === 'proposedEdit'
              ? {
                  type: 'editReviewResult' as const,
                  requestId: msg.requestId,
                  approved: false,
                  reason,
                }
              : msg.type === 'commandRequest'
                ? {
                    type: 'commandReviewResult' as const,
                    requestId: msg.requestId,
                    approved: false,
                    reason,
                  }
                : null;
      if (failure) {
        send(socket, {
          ...failure,
          nonce: generateNonce(),
          ts: new Date().toISOString(),
        });
      }
    });
}

function handleHello(socket: Socket, hello: IpcHello): void {
  // Validate protocol version.
  if (hello.protocolVersion !== IPC_PROTOCOL_VERSION) {
    send(socket, makeAuthState('revoked', `protocol version ${hello.protocolVersion} unsupported`));
    socket.destroy();
    return;
  }
  // Validate installId matches.
  if (!installId || hello.installId !== installId) {
    send(socket, makeAuthState('revoked', 'installId mismatch'));
    socket.destroy();
    return;
  }
  // Validate ipcSecret in constant time.
  if (!ipcSecret || !constantTimeEquals(hello.ipcSecret, ipcSecret)) {
    send(socket, makeAuthState('revoked', 'invalid ipcSecret'));
    socket.destroy();
    return;
  }

  // Success — register the client.
  const client: ActiveClient = {
    socket,
    installId: hello.installId,
    capabilities: new Set(hello.capabilities),
    lastHeartbeat: Date.now(),
    nonces: new NonceTracker(),
  };
  // Stash on the socket so the hello timer can find it.
  (socket as unknown as { _client?: ActiveClient })._client = client;
  clients.add(client);

  send(socket, makeAuthState('connected', 'handshake accepted'));
}

/** Send a single IpcMessage as a newline-delimited JSON frame. */
function send(socket: Socket, msg: IpcMessage): void {
  try {
    socket.write(`${JSON.stringify(msg)}\n`);
  } catch {
    /* ignore backpressure / closed socket */
  }
}

function makeAuthState(state: IpcAuthState['state'], reason?: string): IpcAuthState {
  return {
    type: 'authState',
    state,
    nonce: generateNonce(),
    ts: new Date().toISOString(),
    ...(reason ? { reason } : {}),
  };
}

function makeHeartbeat(): IpcHeartbeat {
  return {
    type: 'heartbeat',
    nonce: generateNonce(),
    ts: new Date().toISOString(),
    at: new Date().toISOString(),
  };
}

function findClientBySocket(socket: Socket): ActiveClient | undefined {
  for (const c of clients) {
    if (c.socket === socket) return c;
  }
  return undefined;
}

/** Sweep clients that haven't heartbeated within the timeout window. */
function sweepDeadClients(): void {
  const cutoff = Date.now() - HANDSHAKE_TIMEOUT_MS;
  for (const client of clients) {
    if (client.lastHeartbeat < cutoff) {
      try {
        client.socket.destroy();
      } catch {
        /* ignore */
      }
      clients.delete(client);
      continue;
    }
    // Emit a heartbeat so the client knows we're still here too.
    send(client.socket, makeHeartbeat());
  }
}

// Exported for tests only — not part of the public surface.
export const __testHooks = {
  pipePathFor,
  resolveInstallIdentity,
  constantTimeEquals,
  handleHello,
  clients,
};
