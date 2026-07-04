import { clipboard } from 'electron';
import { exec } from 'node:child_process';
import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';
import { founderNodeAuthHeader } from '@dcf/founder-vault';
import {
  focusComposerInWorkspaceState,
  resolveCursorComposerContext,
} from './cursor-discovery';
import { readNodeConfig } from './vault-manager';
import { throwIfFounderNodeAuthResponse } from './sync-client';

/**
 * Cursor IDE message relay.
 *
 * When a user selects a Cursor chat session in the Founder OS sidebar and
 * sends a message, the API writes a `PendingIdeDispatch` row. Founder Node
 * polls {@link processPendingDispatches} on each sync cycle, and for each
 * pending dispatch it:
 *   1. Resolves the target composer + workspace folder from state.vscdb.
 *   2. Writes the composer to the front of Cursor's workspace focus list.
 *   3. Opens / focuses the workspace via `cursor --reuse-window`.
 *   4. Copies the prompt to the clipboard and pastes into the focused composer
 *      input (does NOT use Ctrl+Shift+L — that opens a brand-new chat tab).
 *   5. Reports the result back to the API.
 */

const MAX_DISPATCHES_PER_CYCLE = 1;
const CURSOR_FOCUS_WAIT_MS = 2500;
const COMPOSER_TAB_SETTLE_MS = 900;
const SESSION_DISPATCH_COOLDOWN_MS = 8_000;

let dispatchCycleInFlight = false;
const lastDispatchBySession = new Map<string, number>();

export type PendingDispatch = {
  id: string;
  sessionId: string;
  prompt: string;
  ideProvider: string;
};

function apiBase(apiBaseUrl: string, path: string): string {
  const base = apiBaseUrl.replace(/\/$/, '');
  return `${base}${path.startsWith('/') ? path : `/${path}`}`;
}

export async function fetchPendingDispatches(
  apiBaseUrl: string,
  nodeId: string,
  nodeToken: string,
): Promise<PendingDispatch[]> {
  const res = await fetch(apiBase(apiBaseUrl, '/api/founder-node/pending-dispatches'), {
    headers: { Authorization: founderNodeAuthHeader(nodeId, nodeToken) },
  });
  if (!res.ok) {
    const text = await res.text().catch(() => '');
    throwIfFounderNodeAuthResponse(res.status, text);
    console.warn(`Pending dispatch poll failed (${res.status}): ${text.slice(0, 200)}`);
    return [];
  }
  const body = (await res.json().catch(() => null)) as PendingDispatch[] | null;
  if (!Array.isArray(body)) return [];
  return body;
}

/** Atomically claim a dispatch row before executing — returns null if already claimed. */
export async function claimPendingDispatch(
  apiBaseUrl: string,
  nodeId: string,
  nodeToken: string,
  dispatchId: string,
): Promise<PendingDispatch | null> {
  const res = await fetch(apiBase(apiBaseUrl, `/api/founder-node/dispatch/${dispatchId}/claim`), {
    method: 'POST',
    headers: { Authorization: founderNodeAuthHeader(nodeId, nodeToken) },
  });
  if (res.status === 404) return null;
  if (!res.ok) {
    const text = await res.text().catch(() => '');
    throwIfFounderNodeAuthResponse(res.status, text);
    console.warn(`Dispatch claim failed (${res.status}): ${text.slice(0, 200)}`);
    return null;
  }
  const body = (await res.json().catch(() => null)) as PendingDispatch | null;
  if (!body?.id) return null;
  return body;
}

export async function completeDispatch(
  apiBaseUrl: string,
  nodeId: string,
  nodeToken: string,
  dispatchId: string,
  input: { result?: string; error?: string },
): Promise<void> {
  const res = await fetch(apiBase(apiBaseUrl, `/api/founder-node/dispatch/${dispatchId}/complete`), {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: founderNodeAuthHeader(nodeId, nodeToken),
    },
    body: JSON.stringify(input),
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`Dispatch complete failed (${res.status}): ${text}`);
  }
}

function execAsync(cmd: string): Promise<{ ok: boolean; error?: string }> {
  return new Promise((resolve) => {
    exec(cmd, (err) => {
      if (err) {
        resolve({ ok: false, error: err.message });
      } else {
        resolve({ ok: true });
      }
    });
  });
}

function sleep(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
}

const ATTACH_IMAGE_RE =
  /<!--founder-attach:image:([^\n]+)\n(data:image\/[a-zA-Z0-9.+-]+;base64,[A-Za-z0-9+/=\s]+)\n-->/g;

type ParsedDispatchPrompt = {
  text: string;
  images: Array<{ name: string; dataUrl: string }>;
};

function parseDispatchPrompt(raw: string): ParsedDispatchPrompt {
  const images: Array<{ name: string; dataUrl: string }> = [];
  const text = raw
    .replace(ATTACH_IMAGE_RE, (_full, name: string, dataUrl: string) => {
      images.push({ name: String(name).trim(), dataUrl: String(dataUrl).replace(/\s+/g, '') });
      return '';
    })
    .trim();
  return { text, images };
}

/**
 * Best-effort: write a data-URL image to the Windows clipboard as a PNG so
 * Cursor can receive it via Ctrl+V. Falls back silently on failure.
 */
async function pasteImageDataUrl(dataUrl: string): Promise<boolean> {
  if (process.platform !== 'win32') return false;
  const m = dataUrl.match(/^data:(image\/[a-zA-Z0-9.+-]+);base64,(.+)$/);
  if (!m) return false;
  const b64 = m[2]!.replace(/\s+/g, '');
  let tmpPng: string | null = null;
  try {
    const bytes = Buffer.from(b64, 'base64');
    if (bytes.length < 32 || bytes.length > 8 * 1024 * 1024) return false;
    tmpPng = path.join(os.tmpdir(), `fn-attach-${Date.now()}-${Math.random().toString(36).slice(2)}.png`);
    fs.writeFileSync(tmpPng, bytes);
    const ps =
      `Add-Type -AssemblyName System.Windows.Forms;` +
      `Add-Type -AssemblyName System.Drawing;` +
      `$img=[System.Drawing.Image]::FromFile('${tmpPng.replace(/'/g, "''")}');` +
      `[System.Windows.Forms.Clipboard]::SetImage($img);` +
      `$img.Dispose();`;
    const result = await execAsync(
      `powershell -NoProfile -Command "${ps.replace(/"/g, '\\"')}"`,
    );
    return result.ok;
  } catch {
    return false;
  } finally {
    if (tmpPng) {
      try {
        fs.unlinkSync(tmpPng);
      } catch {
        /* ignore */
      }
    }
  }
}

/**
 * Send the prompt into the focused Cursor composer input via clipboard +
 * SendKeys. Avoids Ctrl+Shift+L which opens a new chat tab in Cursor 3.x.
 * Image attachments (embedded by Founder OS) are pasted first when possible.
 */
async function sendPromptToFocusedComposer(prompt: string): Promise<void> {
  const { text, images } = parseDispatchPrompt(prompt);

  if (process.platform !== 'win32') {
    try {
      clipboard.writeText(text || prompt);
    } catch (err) {
      throw new Error(`clipboard write failed: ${err instanceof Error ? err.message : String(err)}`);
    }
    throw new Error(
      'Cursor auto-dispatch is Windows-only; prompt copied to clipboard — paste into the focused composer.',
    );
  }

  // Give Cursor time to restore the focused composer tab after workspace activation.
  await sleep(COMPOSER_TAB_SETTLE_MS);

  // Ctrl+L focuses the chat/composer input in the active tab without spawning
  // a new agent session (Ctrl+Shift+L does spawn one — that was the bug).
  const focusCmd =
    'Add-Type -AssemblyName System.Windows.Forms;' +
    '[System.Windows.Forms.SendKeys]::SendWait(\'^l\');' +
    'Start-Sleep -Milliseconds 450;';
  const focusResult = await execAsync(
    `powershell -NoProfile -Command "${focusCmd.replace(/"/g, '\\"')}"`,
  );
  if (!focusResult.ok) {
    throw new Error(`SendKeys focus failed: ${focusResult.error ?? 'unknown error'}`);
  }

  // Paste images (if any), then the text prompt.
  for (const img of images.slice(0, 3)) {
    const ok = await pasteImageDataUrl(img.dataUrl);
    if (!ok) continue;
    const pasteImg =
      'Add-Type -AssemblyName System.Windows.Forms;' +
      '[System.Windows.Forms.SendKeys]::SendWait(\'^v\');' +
      'Start-Sleep -Milliseconds 400;';
    await execAsync(`powershell -NoProfile -Command "${pasteImg.replace(/"/g, '\\"')}"`);
  }

  const finalText =
    text ||
    (images.length > 0
      ? `[Attachments: ${images.map((i) => i.name).join(', ')}]`
      : prompt);
  try {
    clipboard.writeText(finalText);
  } catch (err) {
    throw new Error(`clipboard write failed: ${err instanceof Error ? err.message : String(err)}`);
  }

  const pasteText =
    'Add-Type -AssemblyName System.Windows.Forms;' +
    '[System.Windows.Forms.SendKeys]::SendWait(\'^v\');' +
    'Start-Sleep -Milliseconds 250;' +
    '[System.Windows.Forms.SendKeys]::SendWait(\'{ENTER}\');';
  const result = await execAsync(
    `powershell -NoProfile -Command "${pasteText.replace(/"/g, '\\"')}"`,
  );
  if (!result.ok) {
    throw new Error(`SendKeys failed: ${result.error ?? 'unknown error'}`);
  }
}

/**
 * Focus the workspace window and pre-select the target composer tab via
 * workspace state.vscdb before Cursor reads it on activation.
 */
async function focusCursorComposer(
  composerId: string,
  workspaceStorageId?: string,
  folderPath?: string,
): Promise<{ focusedTab: boolean; openedWorkspace: boolean }> {
  let focusedTab = false;
  if (workspaceStorageId) {
    focusedTab = focusComposerInWorkspaceState(workspaceStorageId, composerId);
  }

  let openedWorkspace = false;
  if (folderPath) {
    const result = await execAsync(`cursor --reuse-window "${folderPath}"`);
    openedWorkspace = result.ok;
    if (!result.ok) {
      console.warn('cursor CLI workspace focus failed:', result.error);
    }
  } else {
    const result = await execAsync('cursor');
    openedWorkspace = result.ok;
    if (!result.ok) {
      console.warn('cursor CLI focus failed:', result.error);
    }
  }

  await sleep(CURSOR_FOCUS_WAIT_MS);
  return { focusedTab, openedWorkspace };
}

/**
 * Execute a single dispatch: focus the target composer tab, push the prompt,
 * report the result back to the API.
 */
export async function executeCursorDispatch(
  apiBaseUrl: string,
  nodeId: string,
  nodeToken: string,
  dispatch: PendingDispatch,
): Promise<void> {
  try {
    const composerId = dispatch.sessionId;
    const ctx = resolveCursorComposerContext(composerId);
    const workspaceStorageId = ctx?.workspaceStorageId;
    const folderPath = ctx?.folderPath;

    const { focusedTab, openedWorkspace } = await focusCursorComposer(
      composerId,
      workspaceStorageId,
      folderPath,
    );

    await sendPromptToFocusedComposer(dispatch.prompt);

    const bits = [
      `composer=${composerId.slice(0, 8)}`,
      focusedTab ? 'tab-focused' : 'tab-focus-skipped',
      openedWorkspace ? 'workspace-opened' : 'workspace-focus-best-effort',
    ];
    await completeDispatch(apiBaseUrl, nodeId, nodeToken, dispatch.id, {
      result: `dispatched (${bits.join(', ')})`,
    });
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    console.error(`Cursor dispatch ${dispatch.id} failed:`, message);
    await completeDispatch(apiBaseUrl, nodeId, nodeToken, dispatch.id, { error: message }).catch(
      (e) => console.error('Failed to report dispatch error:', e),
    );
  }
}

/**
 * Pull pending dispatches, claim each atomically, and execute at most one per
 * cycle so overlapping sync loops cannot paste the same prompt multiple times.
 */
export async function processPendingDispatches(vaultRoot: string): Promise<void> {
  if (dispatchCycleInFlight) return;
  const config = readNodeConfig(vaultRoot);
  if (!config) return;

  dispatchCycleInFlight = true;
  try {
    let dispatches: PendingDispatch[];
    try {
      dispatches = await fetchPendingDispatches(config.apiBaseUrl, config.nodeId, config.nodeToken);
    } catch (err) {
      console.warn('Pending dispatch fetch failed:', err);
      return;
    }

    for (let i = 0; i < Math.min(dispatches.length, MAX_DISPATCHES_PER_CYCLE); i += 1) {
      const candidate = dispatches[i]!;
      const lastAt = lastDispatchBySession.get(candidate.sessionId) ?? 0;
      if (Date.now() - lastAt < SESSION_DISPATCH_COOLDOWN_MS) {
        continue;
      }

      const claimed = await claimPendingDispatch(
        config.apiBaseUrl,
        config.nodeId,
        config.nodeToken,
        candidate.id,
      );
      if (!claimed) continue;

      lastDispatchBySession.set(claimed.sessionId, Date.now());
      await executeCursorDispatch(config.apiBaseUrl, config.nodeId, config.nodeToken, claimed);
    }
  } finally {
    dispatchCycleInFlight = false;
  }
}
