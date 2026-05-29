'use client';

import Link from 'next/link';
import { useCallback, useEffect, useState } from 'react';
import {
  BuilderSettings,
  connectAiProvider,
  connectGitHubToken,
  disconnectAiProvider,
  disconnectGitHubToken,
  fetchBuilderSettings,
  updateBuilderSettings,
} from '@/lib/api';

type BuilderSettingsPanelProps = {
  accessToken: string;
};

export function BuilderSettingsPanel({ accessToken }: BuilderSettingsPanelProps) {
  const [settings, setSettings] = useState<BuilderSettings | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [connecting, setConnecting] = useState<string | null>(null);
  const [apiKeyInput, setApiKeyInput] = useState<Record<string, string>>({});
  const [githubToken, setGithubToken] = useState('');

  const load = useCallback(async () => {
    try {
      setSettings(await fetchBuilderSettings(accessToken));
      setErr(null);
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Failed to load settings');
    }
  }, [accessToken]);

  useEffect(() => {
    load();
  }, [load]);

  async function saveSettings(patch: {
    defaultProvider?: string;
    preferredModel?: string;
    autoCreateGitHubIssues?: boolean;
    autoPublishOnEvent?: boolean;
    currentGoalFocus?: string;
  }) {
    setErr(null);
    try {
      await updateBuilderSettings(patch, accessToken);
      setMsg('Settings saved');
      load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Save failed');
    }
  }

  async function handleConnectProvider(provider: string) {
    const key = apiKeyInput[provider]?.trim();
    if (!key) {
      setErr('Enter an API key first');
      return;
    }
    setConnecting(provider);
    setErr(null);
    try {
      const result = await connectAiProvider(provider, key, accessToken);
      setMsg(`${result.accountName} connected — AI costs bill to your account`);
      setApiKeyInput({ ...apiKeyInput, [provider]: '' });
      load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Connect failed');
    } finally {
      setConnecting(null);
    }
  }

  async function handleDisconnect(provider: string) {
    try {
      await disconnectAiProvider(provider, accessToken);
      setMsg('Provider disconnected');
      load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Disconnect failed');
    }
  }

  async function handleGitHubToken() {
    if (!githubToken.trim()) return;
    try {
      await connectGitHubToken(githubToken.trim(), accessToken);
      setMsg('GitHub token saved (encrypted) — can create issues & read PRs');
      setGithubToken('');
      load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'GitHub token invalid');
    }
  }

  if (!settings) {
    return <p className="text-sm text-zinc-500">Loading builder settings…</p>;
  }

  const aiProviders = settings.providers.filter((p) => p.key !== 'RULE_BASED');

  return (
    <div className="space-y-8">
      <section className="rounded-2xl border border-violet-500/30 bg-violet-950/10 p-6">
        <h2 className="text-lg font-semibold text-white">Default builder</h2>
        <p className="mt-1 text-sm text-zinc-500">
          Founder OS orchestrates — your AI provider bills you directly. Not tied to Cursor.
        </p>
        <div className="mt-4 grid gap-4 sm:grid-cols-2">
          <label className="block text-sm">
            <span className="text-zinc-400">Default provider</span>
            <select
              value={settings.defaultProvider}
              onChange={(e) => saveSettings({ defaultProvider: e.target.value })}
              className="mt-1 w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-white"
            >
              {settings.providers.map((p) => (
                <option key={p.key} value={p.key}>
                  {p.label}
                </option>
              ))}
            </select>
          </label>
          <label className="block text-sm">
            <span className="text-zinc-400">Preferred model (optional)</span>
            <input
              defaultValue={settings.preferredModel ?? ''}
              onBlur={(e) => saveSettings({ preferredModel: e.target.value || undefined })}
              placeholder="gpt-4o-mini, claude-3-5-haiku-latest…"
              className="mt-1 w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-white"
            />
          </label>
        </div>
        <label className="mt-4 flex items-center gap-2 text-sm text-zinc-300">
          <input
            type="checkbox"
            checked={settings.autoPublishOnEvent}
            onChange={(e) => saveSettings({ autoPublishOnEvent: e.target.checked })}
          />
          Auto-publish when deploy/commit events create suggested updates
        </label>
        <label className="mt-2 flex items-center gap-2 text-sm text-zinc-300">
          <input
            type="checkbox"
            checked={settings.autoCreateGitHubIssues}
            onChange={(e) => saveSettings({ autoCreateGitHubIssues: e.target.checked })}
          />
          Auto-create GitHub issues when Quick Build captures ideas
        </label>
        <label className="mt-4 block text-sm">
          <span className="text-zinc-400">Current goal focus</span>
          <input
            defaultValue={settings.currentGoalFocus ?? ''}
            onBlur={(e) => saveSettings({ currentGoalFocus: e.target.value.trim() || undefined })}
            placeholder="e.g. Referral System — shown on welcome-back briefing"
            className="mt-1 w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-white"
          />
        </label>
      </section>

      <section className="rounded-2xl border border-zinc-800 bg-zinc-900/40 p-6">
        <h2 className="text-lg font-semibold text-white">AI providers (your keys)</h2>
        <p className="mt-1 text-sm text-zinc-500">Encrypted at rest (AES-256-GCM). Founder OS never pays AI costs.</p>
        <div className="mt-4 space-y-4">
          {aiProviders.map((p) => (
            <div key={p.key} className="rounded-xl border border-zinc-800 p-4">
              <div className="flex flex-wrap items-start justify-between gap-2">
                <div>
                  <p className="font-medium text-white">{p.label}</p>
                  <p className="text-xs text-zinc-500">{p.billTip}</p>
                </div>
                {p.connected && (
                  <span className="rounded-full bg-emerald-500/20 px-2 py-0.5 text-[10px] font-semibold text-emerald-300">
                    Connected
                  </span>
                )}
              </div>
              {p.needsApiKey && (
                <div className="mt-3 flex flex-col gap-2 sm:flex-row">
                  <input
                    type="password"
                    value={apiKeyInput[p.credentialProvider ?? ''] ?? ''}
                    onChange={(e) =>
                      setApiKeyInput({ ...apiKeyInput, [p.credentialProvider ?? '']: e.target.value })
                    }
                    placeholder="Paste API key — never stored in plaintext"
                    className="flex-1 rounded-lg border border-zinc-700 bg-black px-3 py-2 text-sm"
                  />
                  <button
                    type="button"
                    disabled={connecting === p.credentialProvider}
                    onClick={() => p.credentialProvider && handleConnectProvider(p.credentialProvider)}
                    className="rounded-lg bg-violet-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
                  >
                    {p.connected ? 'Update key' : 'Connect'}
                  </button>
                  {p.connected && p.credentialProvider && (
                    <button
                      type="button"
                      onClick={() => handleDisconnect(p.credentialProvider!)}
                      className="rounded-lg border border-zinc-600 px-4 py-2 text-sm text-zinc-400"
                    >
                      Disconnect
                    </button>
                  )}
                </div>
              )}
              {p.key === 'CURSOR' && (
                <p className="mt-2 text-xs text-indigo-300">
                  No remote API — use Copy for builder in Founder Copilot when at your desk.
                </p>
              )}
            </div>
          ))}
        </div>
      </section>

      <section className="rounded-2xl border border-zinc-800 bg-zinc-900/40 p-6">
        <h2 className="text-lg font-semibold text-white">GitHub personal access token</h2>
        <p className="mt-1 text-sm text-zinc-500">
          Required for creating issues and listing PRs on private repos. Connect repo in{' '}
          <Link href="/founder-den?tab=build" className="text-emerald-400 underline">
            Founder Copilot
          </Link>{' '}
          first.
        </p>
        <div className="mt-4 flex flex-col gap-2 sm:flex-row">
          <input
            type="password"
            value={githubToken}
            onChange={(e) => setGithubToken(e.target.value)}
            placeholder="ghp_… (repo scope)"
            className="flex-1 rounded-lg border border-zinc-700 bg-black px-3 py-2 text-sm"
          />
          <button
            type="button"
            onClick={handleGitHubToken}
            className="rounded-lg bg-zinc-100 px-4 py-2 text-sm font-medium text-black"
          >
            {settings.githubTokenConnected ? 'Update token' : 'Save token'}
          </button>
          {settings.githubTokenConnected && (
            <button
              type="button"
              onClick={() => disconnectGitHubToken(accessToken).then(load)}
              className="rounded-lg border border-zinc-600 px-4 py-2 text-sm text-zinc-400"
            >
              Remove
            </button>
          )}
        </div>
      </section>

      {msg && (
        <p className="rounded-lg border border-emerald-500/30 bg-emerald-950/20 px-4 py-2 text-sm text-emerald-200">
          {msg}
        </p>
      )}
      {err && (
        <p className="rounded-lg border border-red-500/30 bg-red-950/20 px-4 py-2 text-sm text-red-300">{err}</p>
      )}
    </div>
  );
}
