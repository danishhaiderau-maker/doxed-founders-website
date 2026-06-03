'use client';

import type { BuilderSettings } from '@/lib/api';

type Props = {
  settings: BuilderSettings;
  apiKeyInput: Record<string, string>;
  setApiKeyInput: (v: Record<string, string>) => void;
  ollamaUrl: string;
  setOllamaUrl: (v: string) => void;
  ollamaModel: string;
  setOllamaModel: (v: string) => void;
  phalaKey: string;
  setPhalaKey: (v: string) => void;
  phalaUrl: string;
  setPhalaUrl: (v: string) => void;
  phalaModel: string;
  setPhalaModel: (v: string) => void;
  connecting: string | null;
  onConnectOpenRouter: () => void;
  onConnectOllama: () => void;
  onConnectPhala: () => void;
  onDisconnectPhala: () => void;
  onSaveSettings: (patch: {
    defaultProvider?: string;
    preferredModel?: string;
    currentGoalFocus?: string;
  }) => void;
};

export function FounderNodeAiSection({
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
  onConnectOpenRouter,
  onConnectOllama,
  onConnectPhala,
  onDisconnectPhala,
  onSaveSettings,
}: Props) {
  const openRouterProvider = settings.providers.find((p) => p.key === 'OPENROUTER');
  const ollamaLocalProvider = settings.providers.find((p) => p.key === 'OLLAMA_LOCAL');
  const phalaProvider = settings.providers.find((p) => p.key === 'PHALA');
  const phalaStatus = settings.phalaPrivateAi;
  const nodeAi = settings.founderNodeAi;

  const brainProviders = settings.providers.filter(
    (p) =>
      p.key !== 'RULE_BASED' &&
      p.key !== 'CURSOR' &&
      p.key !== 'OPENHANDS' &&
      (p.connectMode === 'api_key' || p.connectMode === 'founder_node'),
  );

  const activeBrain = brainProviders.find((p) => p.key === settings.defaultProvider);
  const brainReady = settings.defaultBrainConnected ?? Boolean(activeBrain?.connected);
  const needsConnect =
    (settings.connectedBrainCount ?? 0) > 0 && !brainReady && settings.defaultProvider !== 'RULE_BASED';

  return (
    <div className="space-y-5">
      <div className="rounded-lg border border-violet-500/25 bg-violet-950/20 p-4 text-xs leading-relaxed text-zinc-400">
        <p className="font-semibold text-violet-200">Brain vs code agent</p>
        <ul className="mt-2 list-inside list-disc space-y-1">
          <li>
            <strong className="text-zinc-200">Brain (this section):</strong> the LLM you connect here powers
            Copilot <em>Ask</em>, Founder Brain, and every project agent (community replies, marketing drafts,
            research). Connect OpenRouter, Ollama, or Phala — not Cursor.
          </li>
          <li>
            <strong className="text-zinc-200">Code (Remote builder agents below):</strong> Cursor / OpenHands
            edit your GitHub repo. Use <em>Run in Cursor</em> in Mission Control.
          </li>
          <li>
            <strong className="text-zinc-200">Project loyalty:</strong> agents run from your project profile only
            promote and defend that project, using this brain.
          </li>
        </ul>
      </div>

      {brainReady && activeBrain && (
        <div className="rounded-lg border border-emerald-500/30 bg-emerald-950/20 px-4 py-3 text-sm text-emerald-100">
          <strong className="text-white">Active brain:</strong> {activeBrain.label}
          {settings.preferredModel ? (
            <span className="text-emerald-200/80"> · model {settings.preferredModel}</span>
          ) : null}
          <span className="mt-1 block text-xs text-emerald-200/70">
            Copilot Ask, Founder Brain, and project agents use this LLM.
          </span>
        </div>
      )}
      {needsConnect && (
        <div className="rounded-lg border border-amber-500/35 bg-amber-950/20 px-4 py-3 text-xs text-amber-100">
          Your saved brain ({settings.defaultProvider}) is not connected. Pick a connected provider below or
          reconnect — we auto-activate when you click Connect.
        </div>
      )}

      <div className="grid gap-4 sm:grid-cols-2">
        <label className="block text-sm">
          <span className="text-zinc-400">Default brain (Copilot + agents)</span>
          <select
            value={settings.defaultProvider}
            onChange={(e) => onSaveSettings({ defaultProvider: e.target.value })}
            className="mt-1 w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-white"
          >
            {brainProviders.map((p) => (
              <option key={p.key} value={p.key} disabled={!p.connected}>
                {p.label}
                {p.connected ? '' : ' (connect below first)'}
              </option>
            ))}
            <option value="RULE_BASED">Project memory only (no LLM)</option>
          </select>
          <p className="mt-1 text-[10px] text-zinc-600">
            Connect a provider in the cards below — it becomes your brain automatically. Override here if you
            use more than one.
          </p>
        </label>
        <label className="block text-sm">
          <span className="text-zinc-400">Preferred model</span>
          <input
            defaultValue={settings.preferredModel ?? ''}
            onBlur={(e) => onSaveSettings({ preferredModel: e.target.value || undefined })}
            placeholder="gpt-4o-mini, llama3.2, phala/deepseek…"
            className="mt-1 w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-white"
          />
        </label>
      </div>

      <label className="block text-sm">
        <span className="text-zinc-400">Current goal focus</span>
        <input
          defaultValue={settings.currentGoalFocus ?? ''}
          onBlur={(e) => onSaveSettings({ currentGoalFocus: e.target.value.trim() || undefined })}
          placeholder="e.g. Ship referral system — syncs to your local vault"
          className="mt-1 w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-white"
        />
      </label>

      <div className="flex flex-wrap gap-2">
        {openRouterProvider?.connected && (
          <span className="rounded-full bg-emerald-500/20 px-2.5 py-1 text-[10px] font-semibold text-emerald-200">
            OpenRouter connected
          </span>
        )}
        {ollamaLocalProvider?.connected && (
          <span className="rounded-full bg-cyan-500/20 px-2.5 py-1 text-[10px] font-semibold text-cyan-100">
            Ollama ready{nodeAi?.ollamaModel ? ` · ${nodeAi.ollamaModel}` : ''}
          </span>
        )}
        {phalaProvider?.connected && (
          <span className="rounded-full bg-fuchsia-500/20 px-2.5 py-1 text-[10px] font-semibold text-fuchsia-100">
            Phala TEE{phalaStatus?.model ? ` · ${phalaStatus.model}` : ''}
          </span>
        )}
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <div className="rounded-xl border border-zinc-800 bg-zinc-950/50 p-4">
          <p className="font-medium text-white">OpenRouter</p>
          <p className="mt-1 text-xs text-zinc-500">One key — Claude, GPT, DeepSeek, and more.</p>
          <div className="mt-3 flex flex-col gap-2">
            <input
              type="password"
              value={apiKeyInput.openrouter ?? ''}
              onChange={(e) => setApiKeyInput({ ...apiKeyInput, openrouter: e.target.value })}
              placeholder="sk-or-…"
              className="rounded-lg border border-zinc-700 bg-black px-3 py-2 text-sm"
            />
            <button
              type="button"
              disabled={connecting === 'openrouter'}
              onClick={onConnectOpenRouter}
              className="rounded-lg bg-emerald-600 px-3 py-2 text-sm font-medium text-white disabled:opacity-50"
            >
              {openRouterProvider?.connected ? 'Update key' : 'Connect & activate'}
            </button>
          </div>
        </div>

        <div className="rounded-xl border border-cyan-500/30 bg-cyan-950/10 p-4">
          <p className="font-medium text-white">Ollama (local)</p>
          <p className="mt-1 text-xs text-zinc-500">
            Runs on your machine via Founder Node — prompts never leave your desktop.
          </p>
          {nodeAi && (
            <p className="mt-2 text-xs text-zinc-400">
              {nodeAi.paired
                ? nodeAi.online
                  ? `${nodeAi.nodeLabel ?? 'Node'} online${nodeAi.ollamaReady ? '' : ' — start Ollama'}`
                  : 'Node offline — open tray app'
                : 'Pair Founder Node first (Step 2)'}
            </p>
          )}
          <div className="mt-3 space-y-2">
            <input
              type="url"
              value={ollamaUrl}
              onChange={(e) => setOllamaUrl(e.target.value)}
              placeholder="http://127.0.0.1:11434"
              className="w-full rounded-lg border border-zinc-700 bg-black px-3 py-2 text-sm"
            />
            <div className="flex gap-2">
              <input
                value={ollamaModel}
                onChange={(e) => setOllamaModel(e.target.value)}
                placeholder="llama3.2"
                className="flex-1 rounded-lg border border-zinc-700 bg-black px-3 py-2 text-sm"
              />
              <button
                type="button"
                disabled={connecting === 'ollama'}
                onClick={onConnectOllama}
                className="rounded-lg bg-cyan-700 px-3 py-2 text-sm font-medium text-white disabled:opacity-50"
              >
                {ollamaLocalProvider?.connected ? 'Update' : 'Connect & activate'}
              </button>
            </div>
          </div>
        </div>

        <div className="rounded-xl border border-fuchsia-500/30 bg-fuchsia-950/10 p-4">
          <p className="font-medium text-white">Phala TEE (private AI)</p>
          <p className="mt-1 text-xs text-zinc-500">Confidential inference in hardware TEE — Step 5 attestation.</p>
          <div className="mt-3 space-y-2">
            <input
              type="password"
              value={phalaKey}
              onChange={(e) => setPhalaKey(e.target.value)}
              placeholder="Phala API key"
              className="w-full rounded-lg border border-zinc-700 bg-black px-3 py-2 text-sm"
            />
            <input
              type="url"
              value={phalaUrl}
              onChange={(e) => setPhalaUrl(e.target.value)}
              placeholder="https://api.redpill.ai/v1"
              className="w-full rounded-lg border border-zinc-700 bg-black px-3 py-2 text-sm"
            />
            <div className="flex gap-2">
              <input
                value={phalaModel}
                onChange={(e) => setPhalaModel(e.target.value)}
                placeholder="phala/deepseek-chat-v3-0324"
                className="flex-1 rounded-lg border border-zinc-700 bg-black px-3 py-2 text-sm"
              />
              <button
                type="button"
                disabled={connecting === 'phala'}
                onClick={onConnectPhala}
                className="rounded-lg bg-fuchsia-700 px-3 py-2 text-sm font-medium text-white disabled:opacity-50"
              >
                {phalaProvider?.connected ? 'Update' : 'Connect & activate'}
              </button>
            </div>
            {phalaProvider?.connected && (
              <button
                type="button"
                onClick={onDisconnectPhala}
                className="text-xs text-red-300 underline"
              >
                Disconnect Phala
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
