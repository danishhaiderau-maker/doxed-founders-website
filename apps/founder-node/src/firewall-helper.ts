import { app, dialog, shell } from 'electron';
import { spawn } from 'node:child_process';
import path from 'node:path';

const RULE_BASE = 'Founder Node (Doxxed Crypto)';
const PROMPT_COOLDOWN_MS = 24 * 60 * 60 * 1000;

let lastFirewallPromptAt = 0;

export function isWindows(): boolean {
  return process.platform === 'win32';
}

export function getAppExecutablePath(): string {
  return path.normalize(process.execPath);
}

/** True when repeated sync failures look like network/firewall, not auth or HTTP errors.
 *  Only connection-level errors (ECONNREFUSED, ETIMEDOUT, ENOTFOUND, fetch TypeError)
 *  should trigger the firewall prompt — NOT HTTP 4xx/5xx responses, which mean the
 *  server IS reachable. */
export function shouldOfferFirewallHelp(
  consecutiveTransientFailures: number,
  lastError?: string | null,
): boolean {
  if (!isWindows() || !app.isPackaged || consecutiveTransientFailures < 3) return false;
  if (!lastError) return true; // unknown error, allow prompt
  // HTTP responses (4xx/5xx) mean the server is reachable — NOT a firewall issue
  if (/\b[45]\d\d\b|request entity too large|statusCode|status code/i.test(lastError)) return false;
  // Connection-level errors — these ARE firewall candidates
  return /ECONNREFUSED|ETIMEDOUT|ENOTFOUND|ECONNRESET|EHOSTUNREACH|fetch failed|TypeError|network|socket/i.test(lastError);
}

export function canShowFirewallPrompt(): boolean {
  return Date.now() - lastFirewallPromptAt >= PROMPT_COOLDOWN_MS;
}

export function markFirewallPromptShown(): void {
  lastFirewallPromptAt = Date.now();
}

export function resetFirewallPromptCooldown(): void {
  lastFirewallPromptAt = 0;
}

function buildFirewallNetshCommands(exe: string): string {
  const safeExe = exe.replace(/"/g, '`"');
  return [
    `netsh advfirewall firewall add rule name="${RULE_BASE} Out" dir=out action=allow program="${safeExe}" enable=yes profile=any`,
    `netsh advfirewall firewall add rule name="${RULE_BASE} In" dir=in action=allow program="${safeExe}" enable=yes profile=any`,
  ].join('; ');
}

/** Run netsh without elevation (works if user is already admin). */
function runNetshDirect(commands: string): Promise<boolean> {
  return new Promise((resolve) => {
    const child = spawn(
      'cmd.exe',
      ['/d', '/s', '/c', commands],
      { windowsHide: true, stdio: 'ignore' },
    );
    child.on('error', () => resolve(false));
    child.on('exit', (code) => resolve(code === 0));
  });
}

/** Ask Windows to elevate and add firewall rules for this executable. */
function runNetshElevated(commands: string): void {
  const inner = commands.replace(/"/g, '\\"');
  const ps = `Start-Process cmd.exe -Verb RunAs -Wait -ArgumentList '/d /s /c \\"${inner}\\"'`;
  spawn(
    'powershell.exe',
    ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command', ps],
    { detached: true, stdio: 'ignore', windowsHide: true },
  ).unref();
}

/** Check whether the Founder Node firewall rules already exist. Avoids re-triggering
 * a UAC elevation prompt on every app launch when the rule was already added. */
function firewallRuleExists(): Promise<boolean> {
  return new Promise((resolve) => {
    const child = spawn(
      'cmd.exe',
      ['/d', '/s', '/c', `netsh advfirewall firewall show rule name="${RULE_BASE} Out"`],
      { windowsHide: true, stdio: 'ignore' },
    );
    let done = false;
    const finish = (ok: boolean) => {
      if (done) return;
      done = true;
      resolve(ok);
    };
    child.on('error', () => finish(false));
    child.on('exit', (code) => finish(code === 0));
    // Safety timeout — netsh should return well under 3s.
    setTimeout(() => finish(false), 3000);
  });
}

export async function tryAddWindowsFirewallRules(): Promise<{ ok: boolean; detail: string }> {
  if (!isWindows()) {
    return { ok: false, detail: 'Firewall helper is only available on Windows.' };
  }
  // Skip the proactive add (and the UAC prompt it can trigger) when the rule is
  // already present from a previous launch. The user-initiated tray action still
  // force-adds if the rule was removed manually.
  const already = await firewallRuleExists().catch(() => false);
  if (already) {
    return { ok: true, detail: 'Founder Node is already allowed through Windows Firewall.' };
  }
  const exe = getAppExecutablePath();
  const commands = buildFirewallNetshCommands(exe);
  const directOk = await runNetshDirect(commands);
  if (directOk) {
    return { ok: true, detail: 'Founder Node is allowed through Windows Firewall.' };
  }
  runNetshElevated(commands);
  return {
    ok: true,
    detail:
      'If Windows asks for permission, choose Yes to allow Founder Node. Then use tray → Sync now.',
  };
}

export async function openWindowsFirewallSettings(): Promise<void> {
  await shell.openExternal('ms-settings:windowsdefender-firewall');
}

export async function promptFirewallBlocked(options: {
  consecutiveFailures?: number;
  lastError?: string | null;
  onRetrySync?: () => void;
}): Promise<void> {
  const failures = options.consecutiveFailures ?? 2;
  if (!shouldOfferFirewallHelp(failures, options.lastError) || !canShowFirewallPrompt()) return;

  markFirewallPromptShown();

  const detail = [
    'Founder Node cannot reach Founder OS. Windows Firewall often blocks new apps on first run.',
    '',
    options.lastError ? `Last error: ${options.lastError.slice(0, 120)}` : '',
    '',
    'Choose "Allow Founder Node" — we will add a firewall rule for this app (you may see one UAC prompt).',
  ]
    .filter(Boolean)
    .join('\n');

  const { response } = await dialog.showMessageBox({
    type: 'warning',
    title: 'Connection blocked?',
    message: 'Allow Founder Node through Windows Firewall',
    detail,
    buttons: ['Allow Founder Node', 'Open firewall settings', 'Try sync again', 'Later'],
    defaultId: 0,
    cancelId: 3,
  });

  if (response === 0) {
    const result = await tryAddWindowsFirewallRules();
    // Only show the info dialog if we actually had to add a rule (not if it already existed)
    if (!result.ok || !result.detail.includes('already')) {
      await dialog.showMessageBox({
        type: 'info',
        title: 'Firewall',
        message: result.detail,
        buttons: ['OK'],
      });
    }
    options.onRetrySync?.();
  } else if (response === 1) {
    await openWindowsFirewallSettings();
  } else if (response === 2) {
    options.onRetrySync?.();
  }
}
