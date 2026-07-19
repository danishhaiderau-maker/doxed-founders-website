/**
 * Phase 3 — named-pipe IPC client (Founder Node side).
 *
 * The IDE extension hosts the SERVER (server.ts). Founder Node connects to
 * it as a CLIENT to bridge workspace state + edit/command reviews between
 * the API and the IDE.
 *
 * Lifecycle:
 *   1. connectToIde() is called from main.ts shortly after the IDE process
 *      is detected. Looks up installId + ipcSecret from install.json /
 *      node-config.json and connects to `\\.\pipe\founder-ide-{installId}`.
 *   2. Sends `IpcHello` with protocolVersion + installId + ipcSecret +
 *      capabilities. Waits for `IpcAuthState(state=connected)`.
 *   3. On success: emits `connected`, propagates `ideHandshakeActive = true`
 *      to the API via the existing heartbeat.
 *   4. On failure: emits `disconnected`, marks handshake inactive.
 *   5. Heartbeat every 15s; reconnect with exponential backoff on disconnect.
 *
 * Replay protection: every incoming message nonce is checked against a
 * `NonceTracker`. Duplicates are rejected.
 *
 * The class is event-based (EventEmitter) so main.ts can wire side-effects
 * (notify the API, show a tray notification) without coupling to the wire
 * format.
 */
import { EventEmitter } from 'node:events';
import * as net from 'node:net';
import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';
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
} from '@dcf/founder-ide-extension/ipc';

/** Heartbeat — sent every 15s. Server drops us if it doesn't see one for 60s. */
const HEARTBEAT_INTERVAL_MS = 15_000;
/** Initial reconnect backoff. Doubles on each failure up to maxBackoffMs. */
const INITIAL_BACKOFF_MS = 1_000;
/** Max reconnect backoff (cap). */
const MAX_BACKOFF_MS = 30_000;
/** Connect timeout — if TCP connect() doesn't complete, retry. */
const CONNECT_TIMEOUT_MS = 5_000;

/** Capabilities the Founder Node client advertises to the IDE. */
const FOUNDER_NODE_CAPABILITIES: IpcCapability[] = [
  'workspace',
  'taskState',
  'chatPrompt',
  'chatPromptResult',
  'workspaceReadRequest',
  'workspaceReadResult',
  'proposedEdit',
  'editReviewResult',
  'commandRequest',
  'commandReviewResult',
  'commandOutput',
  'cancel',
  'gatewayHealth',
  'memoryHealth',
  'versionState',
  'heartbeat',
];

/** Events emitted by IdeIpcClient. */
export interface IdeIpcClientEvents {
  connected: () => void;
  disconnected: (reason: string) => void;
  message: (msg: IpcMessage) => void;
  authState: (state: IpcAuthState['state'], reason?: string) => void;
}

/** Resolve install identity from install.json or node-config.json. */
function resolveInstallIdentity(): { installId: string; ipcSecret: string } | null {
  const vault = path.join(os.homedir(), 'FounderVault');
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
    /* fall through */
  }
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
    /* not paired */
  }
  return null;
}

/** Compute the pipe path for the current platform + installId. */
export function pipePathFor(installId: string): string {
  if (process.platform === 'win32') {
    return `\\\\.\\pipe\\founder-ide-${installId}`;
  }
  return `/tmp/founder-ide-${installId}.sock`;
}

/**
 * Named-pipe IPC client. Connects to the IDE extension's server.
 *
 * The class is intentionally small — it owns the socket, handshake,
 * heartbeat, and reconnect logic. Dispatching incoming messages to
 * workspace/edit/command handlers is the caller's job (via the `message`
 * event).
 */
export class IdeIpcClient extends EventEmitter {
  private socket: net.Socket | null = null;
  private heartbeatTimer: NodeJS.Timeout | null = null;
  private reconnectTimer: NodeJS.Timeout | null = null;
  private incomingBuffer = '';
  private nonces = new NonceTracker();
  private backoffMs = INITIAL_BACKOFF_MS;
  private activelyClosed = false;
  private handshakeComplete = false;
  private identity: { installId: string; ipcSecret: string } | null = null;

  constructor() {
    super();
  }

  /**
   * Begin the connect → handshake → heartbeat loop. Resolves once the first
   * `authState=connected` is observed. Retries in the background until
   * `disconnect()` is called explicitly.
   *
   * If no install identity is on disk yet (not paired), resolves `false`
   * without retrying — the caller should call connect() again after pairing.
   */
  connect(): Promise<boolean> {
    this.identity = resolveInstallIdentity();
    if (!this.identity) {
      return Promise.resolve(false);
    }
    this.activelyClosed = false;
    return this.attemptConnection();
  }

  /**
   * Stop the client. Cancels pending reconnects, destroys the socket,
   * clears all timers. Safe to call multiple times.
   */
  disconnect(): void {
    this.activelyClosed = true;
    this.handshakeComplete = false;
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    if (this.heartbeatTimer) {
      clearInterval(this.heartbeatTimer);
      this.heartbeatTimer = null;
    }
    if (this.socket) {
      try {
        this.socket.destroy();
      } catch {
        /* ignore */
      }
      this.socket = null;
    }
  }

  /** True if the handshake completed and the socket is still open. */
  isHandshakeActive(): boolean {
    return this.handshakeComplete && this.socket !== null && !this.socket.destroyed;
  }

  /** Send a fire-and-forget message to the server. Returns true on success. */
  send(msg: IpcMessage): boolean {
    if (!this.socket || this.socket.destroyed) return false;
    try {
      this.socket.write(`${JSON.stringify(msg)}\n`);
      return true;
    } catch {
      return false;
    }
  }

  /** Build + send a heartbeat. Exposed for main.ts's heartbeat loop. */
  sendHeartbeat(): boolean {
    return this.send(this.makeHeartbeat());
  }

  private makeHello(): IpcHello {
    if (!this.identity) throw new Error('IdeIpcClient: no install identity');
    return {
      type: 'hello',
      nonce: generateNonce(),
      ts: new Date().toISOString(),
      protocolVersion: IPC_PROTOCOL_VERSION,
      installId: this.identity.installId,
      ipcSecret: this.identity.ipcSecret,
      capabilities: FOUNDER_NODE_CAPABILITIES,
    };
  }

  private makeHeartbeat(): IpcHeartbeat {
    return {
      type: 'heartbeat',
      nonce: generateNonce(),
      ts: new Date().toISOString(),
      at: new Date().toISOString(),
    };
  }

  /**
   * Attempt one TCP connect → handshake. Resolves true on handshake success,
   * false on identity-missing. On connect/handshake failure, schedules a
   * retry and resolves false.
   */
  private attemptConnection(): Promise<boolean> {
    if (!this.identity) return Promise.resolve(false);
    const pipePath = pipePathFor(this.identity.installId);

    return new Promise<boolean>((resolve) => {
      let settled = false;
      const settle = (ok: boolean) => {
        if (!settled) {
          settled = true;
          resolve(ok);
        }
      };

      const socket = net.createConnection(pipePath);
      socket.setEncoding('utf8');
      socket.setTimeout(CONNECT_TIMEOUT_MS);

      const fail = (reason: string) => {
        try {
          socket.destroy();
        } catch {
          /* ignore */
        }
        this.handleDisconnect(reason);
        settle(false);
      };

      socket.on('timeout', () => fail('connect timeout'));
      socket.on('error', (err) => fail(err.message));

      socket.on('connect', () => {
        socket.setTimeout(0);
        this.socket = socket;
        // Send hello immediately. Server responds with authState.
        try {
          socket.write(`${JSON.stringify(this.makeHello())}\n`);
        } catch (err) {
          fail(`hello send failed: ${(err as Error).message}`);
          return;
        }
        // Don't settle yet — wait for authState=connected.
      });

      socket.on('data', (chunk: Buffer | string) => {
        this.incomingBuffer += typeof chunk === 'string' ? chunk : chunk.toString('utf8');
        let nl = this.incomingBuffer.indexOf('\n');
        while (nl >= 0) {
          const raw = this.incomingBuffer.slice(0, nl);
          this.incomingBuffer = this.incomingBuffer.slice(nl + 1);
          nl = this.incomingBuffer.indexOf('\n');
          if (!raw.trim()) continue;
          let msg: unknown;
          try {
            msg = JSON.parse(raw);
          } catch {
            continue;
          }
          this.handleMessage(msg, () => settle(true));
        }
      });

      socket.on('close', () => {
        this.handleDisconnect('socket closed');
        settle(false);
      });
    });
  }

  private handleMessage(raw: unknown, onAuthConnected: () => void): void {
    if (!isIpcMessage(raw)) return;
    const msg = raw as IpcMessage;

    if (msg.type === 'authState') {
      const auth = msg as IpcAuthState;
      this.emit('authState', auth.state, auth.reason);
      if (auth.state === 'connected') {
        this.handshakeComplete = true;
        this.backoffMs = INITIAL_BACKOFF_MS;
        this.startHeartbeat();
        this.emit('connected');
        onAuthConnected();
      } else {
        // revoked / pairing / not_paired / paired_gateway_unreachable /
        // token_expired — treat as a denial and tear down so we don't keep
        // retrying against a server that has rejected us.
        this.handshakeComplete = false;
        try {
          this.socket?.destroy();
        } catch {
          /* ignore */
        }
      }
      return;
    }

    if (msg.type === 'heartbeat') {
      // Server heartbeat — no action required (liveness handled by the
      // server-side sweep on our outbound heartbeats).
      return;
    }

    // Replay protection: every other message must have a fresh nonce.
    if (!this.nonces.check(msg.nonce)) {
      return;
    }
    this.emit('message', msg);
  }

  private startHeartbeat(): void {
    if (this.heartbeatTimer) clearInterval(this.heartbeatTimer);
    this.heartbeatTimer = setInterval(() => {
      if (!this.isHandshakeActive()) return;
      this.sendHeartbeat();
    }, HEARTBEAT_INTERVAL_MS);
  }

  private handleDisconnect(reason: string): void {
    const wasActive = this.handshakeComplete;
    this.handshakeComplete = false;
    if (this.heartbeatTimer) {
      clearInterval(this.heartbeatTimer);
      this.heartbeatTimer = null;
    }
    this.socket = null;
    this.incomingBuffer = '';
    if (wasActive) this.emit('disconnected', reason);
    this.scheduleReconnect();
  }

  private scheduleReconnect(): void {
    if (this.activelyClosed) return;
    if (this.reconnectTimer) return;
    const wait = this.backoffMs;
    this.backoffMs = Math.min(this.backoffMs * 2, MAX_BACKOFF_MS);
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      void this.attemptConnection().catch(() => false);
    }, wait);
  }
}

/**
 * Convenience entry point: build a client, connect it, log lifecycle events.
 * Used by main.ts to spin up the client after the IDE process is detected.
 */
export function startIdeIpcClient(): IdeIpcClient {
  const client = new IdeIpcClient();
  client.on('connected', () => console.log('[ide-ipc] connected to IDE'));
  client.on('disconnected', (reason) => console.log(`[ide-ipc] disconnected: ${reason}`));
  client.on('authState', (state, reason) =>
    console.log(`[ide-ipc] authState=${state}${reason ? ` (${reason})` : ''}`),
  );
  void client.connect().catch((err) => console.warn('[ide-ipc] connect failed:', err));
  return client;
}
