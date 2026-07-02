import { clipboard } from 'electron';
import { exec } from 'node:child_process';
import { founderNodeAuthHeader } from '@dcf/founder-vault';
import { readNodeConfig } from './vault-manager';
import { throwIfFounderNodeAuthResponse } from './sync-client';

/**
 * Cursor IDE message relay.
 *
 * When a user selects a Cursor chat session in the Founder OS sidebar and
 * sends a message, the API writes a `PendingIdeDispatch` row. Founder Node
 * polls {@link processPendingDispatches} on each sync cycle, and for each
 * pending dispatch it:
 *   1. Opens / focuses the local Cursor IDE.
 *   2. Copies the prompt to the OS clipboard (via Electron's clipboard —
 *      avoids shell-escaping the prompt text).
 *   3. Sends Ctrl+Shift+L (open Cursor chat), Ctrl+V (paste), Enter (send)
 *      via PowerShell SendKeys on Windows.
 *   4. Reports the result back to the API so the row is marked DISPATCHED.
 *
 * The SendKeys approach is a pragmatic Windows-only solution and requires
 * Cursor to be the focused window after step 1.
 */

const MAX_DISPATCHES_PER_CYCLE = 3;
const CURSOR_FOCUS_WAIT_MS = 2000;

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

/**
 * Send the prompt into the focused Cursor window via clipboard + SendKeys.
 * Windows-only; on other platforms we just copy to the clipboard and let the
 * user paste manually (returned as a soft error so the dispatch is still
 * marked dispatched and we don't retry forever).
 */
async function sendPromptToCursor(prompt: string): Promise<void> {
  // Always copy to clipboard first — works cross-platform and gives the user
  // a manual-paste fallback if SendKeys can't reach Cursor.
  try {
    clipboard.writeText(prompt);
  } catch (err) {
    throw new Error(`clipboard write failed: ${err instanceof Error ? err.message : String(err)}`);
  }

  if (process.platform !== 'win32') {
    throw new Error('Cursor auto-dispatch is Windows-only; prompt copied to clipboard — press Ctrl+V in Cursor.');
  }

  // Ctrl+Shift+L opens the Cursor chat pane, Ctrl+V pastes, Enter sends.
  // SendKeys: ^ = Ctrl, + = Shift, {ENTER} = Enter key.
  const sendKeysCmd =
    "Add-Type -AssemblyName System.Windows.Forms;" +
    "[System.Windows.Forms.SendKeys]::SendWait('^+l');" +
    "Start-Sleep -Milliseconds 600;" +
    "[System.Windows.Forms.SendKeys]::SendWait('^v');" +
    "Start-Sleep -Milliseconds 250;" +
    "[System.Windows.Forms.SendKeys]::SendWait('{ENTER}');";

  const result = await execAsync(`powershell -NoProfile -Command "${sendKeysCmd.replace(/"/g, '\\"')}"`);
  if (!result.ok) {
    throw new Error(`SendKeys failed: ${result.error ?? 'unknown error'}`);
  }
}

/**
 * Open or focus Cursor for the given workspace path. When no workspace path is
 * supplied (the common case — the API dispatch payload only carries the
 * session id + prompt), we just launch `cursor` with no args which focuses the
 * most recently used window.
 */
async function focusCursor(workspacePath?: string): Promise<void> {
  const arg = workspacePath ? ` --reuse-window "${workspacePath}"` : '';
  const result = await execAsync(`cursor${arg}`);
  if (!result.ok) {
    // `cursor` CLI not on PATH — non-fatal; the user may still have Cursor
    // open. We proceed and let SendKeys try to reach the existing window.
    console.warn('cursor CLI not available, trying existing window:', result.error);
  }
  await new Promise((r) => setTimeout(r, CURSOR_FOCUS_WAIT_MS));
}

/**
 * Execute a single dispatch: focus Cursor, push the prompt into the chat pane,
 * report the result back to the API.
 */
export async function executeCursorDispatch(
  apiBaseUrl: string,
  nodeId: string,
  nodeToken: string,
  dispatch: PendingDispatch,
): Promise<void> {
  try {
    await focusCursor(undefined);
    await sendPromptToCursor(dispatch.prompt);
    await completeDispatch(apiBaseUrl, nodeId, nodeToken, dispatch.id, {
      result: 'dispatched via clipboard + SendKeys',
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
 * Pull all pending dispatches for this node and execute them serially.
 * Called from the main sync loop.
 */
export async function processPendingDispatches(vaultRoot: string): Promise<void> {
  const config = readNodeConfig(vaultRoot);
  if (!config) return;

  let dispatches: PendingDispatch[];
  try {
    dispatches = await fetchPendingDispatches(config.apiBaseUrl, config.nodeId, config.nodeToken);
  } catch (err) {
    console.warn('Pending dispatch fetch failed:', err);
    return;
  }

  for (let i = 0; i < Math.min(dispatches.length, MAX_DISPATCHES_PER_CYCLE); i += 1) {
    await executeCursorDispatch(config.apiBaseUrl, config.nodeId, config.nodeToken, dispatches[i]);
  }
}
