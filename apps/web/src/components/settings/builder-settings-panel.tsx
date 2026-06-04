'use client';

import { useCallback, useEffect, useState } from 'react';
import {
  BuilderSettings,
  connectAiProvider,
  connectCursorCloud,
  connectGitHubToken,
  connectOllamaDirect,
  connectPhalaDirect,
  connectOpenHands,
  disconnectAiProvider,
  disconnectGitHubToken,
  fetchBuilderSettings,
  updateBuilderSettings,
} from '@/lib/api';
import { FounderNodeHubPanel } from '@/components/settings/founder-node-hub-panel';
import { GitHubPatConnectGuide } from '@/components/settings/github-pat-connect-guide';
import { HybridControlPlane } from '@/components/hybrid-control-plane';
import type { MemoryStorageModeKey } from '@dcf/utils';

type BuilderSettingsPanelProps = {
  accessToken: string;
};

export function BuilderSettingsPanel({ accessToken }: BuilderSettingsPanelProps) {
  const [settings, setSettings] = useState<BuilderSettings | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [connecting, setConnecting] = useState<string | null>(null);
  const [apiKeyInput, setApiKeyInput] = useState<Record<string, string>>({});
  const [openhandsUrl, setOpenhandsUrl] = useState('');
  const [openhandsKey, setOpenhandsKey] = useState('');
  const [cursorKey, setCursorKey] = useState('');
  const [githubToken, setGithubToken] = useState('');
  const [ollamaUrl, setOllamaUrl] = useState('http://127.0.0.1:11434');
  const [ollamaModel, setOllamaModel] = useState('llama3.2');
  const [phalaKey, setPhalaKey] = useState('');
  const [phalaUrl, setPhalaUrl] = useState('https://api.redpill.ai/v1');
  const [phalaModel, setPhalaModel] = useState('phala/deepseek-chat-v3-0324');

  const load = useCallback(async () => {
    try {
      const data = await fetchBuilderSettings(accessToken);
      setSettings(data);
      if (data.openHandsBaseUrl) setOpenhandsUrl(data.openHandsBaseUrl);
      if (data.phalaPrivateAi?.inferenceUrl) setPhalaUrl(data.phalaPrivateAi.inferenceUrl);
      if (data.phalaPrivateAi?.model) setPhalaModel(data.phalaPrivateAi.model);
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
    autopilotEnabled?: boolean;
    autopilotRedeployHosts?: boolean;
    controlPlaneMode?: string;
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
      const brain = (result as { brainActivated?: { label: string } }).brainActivated;
      setMsg(
        brain
          ? `${result.accountName} connected — ${brain.label} is now your Copilot brain`
          : `${result.accountName} connected — AI costs bill to your account`,
      );
      setApiKeyInput({ ...apiKeyInput, [provider]: '' });
      load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Connect failed');
    } finally {
      setConnecting(null);
    }
  }

  async function handleConnectOpenHands() {
    if (!openhandsUrl.trim() || !openhandsKey.trim()) {
      setErr('OpenHands base URL and API key required');
      return;
    }
    setConnecting('openhands');
    setErr(null);
    try {
      const result = await connectOpenHands(openhandsUrl.trim(), openhandsKey.trim(), accessToken);
      setMsg(
        `${result.accountName} connected — Quick Build will dispatch tasks to ${result.baseUrl}`,
      );
      setOpenhandsKey('');
      load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'OpenHands connect failed');
    } finally {
      setConnecting(null);
    }
  }

  async function handleConnectCursor() {
    if (!cursorKey.trim()) {
      setErr('Cursor API key required');
      return;
    }
    setConnecting('cursor');
    setErr(null);
    try {
      const result = await connectCursorCloud(cursorKey.trim(), accessToken);
      setMsg(
        `${result.accountName} connected — Quick Build and Continue will dispatch to Cursor Cloud Agents${
          result.agentUrl ? ` (${result.agentUrl})` : ''
        }`,
      );
      setCursorKey('');
      load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Cursor connect failed');
    } finally {
      setConnecting(null);
    }
  }

  async function handleConnectPhala() {
    if (!phalaKey.trim()) {
      setErr('Phala API key required');
      return;
    }
    setConnecting('phala');
    setErr(null);
    try {
      const result = await connectPhalaDirect(
        phalaKey.trim(),
        phalaUrl.trim() || undefined,
        phalaModel.trim() || undefined,
        accessToken,
      );
      const brain = (result as { brainActivated?: { label: string } }).brainActivated;
      setMsg(
        brain
          ? `${result.accountName} connected — ${brain.label} is now your Copilot brain (TEE at ${result.inferenceUrl})`
          : `${result.accountName} connected — Copilot can use TEE inference at ${result.inferenceUrl}`,
      );
      setPhalaKey('');
      load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Phala connect failed');
    } finally {
      setConnecting(null);
    }
  }

  async function handleConnectOllamaDirect() {
    if (!ollamaUrl.trim()) {
      setErr('Ollama base URL required');
      return;
    }
    setConnecting('ollama');
    setErr(null);
    try {
      const result = await connectOllamaDirect(ollamaUrl.trim(), ollamaModel.trim() || undefined, accessToken);
      const brain = (result as { brainActivated?: { label: string } }).brainActivated;
      setMsg(
        brain
          ? `${result.accountName} connected — ${brain.label} is now your Copilot brain`
          : `${result.accountName} connected at ${result.baseUrl}`,
      );
      load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Ollama connect failed');
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

  const llmProviders = settings.providers.filter(
    (p) =>
      p.key !== 'RULE_BASED' &&
      p.key !== 'OPENHANDS' &&
      p.key !== 'CURSOR' &&
      p.key !== 'OLLAMA_LOCAL' &&
      p.key !== 'PHALA' &&
      p.key !== 'OPENROUTER',
  );
  const openHandsProvider = settings.providers.find((p) => p.key === 'OPENHANDS');
  const cursorProvider = settings.providers.find((p) => p.key === 'CURSOR');

  return (
    <div className="space-y-8">
      <FounderNodeHubPanel
        accessToken={accessToken}
        settings={settings}
        onRefresh={load}
        memoryMode={(settings.memoryStorageMode as MemoryStorageModeKey) ?? 'PLATFORM'}
        onMemoryModeChange={(mode) => setSettings((s) => (s ? { ...s, memoryStorageMode: mode } : s))}
        aiSection={{
          settings,
          apiKeyInput,
          setApiKeyInput,
          ollamaUrl,
          setOllamaUrl,
          ollamaModel,
          setOllamaModel,
          phalaKey,
          setPhalaKey,
          phalaUrl,
          setPhalaUrl,
          phalaModel,
          setPhalaModel,
          connecting,
          onConnectOpenRouter: () => handleConnectProvider('openrouter'),
          onConnectOllama: handleConnectOllamaDirect,
          onConnectPhala: handleConnectPhala,
          onDisconnectPhala: () => handleDisconnect('phala'),
          onSaveSettings: saveSettings,
        }}
      />

      <section className="rounded-2xl border border-zinc-800 bg-zinc-900/40 p-6">
        <h2 className="text-lg font-semibold text-white">Autopilot & production sync</h2>
        <p className="mt-1 text-sm text-zinc-500">
          Push schema to Neon, redeploy Vercel and Railway, and sync GitHub — without cluttering Mission
          Control.
        </p>
        <div className="mt-4">
          <HybridControlPlane
            accessToken={accessToken}
            onMessage={setMsg}
            onRefresh={load}
            autoRunWhenAutopilot={false}
          />
        </div>
      </section>

      <section className="rounded-2xl border border-zinc-800 bg-zinc-900/40 p-6">
        <h2 className="text-lg font-semibold text-white">Remote builder agents</h2>
        <p className="mt-1 text-sm text-zinc-500">
          Optional cloud coding agents — Cursor and OpenHands dispatch Quick Build tasks. LLM keys below power specs and
          Founder Brain when not using local Ollama or Phala.
        </p>

        <div className="mt-4 space-y-3 rounded-xl border border-indigo-500/25 bg-indigo-950/10 p-4">
          <h3 className="text-sm font-semibold text-indigo-100">Control plane mode</h3>
          <div className="flex flex-wrap gap-2">
            {(['CURSOR_FIRST', 'FULL_STACK'] as const).map((mode) => (
              <button
                key={mode}
                type="button"
                onClick={() => saveSettings({ controlPlaneMode: mode })}
                className={`rounded-lg border px-3 py-2 text-xs ${
                  (settings.controlPlaneMode ?? 'FULL_STACK') === mode
                    ? 'border-indigo-400 bg-indigo-950/50 text-white'
                    : 'border-zinc-700 text-zinc-500'
                }`}
              >
                {mode === 'CURSOR_FIRST' ? 'Cursor for code' : 'Full stack'}
              </button>
            ))}
          </div>
        </div>

        <div className="mt-4 space-y-3 rounded-xl border border-emerald-500/25 bg-emerald-950/10 p-4">
          <h3 className="text-sm font-semibold text-emerald-100">Autopilot</h3>
          <p className="text-xs text-zinc-500">
            When enabled, Copilot can sync GitHub, publish pending updates, redeploy Vercel/Railway (if
            connected), and resume your builder agent. Say &quot;take full control&quot; in Mission Control.
          </p>
          <div className="flex flex-col gap-2 text-sm text-zinc-300">
            <label className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={settings.autopilotEnabled ?? false}
                onChange={(e) => saveSettings({ autopilotEnabled: e.target.checked })}
              />
              Enable Autopilot (sync + publish + builder resume)
            </label>
            <label className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={settings.autopilotRedeployHosts ?? false}
                onChange={(e) => saveSettings({ autopilotRedeployHosts: e.target.checked })}
              />
              Redeploy Vercel + Railway on Autopilot (requires tokens in Stack hub)
            </label>
            <label className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={settings.autoPublishOnEvent}
                onChange={(e) => saveSettings({ autoPublishOnEvent: e.target.checked })}
              />
              Auto-publish on deploy webhooks
            </label>
            <label className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={settings.autoCreateGitHubIssues}
                onChange={(e) => saveSettings({ autoCreateGitHubIssues: e.target.checked })}
              />
              Auto-create GitHub issues from Quick Build
            </label>
          </div>
        </div>

        <div className="mt-4">
          <p className="text-xs font-medium text-zinc-400">Default chat AI (when multiple connected)</p>
          <select
            value={settings.defaultProvider}
            onChange={(e) => saveSettings({ defaultProvider: e.target.value })}
            className="mt-2 w-full max-w-md rounded-lg border border-zinc-700 bg-black px-3 py-2 text-sm text-white"
          >
            {settings.providers
              .filter(
                (p) =>
                  p.connected &&
                  p.key !== 'RULE_BASED' &&
                  (p.connectMode === 'api_key' || p.connectMode === 'founder_node'),
              )
              .map((p) => (
                <option key={p.key} value={p.key}>
                  {p.label}
                </option>
              ))}
            <option value="RULE_BASED">Project memory only</option>
          </select>
        </div>

        <div className="mt-6 space-y-6">
        <div className="rounded-xl border border-sky-500/30 bg-sky-950/10 p-4">
        <h3 className="font-semibold text-white">Cursor Cloud Agents</h3>
        <p className="mt-1 text-sm text-zinc-500">
          Cursor Cloud Agents API — Founder OS creates and resumes cloud agents on your GitHub repo. Generate an API key
          in{' '}
          <a href="https://cursor.com/dashboard" className="text-sky-300 underline" target="_blank" rel="noreferrer">
            Cursor Dashboard → Integrations
          </a>
          .
        </p>
        {cursorProvider?.connected && (
          <p className="mt-2 text-xs text-emerald-300">
            Connected — Quick Build and Continue dispatch remotely
            {settings.cursorAgentUrl ? (
              <>
                {' '}
                ·{' '}
                <a href={settings.cursorAgentUrl} className="underline" target="_blank" rel="noreferrer">
                  Open agent
                </a>
              </>
            ) : null}
          </p>
        )}
        <div className="mt-4 flex flex-col gap-2 sm:flex-row">
          <input
            type="password"
            value={cursorKey}
            onChange={(e) => setCursorKey(e.target.value)}
            placeholder="Cursor API key"
            className="flex-1 rounded-lg border border-zinc-700 bg-black px-3 py-2 text-sm"
          />
          <button
            type="button"
            disabled={connecting === 'cursor'}
            onClick={handleConnectCursor}
            className="rounded-lg bg-sky-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
          >
            {cursorProvider?.connected ? 'Update connection' : 'Connect Cursor'}
          </button>
          {cursorProvider?.connected && (
            <button
              type="button"
              onClick={() => handleDisconnect('cursor')}
              className="rounded-lg border border-red-500/40 bg-red-950/20 px-4 py-2 text-sm font-medium text-red-200 hover:border-red-400/60"
            >
              Disconnect
            </button>
          )}
        </div>
        </div>

        <div className="rounded-xl border border-indigo-500/30 bg-indigo-950/10 p-4">
        <h3 className="font-semibold text-white">OpenHands — remote coding agent</h3>
        <p className="mt-1 text-sm text-zinc-500">
          Self-hosted OpenHands or{' '}
          <a href="https://app.all-hands.dev" className="text-indigo-300 underline" target="_blank" rel="noreferrer">
            OpenHands Cloud
          </a>
          . When set as default, Quick Build dispatches specs directly — Cursor-like, no copy-paste.
        </p>
        {openHandsProvider?.connected && (
          <p className="mt-2 text-xs text-emerald-300">Connected — tasks dispatch on Quick Build</p>
        )}
        <div className="mt-4 space-y-3">
          <input
            type="url"
            value={openhandsUrl}
            onChange={(e) => setOpenhandsUrl(e.target.value)}
            placeholder="https://app.all-hands.dev or https://your-openhands.example.com"
            className="w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm"
          />
          <div className="flex flex-col gap-2 sm:flex-row">
            <input
              type="password"
              value={openhandsKey}
              onChange={(e) => setOpenhandsKey(e.target.value)}
              placeholder="OpenHands API key"
              className="flex-1 rounded-lg border border-zinc-700 bg-black px-3 py-2 text-sm"
            />
            <button
              type="button"
              disabled={connecting === 'openhands'}
              onClick={handleConnectOpenHands}
              className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
            >
              {openHandsProvider?.connected ? 'Update connection' : 'Connect OpenHands'}
            </button>
            {openHandsProvider?.connected && (
              <button
                type="button"
                onClick={() => handleDisconnect('openhands')}
                className="rounded-lg border border-red-500/40 bg-red-950/20 px-4 py-2 text-sm font-medium text-red-200 hover:border-red-400/60"
              >
                Disconnect
              </button>
            )}
          </div>
        </div>
        </div>

        <div className="rounded-xl border border-zinc-800 p-4">
        <h3 className="font-semibold text-white">LLM providers (specs & Founder Brain)</h3>
        <p className="mt-1 text-sm text-zinc-500">
          Additional API keys — encrypted at rest. Set default provider in Founder Node Step 3 above.
        </p>
        <div className="mt-4 space-y-4">
          {llmProviders.map((p) => (
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
              <div className="mt-3 flex flex-col gap-2 sm:flex-row">
                <input
                  type="password"
                  value={apiKeyInput[p.credentialProvider ?? ''] ?? ''}
                  onChange={(e) =>
                    setApiKeyInput({ ...apiKeyInput, [p.credentialProvider ?? '']: e.target.value })
                  }
                  placeholder="Paste API key"
                  className="flex-1 rounded-lg border border-zinc-700 bg-black px-3 py-2 text-sm"
                />
                <button
                  type="button"
                  disabled={connecting === p.credentialProvider}
                  onClick={() => p.credentialProvider && handleConnectProvider(p.credentialProvider)}
                  className="rounded-lg bg-violet-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
                >
                  {p.connected ? 'Update key' : 'Connect & activate'}
                </button>
                {p.connected && p.credentialProvider && (
                  <button
                    type="button"
                    onClick={() => handleDisconnect(p.credentialProvider!)}
                    className="rounded-lg border border-red-500/40 bg-red-950/20 px-4 py-2 text-sm font-medium text-red-200 hover:border-red-400/60"
                  >
                    Disconnect
                  </button>
                )}
              </div>
            </div>
          ))}
        </div>
        </div>

        <div className="rounded-xl border border-zinc-800 p-4">
        <h3 className="font-semibold text-white">GitHub personal access token</h3>
        <p className="mt-1 text-sm text-zinc-500">
          Optional for public repos; <strong className="text-zinc-300">required for private repos</strong> so Founder
          OS can sync commits, list PRs, publish issues, and update{' '}
          <code className="text-violet-300/90">.github/founder-os/</code> memory files.
        </p>

        <GitHubPatConnectGuide
          githubTokenConnected={settings.githubTokenConnected}
          repoLinked={settings.repoFullName ?? null}
        />

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
        </div>
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
