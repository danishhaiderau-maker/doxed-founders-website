import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawn, spawnSync } from 'node:child_process';
import type { FounderCloudMode, FounderNodeConfig } from '@dcf/founder-vault';
import { FOUNDER_CLOUD_DEFAULT_URLS } from '@dcf/utils';

export function readFounderCloudStatusFile(): {
  running?: boolean;
  webUrl?: string;
  apiUrl?: string;
  lastError?: string;
} | null {
  const file = path.join(os.homedir(), 'FounderVault', 'founder-cloud-status.json');
  if (!fs.existsSync(file)) return null;
  try {
    return JSON.parse(fs.readFileSync(file, 'utf8')) as Record<string, unknown>;
  } catch {
    return null;
  }
}

export function resolveFounderCloudRepo(config: FounderNodeConfig | null): string | null {
  const fromConfig = config?.founderCloud?.repoPath?.trim();
  if (fromConfig && fs.existsSync(path.join(fromConfig, 'package.json'))) return fromConfig;
  const fromEnv = process.env.FOUNDER_CLOUD_REPO?.trim();
  if (fromEnv && fs.existsSync(path.join(fromEnv, 'package.json'))) return fromEnv;
  return null;
}

export function getFounderCloudMode(config: FounderNodeConfig | null): FounderCloudMode {
  const status = readFounderCloudStatusFile();
  const enabled = config?.founderCloud?.enabled ?? false;
  return {
    enabled,
    repoPath: resolveFounderCloudRepo(config) ?? config?.founderCloud?.repoPath,
    stackRunning: Boolean(status?.running ?? config?.founderCloud?.stackRunning),
    webUrl: status?.webUrl ?? config?.founderCloud?.webUrl ?? FOUNDER_CLOUD_DEFAULT_URLS.web,
    apiUrl: status?.apiUrl ?? config?.founderCloud?.apiUrl ?? FOUNDER_CLOUD_DEFAULT_URLS.api,
    lastStartedAt: config?.founderCloud?.lastStartedAt,
    lastError: status?.lastError ?? config?.founderCloud?.lastError,
  };
}

export function patchFounderCloudConfig(
  config: FounderNodeConfig,
  patch: Partial<FounderCloudMode>,
): FounderNodeConfig {
  return {
    ...config,
    founderCloud: {
      enabled: patch.enabled ?? config.founderCloud?.enabled ?? false,
      repoPath: patch.repoPath ?? config.founderCloud?.repoPath,
      stackRunning: patch.stackRunning ?? config.founderCloud?.stackRunning,
      webUrl: patch.webUrl ?? config.founderCloud?.webUrl,
      apiUrl: patch.apiUrl ?? config.founderCloud?.apiUrl,
      lastStartedAt: patch.lastStartedAt ?? config.founderCloud?.lastStartedAt,
      lastError: patch.lastError ?? config.founderCloud?.lastError,
    },
  };
}

export function runFounderLocalAsync(
  repoPath: string,
  action: 'start' | 'stop' | 'bootstrap' | 'status',
): Promise<{ ok: boolean; detail: string }> {
  const script = path.join(repoPath, 'scripts', 'founder-local.mjs');
  if (!fs.existsSync(script)) {
    return Promise.resolve({
      ok: false,
      detail: 'founder-local.mjs not found — clone Founder OS repo and set FOUNDER_CLOUD_REPO',
    });
  }

  return new Promise((resolve) => {
    const child = spawn(process.execPath, [script, action], {
      cwd: repoPath,
      env: { ...process.env, FOUNDER_CLOUD_REPO: repoPath },
      stdio: action === 'status' ? 'pipe' : 'ignore',
    });
    let out = '';
    child.stdout?.on('data', (d) => {
      out += String(d);
    });
    child.on('close', (code) => {
      if (action === 'status') {
        resolve({
          ok: code === 0,
          detail: out.trim() || (code === 0 ? 'Stack running' : 'Stack stopped'),
        });
        return;
      }
      resolve({
        ok: code === 0,
        detail: code === 0 ? `${action} OK` : `${action} failed (exit ${code})`,
      });
    });
    child.on('error', (err) => {
      resolve({ ok: false, detail: err.message });
    });
  });
}

export function runFounderLocalSync(
  repoPath: string,
  action: 'start' | 'stop' | 'bootstrap',
): { ok: boolean; detail: string } {
  const script = path.join(repoPath, 'scripts', 'founder-local.mjs');
  const r = spawnSync(process.execPath, [script, action], {
    cwd: repoPath,
    env: { ...process.env, FOUNDER_CLOUD_REPO: repoPath },
    encoding: 'utf8',
  });
  return {
    ok: r.status === 0,
    detail: r.status === 0 ? `${action} OK` : (r.stderr || r.stdout || `${action} failed`).slice(0, 200),
  };
}
