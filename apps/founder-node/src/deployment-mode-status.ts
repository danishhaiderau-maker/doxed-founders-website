import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';

/**
 * Phase 7 — Private-mode runtime status probe + local HTTP endpoint.
 *
 * Serves `GET /api/deployment-mode/runtime-status` on the Founder Node's local
 * port (default 7012) so the cloud API / dashboard panel can reach the laptop
 * to render the "What's running right now" block. See docs/DEPLOYMENT-MODES-UX.md
 * §3 (mode panel) and §6 (phone remote per mode).
 *
 * The probe is best-effort: every check degrades gracefully to `'not-installed'`
 * or `false` if the underlying tool (Forgejo / cloudflared / Tailscale) isn't
 * present. We never throw — a half-broken Private mode still reports the parts
 * that are alive.
 */

// Port 7002 is reserved for the canonical BTC showcase bot. Keeping Founder
// Node on a distinct loopback port prevents Windows from accepting both the
// bot's 0.0.0.0:7002 listener and this 127.0.0.1 listener, which otherwise
// makes a localhost Cloudflare origin intermittently reach the wrong service.
const DEFAULT_PORT = Number(process.env.FOUNDER_NODE_PORT ?? 7012);
const FORGEJO_PROBE_URL = process.env.FORGEJO_PROBE_URL ?? 'http://127.0.0.1:3000';
const STATUS_CACHE_MS = 5_000;
const execFileAsync = promisify(execFile);
let cachedStatus: { expiresAt: number; value: DeploymentRuntimeStatusResponse } | null = null;
let statusProbeInFlight: Promise<DeploymentRuntimeStatusResponse> | null = null;

export interface DeploymentRuntimeStatusResponse {
  forgejo: 'online' | 'offline' | 'not-installed';
  sqlite: { file: string | null; sizeBytes: number | null };
  tunnel: { active: boolean; url: string | null };
  tailscale: { reachable: boolean; hostname: string | null };
  probedAt: string;
}

/**
 * Probe all Private-mode runtime components. Safe to call on any platform;
 * missing tools are reported as `'not-installed'` / `false`, never thrown.
 */
export async function probeDeploymentRuntimeStatus(
  vaultRoot: string,
): Promise<DeploymentRuntimeStatusResponse> {
  const [forgejo, sqlite, tunnel, tailscale] = await Promise.all([
    probeForgejo(),
    probeSqlite(vaultRoot),
    probeTunnel(),
    probeTailscale(),
  ]);

  return {
    forgejo,
    sqlite,
    tunnel,
    tailscale,
    probedAt: new Date().toISOString(),
  };
}

function fallbackDeploymentRuntimeStatus(): DeploymentRuntimeStatusResponse {
  return {
    forgejo: 'not-installed',
    sqlite: { file: null, sizeBytes: null },
    tunnel: { active: false, url: null },
    tailscale: { reachable: false, hostname: null },
    probedAt: new Date().toISOString(),
  };
}

export function getDeploymentRuntimeStatus(
  vaultRoot: string,
): DeploymentRuntimeStatusResponse {
  if (cachedStatus && cachedStatus.expiresAt > Date.now()) {
    return cachedStatus.value;
  }

  if (!statusProbeInFlight) {
    // Defer the probe itself so synchronous setup work (including filesystem
    // discovery) can never delay the status response on a busy machine.
    statusProbeInFlight = Promise.resolve()
      .then(() => probeDeploymentRuntimeStatus(vaultRoot))
      .then((value) => {
        cachedStatus = { value, expiresAt: Date.now() + STATUS_CACHE_MS };
        return value;
      })
      .catch((err) => {
        console.warn('[deployment-mode] runtime-status refresh failed:', err);
        return cachedStatus?.value ?? fallbackDeploymentRuntimeStatus();
      })
      .finally(() => {
        statusProbeInFlight = null;
      });
  }

  // Never make dashboard or tunnel requests wait for OS process discovery.
  // A background refresh will replace this stale/default value for the next poll.
  return cachedStatus?.value ?? fallbackDeploymentRuntimeStatus();
}

/** Check whether Forgejo responds on localhost:3000 (its default bind port). */
async function probeForgejo(): Promise<'online' | 'offline' | 'not-installed'> {
  try {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 1500);
    const res = await fetch(FORGEJO_PROBE_URL, {
      signal: controller.signal,
      headers: { Accept: 'text/html' },
    });
    clearTimeout(timeout);
    // Forgejo returns 200 on the landing page. Any HTTP response means something
    // is listening on :3000; treat 2xx/3xx as online.
    if (res.status < 400) return 'online';
    return 'offline';
  } catch {
    // Could be ECONNREFUSED (nothing on :3000) or ENOTFOUND. Either way, not online.
    return 'not-installed';
  }
}

/** Look for a dev.db file in the vault's projects/ tree. */
async function probeSqlite(
  vaultRoot: string,
): Promise<{ file: string | null; sizeBytes: number | null }> {
  const projectsDir = path.join(vaultRoot, 'projects');
  const found = findDevDb(projectsDir);
  if (!found) {
    // Fallback: a top-level dev.db in the vault root (legacy layout).
    const legacy = path.join(vaultRoot, 'dev.db');
    if (fs.existsSync(legacy)) {
      return { file: legacy, sizeBytes: safeSize(legacy) };
    }
    return { file: null, sizeBytes: null };
  }
  return { file: found, sizeBytes: safeSize(found) };
}

function findDevDb(dir: string): string | null {
  if (!fs.existsSync(dir)) return null;
  let entries: fs.Dirent[] = [];
  try {
    entries = fs.readdirSync(dir, { withFileTypes: true });
  } catch {
    return null;
  }
  for (const entry of entries) {
    const full = path.join(dir, entry.name);
    if (entry.isFile() && entry.name === 'dev.db') return full;
    if (entry.isDirectory()) {
      const nested = findDevDb(full);
      if (nested) return nested;
    }
  }
  return null;
}

function safeSize(p: string): number {
  try {
    return fs.statSync(p).size;
  } catch {
    return 0;
  }
}

/** Check whether a cloudflared tunnel process is currently running. */
async function probeTunnel(): Promise<{ active: boolean; url: string | null }> {
  // We don't track the assigned trycloudflare.com URL yet (Phase 7 stub);
  // presence of the process is the signal the panel needs for now.
  if (process.platform === 'win32') {
    try {
      const { stdout } = await execFileAsync(
        'tasklist',
        ['/FI', 'IMAGENAME eq cloudflared.exe', '/NH', '/FO', 'CSV'],
        {
          encoding: 'utf8',
          timeout: 2000,
          windowsHide: true,
        },
      );
      const active = stdout.toLowerCase().includes('cloudflared.exe');
      return { active, url: null };
    } catch {
      return { active: false, url: null };
    }
  }
  try {
    const { stdout } = await execFileAsync('pgrep', ['-x', 'cloudflared'], {
      encoding: 'utf8',
      timeout: 2000,
    });
    return { active: stdout.trim().length > 0, url: null };
  } catch {
    return { active: false, url: null };
  }
}

/** Check Tailscale reachability + hostname via the CLI if available. */
async function probeTailscale(): Promise<{ reachable: boolean; hostname: string | null }> {
  try {
    const { stdout } = await execFileAsync('tailscale', ['status', '--json'], {
      encoding: 'utf8',
      timeout: 2500,
      windowsHide: true,
    });
    const parsed = JSON.parse(stdout) as { Self?: { HostName?: string }; BackendState?: string };
    const reachable = parsed.BackendState === 'Running' || Boolean(parsed.Self);
    return { reachable, hostname: parsed.Self?.HostName ?? null };
  } catch {
    // tailscale CLI not on PATH, or not logged in.
    return { reachable: false, hostname: null };
  }
}

/**
 * Start the local HTTP server exposing the runtime-status endpoint. Returns a
 * close() handle for shutdown. The server binds to 127.0.0.1 only — the cloud
 * API reaches it via Tailscale or the cloudflared tunnel, never raw LAN.
 */
export function startDeploymentRuntimeStatusServer(
  vaultRoot: string,
  port: number = DEFAULT_PORT,
): { close: () => void } {
  const server = http.createServer(async (req, res) => {
    if (!req.url?.startsWith('/api/deployment-mode/runtime-status')) {
      res.writeHead(404, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ error: 'not found' }));
      return;
    }

    try {
      const status = getDeploymentRuntimeStatus(vaultRoot);
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify(status));
    } catch (err) {
      res.writeHead(500, { 'Content-Type': 'application/json' });
      res.end(
        JSON.stringify({
          error: err instanceof Error ? err.message : 'runtime-status probe failed',
        }),
      );
    }
  });

  server.listen(port, '127.0.0.1', () => {
    // Best-effort log; never fail the app if the port is taken.
    console.log(`[deployment-mode] runtime-status on http://127.0.0.1:${port}/api/deployment-mode/runtime-status`);
    getDeploymentRuntimeStatus(vaultRoot);
  });

  server.on('error', (err: NodeJS.ErrnoException) => {
    // EADDRINUSE is common when the dev server already holds the port — degrade
    // silently rather than crashing the tray app.
    if (err.code === 'EADDRINUSE') {
      console.warn(`[deployment-mode] port ${port} busy — runtime-status endpoint disabled`);
    } else {
      console.error('[deployment-mode] runtime-status server error:', err.message);
    }
  });

  return { close: () => server.close() };
}
