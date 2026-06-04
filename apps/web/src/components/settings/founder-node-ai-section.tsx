'use client';

import { AI_PROVIDERS } from '@dcf/utils';
import type { BuilderSettings } from '@/lib/api';

function mergeBrainProviders(settings: BuilderSettings) {
  const fromApi = new Map(settings.providers.map((p) => [p.key, p]));
  return AI_PROVIDERS.filter(
    (cfg) =>
      cfg.key !== 'RULE_BASED' &&
      cfg.key !== 'CURSOR' &&
      cfg.key !== 'OPENHANDS' &&
      (cfg.connectMode === 'api_key' || cfg.connectMode === 'founder_node'),
  ).map((cfg) => {
    const api = fromApi.get(cfg.key);
    return {
      key: cfg.key,
      label: cfg.label,
      connected: api?.connected ?? false,
      connectMode: cfg.connectMode,
      credentialProvider: cfg.credentialProvider,
    };
  });
}

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
  onConnectJatevo: () => void;
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
  onConnectJatevo,
  onConnectOllama,
  onConnectPhala,
  onDisconnectPhala,
  onSaveSettings,
}: Props) {
  const openRouterProvider = settings.providers.find((p) => p.key === 'OPENROUTER');
  const jatevoProvider = settings.providers.find((p) => p.key === 'JATEVO');
  const ollamaLocalProvider = settings.providers.find((p) => p.key === 'OLLAMA_LOCAL');
  const phalaProvider = settings.providers.find((p) => p.key === 'PHALA');
  const phalaStatus = settings.phalaPrivateAi;
  const nodeAi = settings.founderNodeAi;

  const brainProviders = mergeBrainProviders(settings);

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
            research). Connect Jatevo, OpenRouter, Ollama, or Phala — not Cursor.
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

      <div className="rounded-lg border border-zinc-700/80 bg-zinc-950/60 p-4 text-xs text-zinc-400">
        <p className="font-semibold text-zinc-200">What to connect (quick reference)</p>
        <div className="mt-3 overflow-x-auto">
          <table className="w-full min-w-[280px] text-left text-[11px]">
            <thead>
              <tr className="text-zinc-500">
                <th className="pb-2 pr-3 font-medium">Provider</th>
                <th className="pb-2 pr-3 font-medium">You add</th>
                <th className="pb-2 font-medium">Where</th>
              </tr>
            </thead>
            <tbody className="text-zinc-300">
              <tr className="border-t border-zinc-800">
                <td className="py-2 pr-3 text-amber-200">Jatevo</td>
                <td className="py-2 pr-3">API key <code className="text-zinc-500">sk-clb-…</code></td>
                <td className="py-2">Jatevo card → Connect</td>
              </tr>
              <tr className="border-t border-zinc-800">
                <td className="py-2 pr-3 text-emerald-200">OpenRouter</td>
                <td className="py-2 pr-3">API key <code className="text-zinc-500">sk-or-…</code></td>
                <td className="py-2">OpenRouter card → Connect</td>
              </tr>
              <tr className="border-t border-zinc-800">
                <td className="py-2 pr-3 text-cyan-200">Ollama (local)</td>
                <td className="py-2 pr-3">
                  <strong className="text-emerald-300">No API key</strong> — install Ollama + Founder Node
                </td>
                <td className="py-2">Steps in cyan card; pick Default brain</td>
              </tr>
              <tr className="border-t border-zinc-800">
                <td className="py-2 pr-3 text-fuchsia-200">Phala TEE</td>
                <td className="py-2 pr-3">API key + optional URL</td>
                <td className="py-2">Phala card → Connect</td>
              </tr>
              <tr className="border-t border-zinc-800">
                <td className="py-2 pr-3 text-violet-200">OpenAI / Claude / DeepSeek</td>
                <td className="py-2 pr-3">API key each</td>
                <td className="py-2">LLM providers section below</td>
              </tr>
            </tbody>
          </table>
        </div>
        <p className="mt-3 text-[10px] text-zinc-500">
          <strong className="text-zinc-400">Ollama confusion?</strong> Do not paste{' '}
          <code className="text-zinc-500">127.0.0.1</code> unless you host Ollama on a public server. Desktop Ollama
          uses Founder Node only.
        </p>
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
            placeholder="auto, gpt-4o-mini, qwen3.5-plus, llama3.2…"
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
        {jatevoProvider?.connected && (
          <span className="rounded-full bg-amber-500/20 px-2.5 py-1 text-[10px] font-semibold text-amber-100">
            Jatevo ($JTVO) connected
          </span>
        )}
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

      <div className="grid gap-4 lg:grid-cols-2 xl:grid-cols-4">
        <div className="rounded-xl border border-amber-500/30 bg-amber-950/10 p-4">
          <p className="font-medium text-white">
            Jatevo <span className="text-amber-300/90">($JTVO)</span>
          </p>
          <p className="mt-1 text-xs text-zinc-500">
            <strong className="text-amber-200/90">This is the $JTVO subsidized gateway</strong> — one key,
            multi-model routing. Get <code className="text-amber-200/80">sk-clb-…</code> on{' '}
            <a href="https://jatevo.ai" className="text-amber-300 underline" target="_blank" rel="noreferrer">
              jatevo.ai
            </a>
            .
          </p>
          <div className="mt-3 flex flex-col gap-2">
            <input
              type="password"
              value={apiKeyInput.jatevo ?? ''}
              onChange={(e) => setApiKeyInput({ ...apiKeyInput, jatevo: e.target.value })}
              placeholder="sk-clb-…"
              className="rounded-lg border border-zinc-700 bg-black px-3 py-2 text-sm"
            />
            <button
              type="button"
              disabled={connecting === 'jatevo'}
              onClick={onConnectJatevo}
              className="rounded-lg bg-amber-600 px-3 py-2 text-sm font-medium text-white disabled:opacity-50"
            >
              {jatevoProvider?.connected ? 'Update key' : 'Connect & activate'}
            </button>
          </div>
        </div>

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
            Private inference on <strong className="text-cyan-200/90">your PC</strong> — Copilot sends jobs to Founder
            Node; prompts never hit OpenAI/DeepSeek.
          </p>

          <div className="mt-3 rounded-lg border border-cyan-500/20 bg-cyan-950/30 p-3 text-xs leading-relaxed text-zinc-400">
            <p className="font-semibold text-cyan-100">Recommended path (Founder Node)</p>
            <ol className="mt-2 list-inside list-decimal space-y-1.5">
              <li>
                Install{' '}
                <a href="https://ollama.com/download" className="text-cyan-300 underline" target="_blank" rel="noreferrer">
                  Ollama
                </a>{' '}
                on the <em>same machine</em> as Founder Node (Windows/Mac/Linux).
              </li>
              <li>
                In a terminal: <code className="text-cyan-200/90">ollama pull llama3.2</code> (or another model you
                prefer).
              </li>
              <li>Keep the Ollama app running (it serves on port 11434 by default).</li>
              <li>
                Keep <strong className="text-zinc-300">Founder Node tray app</strong> open (Step 2 paired) — it detects
                Ollama every minute and reports to Founder OS.
              </li>
              <li>
                When you see <strong className="text-emerald-300">Ollama ready</strong> above, pick{' '}
                <strong className="text-zinc-300">Ollama (local via Founder Node)</strong> in{' '}
                <em>Default brain</em> — you do <strong className="text-amber-200/90">not</strong> need the URL box
                below for localhost.
              </li>
            </ol>
          </div>

          {nodeAi && (
            <div className="mt-3 space-y-2 text-xs">
              {!nodeAi.paired && (
                <p className="rounded-md border border-amber-500/30 bg-amber-950/25 px-3 py-2 text-amber-100">
                  Complete <strong>Step 2 — Vault & pairing</strong> first, then install Ollama on that desktop.
                </p>
              )}
              {nodeAi.paired && !nodeAi.online && (
                <p className="rounded-md border border-amber-500/30 bg-amber-950/25 px-3 py-2 text-amber-100">
                  Founder Node is offline — open the tray app near your clock and wait ~30s for status to refresh.
                </p>
              )}
              {nodeAi.paired && nodeAi.online && !nodeAi.ollamaReady && (
                <p className="rounded-md border border-amber-500/30 bg-amber-950/25 px-3 py-2 text-amber-100">
                  <strong>{nodeAi.nodeLabel ?? 'Node'} online</strong> but Ollama not detected — install/start Ollama,
                  run <code className="text-amber-200/80">ollama pull llama3.2</code>, then wait for the next tray
                  heartbeat.
                </p>
              )}
              {nodeAi.paired && nodeAi.online && nodeAi.ollamaReady && (
                <div className="rounded-md border border-emerald-500/35 bg-emerald-950/25 px-3 py-2 text-emerald-100">
                  <p>
                    <strong>{nodeAi.nodeLabel ?? 'Founder Node'} online</strong>
                    {nodeAi.ollamaModel ? ` · model ${nodeAi.ollamaModel}` : ''} — ready for Copilot.
                  </p>
                  {settings.defaultProvider !== 'OLLAMA_LOCAL' && (
                    <button
                      type="button"
                      className="mt-2 rounded-lg bg-emerald-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-emerald-500"
                      onClick={() =>
                        onSaveSettings({
                          defaultProvider: 'OLLAMA_LOCAL',
                          preferredModel: nodeAi.ollamaModel ?? 'llama3.2',
                        })
                      }
                    >
                      Set Ollama as default brain
                    </button>
                  )}
                </div>
              )}
            </div>
          )}

          <details className="mt-3 group">
            <summary className="cursor-pointer text-xs font-medium text-zinc-500 hover:text-zinc-300">
              Advanced: direct Ollama URL (VPS / homelab only)
            </summary>
            <p className="mt-2 text-[11px] leading-relaxed text-zinc-500">
              Only use this if Ollama runs on a server the <strong className="text-zinc-400">cloud API</strong> can
              reach. <code className="text-zinc-400">http://127.0.0.1:11434</code> on your laptop will{' '}
              <strong className="text-amber-200/80">fail</strong> here — localhost is not visible to Railway. Use the
              Founder Node path above instead.
            </p>
            <div className="mt-2 space-y-2">
              <input
                type="url"
                value={ollamaUrl}
                onChange={(e) => setOllamaUrl(e.target.value)}
                placeholder="https://ollama.your-vps.com:11434"
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
                  {ollamaLocalProvider?.connected ? 'Update URL' : 'Connect direct URL'}
                </button>
              </div>
            </div>
          </details>
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
