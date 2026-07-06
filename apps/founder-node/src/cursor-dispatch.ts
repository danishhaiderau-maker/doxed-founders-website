import { clipboard, nativeImage } from 'electron';
import { exec } from 'node:child_process';
import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';
import { founderNodeAuthHeader } from '@dcf/founder-vault';
import {
  focusComposerInWorkspaceState,
  resolveCursorComposerContext,
  verifyComposerFocusedInWorkspaceState,
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
 *      input — no Ctrl+L (browser omnibox) or Ctrl+Shift+L (new chat tab).
 *   5. Reports the result back to the API.
 */

const MAX_DISPATCHES_PER_CYCLE = 1;
const CURSOR_FOCUS_WAIT_MS = 3000;
const COMPOSER_TAB_SETTLE_MS = 1400;
const SESSION_DISPATCH_COOLDOWN_MS = 12_000;
const DISPATCH_FINGERPRINT_COOLDOWN_MS = 60_000;
const CURSOR_FOCUS_ATTEMPTS = 8;
const CURSOR_FOCUS_ATTEMPT_MS = 400;
const SENDKEYS_STEP_DELAY_MS = 350;
const ENTER_SUBMIT_DELAY_MS = 500;
const CURSOR_FOREGROUND_STRICT_VERIFY_ROUNDS = 3;
const CURSOR_FOREGROUND_STRICT_VERIFY_DELAY_MS = 500;
const COMPOSER_REFOCUS_SETTLE_MS = 700;
const IMAGE_PASTE_SETTLE_MS = 900;

type ComposerFocusTarget = {
  composerId: string;
  workspaceStorageId: string;
  folderPath?: string;
  title?: string;
};

let dispatchCycleInFlight = false;
const lastDispatchBySession = new Map<string, number>();
const recentDispatchFingerprints = new Map<string, number>();
const executingDispatchIds = new Set<string>();

export type PendingDispatch = {
  id: string;
  sessionId: string;
  prompt: string;
  ideProvider: string;
};

type SendKeysStep = {
  keys: string;
  delayMs: number;
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

function execAsync(cmd: string): Promise<{ ok: boolean; error?: string; stdout?: string }> {
  return new Promise((resolve) => {
    exec(cmd, (err, stdout, stderr) => {
      if (err) {
        resolve({
          ok: false,
          error: err.message,
          stdout: `${stdout || ''}${stderr || ''}`.trim() || undefined,
        });
      } else {
        resolve({ ok: true, stdout: (stdout || '').trim() || undefined });
      }
    });
  });
}

function sleep(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
}

function dispatchFingerprint(sessionId: string, prompt: string): string {
  const { text } = parseDispatchPrompt(prompt);
  return `${sessionId}:${(text || prompt).trim()}`;
}

/**
 * Win32 helpers embedded once per script. Foreground is verified by **process
 * name** (Cursor.exe), not window title — browser tabs mentioning "Cursor"
 * must never pass the guard.
 */
const WIN32_CURSOR_FOCUS_TYPE = [
  'Add-Type @"',
  'using System;',
  'using System.Diagnostics;',
  'using System.Runtime.InteropServices;',
  'using System.Text;',
  'public class FnCursorFocus {',
  '  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);',
  '  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);',
  '  [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();',
  '  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint processId);',
  '  [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetWindowText(IntPtr hWnd, StringBuilder text, int count);',
  '  public static IntPtr FindCursorMainWindow() {',
  '    foreach (var p in Process.GetProcessesByName("Cursor")) {',
  '      if (p.MainWindowHandle != IntPtr.Zero) return p.MainWindowHandle;',
  '    }',
  '    return IntPtr.Zero;',
  '  }',
  '  public static string ForegroundProcessName() {',
  '    var fg = GetForegroundWindow();',
  '    if (fg == IntPtr.Zero) return "";',
  '    uint pid;',
  '    GetWindowThreadProcessId(fg, out pid);',
  '    try { return Process.GetProcessById((int)pid).ProcessName; } catch { return ""; }',
  '  }',
  '  public static string ForegroundTitle() {',
  '    var sb = new StringBuilder(512);',
  '    GetWindowText(GetForegroundWindow(), sb, 512);',
  '    return sb.ToString();',
  '  }',
  '  public static bool IsCursorForeground() {',
  '    if (!string.Equals(ForegroundProcessName(), "Cursor", StringComparison.OrdinalIgnoreCase)) return false;',
  '    var title = ForegroundTitle();',
  '    if (string.IsNullOrEmpty(title)) return false;',
  '    return title.IndexOf("Cursor", StringComparison.OrdinalIgnoreCase) >= 0;',
  '  }',
  '  public static bool ActivateCursor() {',
  '    var h = FindCursorMainWindow();',
  '    if (h == IntPtr.Zero) return false;',
  '    ShowWindow(h, 9);',
  '    return SetForegroundWindow(h);',
  '  }',
  '  public static bool EnsureCursorForeground(int attempts, int delayMs) {',
  '    for (int i = 0; i < attempts; i++) {',
  '      ActivateCursor();',
  '      System.Threading.Thread.Sleep(delayMs);',
  '      if (IsCursorForeground()) return true;',
  '    }',
  '    return IsCursorForeground();',
  '  }',
  '}',
  '"@',
].join('\n');

function escapePsSingleQuoted(value: string): string {
  return value.replace(/'/g, "''");
}

/**
 * Run SendKeys only inside a single PowerShell process that verifies
 * Cursor.exe owns the foreground immediately before every keystroke.
 * Never split focus-check and SendKeys across separate exec() calls.
 */
async function runVerifiedCursorSendKeys(
  steps: SendKeysStep[],
): Promise<{ ok: boolean; error?: string; foregroundTitle?: string }> {
  if (process.platform !== 'win32') {
    return { ok: false, error: 'Windows-only SendKeys' };
  }

  const stepScripts = steps
    .map(
      (step) =>
        [
          `if (-not [FnCursorFocus]::IsCursorForeground()) { Write-Output ('FAIL:' + [FnCursorFocus]::ForegroundProcessName() + '|' + [FnCursorFocus]::ForegroundTitle()); exit 1 }`,
          `[System.Windows.Forms.SendKeys]::SendWait('${escapePsSingleQuoted(step.keys)}');`,
          `Start-Sleep -Milliseconds ${step.delayMs};`,
        ].join('\n'),
    )
    .join('\n');

  const ps = [
    WIN32_CURSOR_FOCUS_TYPE,
    'Add-Type -AssemblyName System.Windows.Forms;',
    '$strictOk=$true;',
    `for ($v=0; $v -lt ${CURSOR_FOREGROUND_STRICT_VERIFY_ROUNDS}; $v++) {`,
    `  $ok=[FnCursorFocus]::EnsureCursorForeground(${CURSOR_FOCUS_ATTEMPTS},${CURSOR_FOCUS_ATTEMPT_MS});`,
    '  if (-not $ok -or -not [FnCursorFocus]::IsCursorForeground()) { $strictOk=$false; break }',
    `  Start-Sleep -Milliseconds ${CURSOR_FOREGROUND_STRICT_VERIFY_DELAY_MS};`,
    '}',
    'if (-not $strictOk) { Write-Output ("FAIL:" + [FnCursorFocus]::ForegroundProcessName() + "|" + [FnCursorFocus]::ForegroundTitle()); exit 1 }',
    stepScripts,
    'Write-Output ("OK:" + [FnCursorFocus]::ForegroundTitle())',
  ].join('\n');

  return readCursorFocusResult(ps);
}

function runPowerShellScriptFile(
  psBody: string,
  tmpPrefix: string,
): Promise<{ ok: boolean; foregroundTitle?: string; error?: string }> {
  const tmpScript = path.join(
    os.tmpdir(),
    `${tmpPrefix}-${Date.now()}-${Math.random().toString(36).slice(2)}.ps1`,
  );
  return new Promise((resolve) => {
    try {
      fs.writeFileSync(tmpScript, psBody, 'utf8');
    } catch (err) {
      resolve({
        ok: false,
        error: err instanceof Error ? err.message : String(err),
      });
      return;
    }

    exec(
      `powershell -NoProfile -ExecutionPolicy Bypass -File "${tmpScript.replace(/"/g, '""')}"`,
      (err, stdout, stderr) => {
        try {
          fs.unlinkSync(tmpScript);
        } catch {
          /* ignore */
        }

        const line = (stdout || '').trim().split(/\r?\n/).pop() || '';
        if (line.startsWith('OK:')) {
          resolve({ ok: true, foregroundTitle: line.slice(3) });
          return;
        }
        if (line.startsWith('FAIL:')) {
          const detail = line.slice(5);
          const [processName, title] = detail.split('|');
          resolve({
            ok: false,
            foregroundTitle: title || processName || undefined,
            error: `Cursor.exe not foreground (process="${processName ?? 'unknown'}", title="${title ?? ''}")`,
          });
          return;
        }

        const stderrLine = (stderr || '').trim().split(/\r?\n/).pop();
        resolve({
          ok: false,
          foregroundTitle: line || undefined,
          error: err?.message || stderrLine || line || 'Cursor focus verification failed',
        });
      },
    );
  });
}

async function readCursorFocusResult(
  psBody: string,
): Promise<{ ok: boolean; foregroundTitle?: string; error?: string }> {
  return runPowerShellScriptFile(psBody, 'fn-cursor-sendkeys');
}

/** Pre-flight activation check — abort dispatch before touching clipboard/SendKeys. */
async function ensureCursorWindowFocused(): Promise<{ ok: boolean; foregroundTitle?: string; error?: string }> {
  if (process.platform !== 'win32') return { ok: false, error: 'Windows-only' };
  return runVerifiedCursorSendKeys([]);
}

/**
 * Bring Cursor to the foreground without minimizing — enough for most tab switches.
 */
async function activateCursorWindow(): Promise<{ ok: boolean; error?: string }> {
  if (process.platform !== 'win32') return { ok: false, error: 'Windows-only' };
  const ps = [
    WIN32_CURSOR_FOCUS_TYPE,
    '$ok=[FnCursorFocus]::EnsureCursorForeground(6,300);',
    'if (-not $ok -or -not [FnCursorFocus]::IsCursorForeground()) {',
    '  Write-Output ("FAIL:" + [FnCursorFocus]::ForegroundProcessName() + "|" + [FnCursorFocus]::ForegroundTitle()); exit 1',
    '}',
    'Write-Output ("OK:" + [FnCursorFocus]::ForegroundTitle())',
  ].join('\n');
  const result = await readCursorFocusResult(ps);
  return result.ok ? { ok: true } : { ok: false, error: result.error ?? 'Cursor activation failed' };
}

/**
 * Last resort: minimize then restore so an already-open workspace reloads
 * composer.composerData from state.vscdb. Causes visible flicker — only use when
 * workspace-state verification still fails after reuse-window + activate.
 */
async function nudgeCursorToReloadWorkspaceState(): Promise<{ ok: boolean; error?: string }> {
  if (process.platform !== 'win32') return { ok: false, error: 'Windows-only' };
  const ps = [
    WIN32_CURSOR_FOCUS_TYPE,
    '$h=[FnCursorFocus]::FindCursorMainWindow();',
    'if ($h -eq [IntPtr]::Zero) { Write-Output "FAIL:no-window"; exit 1 }',
    '[FnCursorFocus]::ShowWindow($h, 6);',
    'Start-Sleep -Milliseconds 450;',
    '$ok=[FnCursorFocus]::EnsureCursorForeground(8,350);',
    'if (-not $ok -or -not [FnCursorFocus]::IsCursorForeground()) {',
    '  Write-Output ("FAIL:" + [FnCursorFocus]::ForegroundProcessName() + "|" + [FnCursorFocus]::ForegroundTitle()); exit 1',
    '}',
    'Write-Output ("OK:" + [FnCursorFocus]::ForegroundTitle())',
  ].join('\n');
  const result = await readCursorFocusResult(ps);
  return result.ok ? { ok: true } : { ok: false, error: result.error ?? 'Cursor window nudge failed' };
}

function writeComposerFocusState(target: ComposerFocusTarget): boolean {
  return focusComposerInWorkspaceState(target.workspaceStorageId, target.composerId);
}

function assertComposerFocusState(target: ComposerFocusTarget): void {
  if (!verifyComposerFocusedInWorkspaceState(target.workspaceStorageId, target.composerId)) {
    const label = target.title ? `"${target.title}"` : target.composerId.slice(0, 8);
    throw new Error(
      `Target composer ${label} is not focused in workspace state — refusing SendKeys (wrong tab would receive paste)`,
    );
  }
}

type RefocusMode = 'full' | 'light';

/**
 * Re-assert composer tab focus before paste. Full mode reopens workspace + steals
 * foreground once per dispatch; light mode only checks workspace state (SendKeys
 * batches already verify Cursor.exe foreground).
 */
async function refocusComposerTarget(
  target: ComposerFocusTarget,
  mode: RefocusMode = 'full',
): Promise<void> {
  if (mode === 'light') {
    if (verifyComposerFocusedInWorkspaceState(target.workspaceStorageId, target.composerId)) {
      return;
    }
    mode = 'full';
  }

  if (!writeComposerFocusState(target)) {
    throw new Error(
      `Could not re-focus composer tab ${target.composerId.slice(0, 8)} in workspace state`,
    );
  }
  if (target.folderPath) {
    const result = await execAsync(`cursor --reuse-window "${target.folderPath}"`);
    if (!result.ok) {
      console.warn('cursor CLI refocus failed:', result.error);
    }
  }
  await sleep(COMPOSER_REFOCUS_SETTLE_MS);
  const foreground = await ensureCursorWindowFocused();
  if (!foreground.ok) {
    throw new Error(
      foreground.error ?? 'Cursor.exe is not foreground — refusing paste into unknown composer',
    );
  }
  assertComposerFocusState(target);
}

const ATTACH_IMAGE_RE =
  /<!--founder-attach:image:([^\n]+)\n(data:image\/[a-zA-Z0-9.+-]+;base64,[A-Za-z0-9+/=\s]+)\n-->/g;

const FOUNDER_OS_DISPATCH_PREFIX = '[Founder OS → Cursor]';

/** Keep paste attribution aligned with @dcf/utils — local copy avoids Electron tsc resolution quirks. */
function withFounderOsDispatchAttribution(prompt: string): string {
  const trimmed = prompt.trim();
  if (!trimmed) return trimmed;
  if (trimmed.includes(FOUNDER_OS_DISPATCH_PREFIX)) return trimmed;
  const attachmentBlocks: string[] = [];
  const text = trimmed
    .replace(ATTACH_IMAGE_RE, (block) => {
      attachmentBlocks.push(block);
      return '';
    })
    .trim();
  const prefixedText = text
    ? `${FOUNDER_OS_DISPATCH_PREFIX}\n\n${text}`
    : FOUNDER_OS_DISPATCH_PREFIX;
  return attachmentBlocks.length > 0
    ? `${prefixedText}${attachmentBlocks.join('')}`
    : prefixedText;
}

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
 * Write a data-URL image to the OS clipboard so Cursor can receive it via Ctrl+V.
 * Uses Electron nativeImage — PowerShell SetImage scripts do not emit the OK:
 * marker that runPowerShellScriptFile requires, so they always looked like failures.
 */
async function pasteImageDataUrl(dataUrl: string): Promise<boolean> {
  if (process.platform !== 'win32') return false;
  const m = dataUrl.match(/^data:(image\/[a-zA-Z0-9.+-]+);base64,(.+)$/);
  if (!m) return false;
  const b64 = m[2]!.replace(/\s+/g, '');
  try {
    const bytes = Buffer.from(b64, 'base64');
    if (bytes.length < 32 || bytes.length > 8 * 1024 * 1024) return false;
    const img = nativeImage.createFromBuffer(bytes);
    if (img.isEmpty()) return false;
    clipboard.writeImage(img);
    return true;
  } catch {
    return false;
  }
}

type SendPromptResult = {
  pasted: boolean;
  submitted: boolean;
  clipboardReady: boolean;
  warning?: string;
};

/**
 * Paste into the composer that focusComposerInWorkspaceState + cursor CLI
 * already selected. No Ctrl+L — that hijacks browser address bars during demos.
 */
async function sendPromptToFocusedComposer(
  prompt: string,
  focusTarget: ComposerFocusTarget,
): Promise<SendPromptResult> {
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

  const hasImages = images.length > 0;
  await sleep(hasImages ? COMPOSER_TAB_SETTLE_MS + IMAGE_PASTE_SETTLE_MS : COMPOSER_TAB_SETTLE_MS);

  const bodyText = withFounderOsDispatchAttribution(
    text ||
      (images.length > 0
        ? `[Attachments: ${images.map((i) => i.name).join(', ')}]`
        : prompt),
  );

  await refocusComposerTarget(focusTarget, 'full');

  for (const img of images.slice(0, 3)) {
    await refocusComposerTarget(focusTarget, 'light');
    const ok = await pasteImageDataUrl(img.dataUrl);
    if (!ok) {
      throw new Error(`Failed to load image "${img.name}" onto clipboard for Cursor paste`);
    }
    const imgResult = await runVerifiedCursorSendKeys([
      { keys: '^v', delayMs: SENDKEYS_STEP_DELAY_MS },
    ]);
    if (!imgResult.ok) {
      return {
        pasted: false,
        submitted: false,
        clipboardReady: true,
        warning: `SendKeys image paste failed: ${imgResult.error ?? 'unknown error'}`,
      };
    }
    await sleep(IMAGE_PASTE_SETTLE_MS);
  }

  await refocusComposerTarget(focusTarget, 'light');
  try {
    clipboard.writeText(bodyText);
  } catch (err) {
    throw new Error(`clipboard write failed: ${err instanceof Error ? err.message : String(err)}`);
  }

  await sleep(120);

  const pasteResult = await runVerifiedCursorSendKeys([
    { keys: '^v', delayMs: SENDKEYS_STEP_DELAY_MS },
  ]);
  if (!pasteResult.ok) {
    return {
      pasted: false,
      submitted: false,
      clipboardReady: true,
      warning: `SendKeys paste failed: ${pasteResult.error ?? 'unknown error'}`,
    };
  }

  await sleep(ENTER_SUBMIT_DELAY_MS);

  const submitResult = await runVerifiedCursorSendKeys([
    { keys: '{ENTER}', delayMs: SENDKEYS_STEP_DELAY_MS },
  ]);
  if (!submitResult.ok) {
    return {
      pasted: true,
      submitted: false,
      clipboardReady: true,
      warning: `SendKeys submit refused — Cursor lost focus before Enter (${submitResult.error ?? 'unknown'})`,
    };
  }

  return { pasted: true, submitted: true, clipboardReady: true };
}

/**
 * Focus the workspace window and pre-select the target composer tab via
 * workspace state.vscdb before Cursor reads it on activation.
 */
async function focusCursorComposer(
  target: ComposerFocusTarget,
): Promise<{ focusedTab: boolean; openedWorkspace: boolean; reloadedUi: boolean }> {
  let focusedTab = writeComposerFocusState(target);
  if (!focusedTab) {
    return { focusedTab: false, openedWorkspace: false, reloadedUi: false };
  }

  let openedWorkspace = false;
  if (target.folderPath) {
    const result = await execAsync(`cursor --reuse-window "${target.folderPath}"`);
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

  focusedTab = writeComposerFocusState(target) && focusedTab;

  let reloadedUi = false;
  if (!verifyComposerFocusedInWorkspaceState(target.workspaceStorageId, target.composerId)) {
    writeComposerFocusState(target);
    if (target.folderPath) {
      await execAsync(`cursor --reuse-window "${target.folderPath}"`);
      await sleep(COMPOSER_TAB_SETTLE_MS);
    }
    await activateCursorWindow();
    if (!verifyComposerFocusedInWorkspaceState(target.workspaceStorageId, target.composerId)) {
      const nudge = await nudgeCursorToReloadWorkspaceState();
      reloadedUi = nudge.ok;
      if (nudge.ok) {
        focusedTab = writeComposerFocusState(target) && focusedTab;
      }
    }
  }

  const foreground = await ensureCursorWindowFocused();
  if (!foreground.ok) {
    return { focusedTab: false, openedWorkspace, reloadedUi };
  }

  try {
    assertComposerFocusState(target);
  } catch {
    return { focusedTab: false, openedWorkspace, reloadedUi };
  }

  return { focusedTab: true, openedWorkspace, reloadedUi };
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
  if (executingDispatchIds.has(dispatch.id)) {
    await completeDispatch(apiBaseUrl, nodeId, nodeToken, dispatch.id, {
      result: 'deduplicated (dispatch already executing)',
    });
    return;
  }

  const fingerprint = dispatchFingerprint(dispatch.sessionId, dispatch.prompt);
  const lastFingerprintAt = recentDispatchFingerprints.get(fingerprint) ?? 0;
  if (Date.now() - lastFingerprintAt < DISPATCH_FINGERPRINT_COOLDOWN_MS) {
    await completeDispatch(apiBaseUrl, nodeId, nodeToken, dispatch.id, {
      result: 'deduplicated (identical prompt recently dispatched)',
    });
    return;
  }

  executingDispatchIds.add(dispatch.id);
  recentDispatchFingerprints.set(fingerprint, Date.now());

  try {
    const composerId = dispatch.sessionId;
    const ctx = resolveCursorComposerContext(composerId);
    if (!ctx) {
      throw new Error(
        `Cursor composer ${composerId.slice(0, 8)} not found locally — sync Cursor sessions in Founder Node and retry`,
      );
    }
    if (!ctx.workspaceStorageId) {
      throw new Error(
        `Composer "${ctx.title ?? composerId.slice(0, 8)}" has no workspace binding — cannot target Cursor tab`,
      );
    }

    const focusTarget: ComposerFocusTarget = {
      composerId,
      workspaceStorageId: ctx.workspaceStorageId,
      folderPath: ctx.folderPath,
      title: ctx.title,
    };

    const { focusedTab, openedWorkspace, reloadedUi } = await focusCursorComposer(focusTarget);

    if (!focusedTab) {
      throw new Error(
        `Could not focus composer tab "${ctx.title ?? composerId.slice(0, 8)}" — refusing SendKeys (would paste into wrong chat)`,
      );
    }
    if (ctx.folderPath && !openedWorkspace) {
      throw new Error(
        `Could not open Cursor workspace for "${ctx.title ?? composerId.slice(0, 8)}" — refusing SendKeys`,
      );
    }

    const sendResult = await sendPromptToFocusedComposer(dispatch.prompt, focusTarget);

    const bits = [
      `composer=${composerId.slice(0, 8)}`,
      'tab-focused',
      openedWorkspace ? 'workspace-opened' : 'workspace-focus-best-effort',
      reloadedUi ? 'ui-reloaded' : 'ui-reload-skipped',
    ];

    if (!sendResult.submitted) {
      const fallback = sendResult.clipboardReady
        ? ' — prompt copied to clipboard; focus Cursor composer and press Ctrl+V'
        : '';
      throw new Error((sendResult.warning ?? 'Cursor paste did not complete') + fallback);
    }

    await completeDispatch(apiBaseUrl, nodeId, nodeToken, dispatch.id, {
      result: `dispatched (${bits.join(', ')})`,
    });
  } catch (err) {
    recentDispatchFingerprints.delete(fingerprint);
    const message = err instanceof Error ? err.message : String(err);
    console.error(`Cursor dispatch ${dispatch.id} failed:`, message);
    await completeDispatch(apiBaseUrl, nodeId, nodeToken, dispatch.id, { error: message }).catch(
      (e) => console.error('Failed to report dispatch error:', e),
    );
  } finally {
    executingDispatchIds.delete(dispatch.id);
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

      const fingerprint = dispatchFingerprint(candidate.sessionId, candidate.prompt);
      const lastFingerprintAt = recentDispatchFingerprints.get(fingerprint) ?? 0;
      if (Date.now() - lastFingerprintAt < DISPATCH_FINGERPRINT_COOLDOWN_MS) {
        await completeDispatch(config.apiBaseUrl, config.nodeId, config.nodeToken, candidate.id, {
          result: 'deduplicated (identical prompt recently dispatched)',
        }).catch(() => undefined);
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
