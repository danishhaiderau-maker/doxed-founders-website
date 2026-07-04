'use client';

import {
  AI_PROVIDERS,
  AI_PROVIDER_GUIDES,
  aiProviderConfig,
  listBrainProvidersByCategory,
  type AiProviderKey,
} from '@dcf/utils';
import type { BuilderSettings } from '@/lib/api';
import { CollapsibleInfo } from '@/components/ui/collapsible-info';

const CATEGORY_STYLES = {
  marketplace: {
    border: 'border-orange-500/30',
    bg: 'bg-orange-950/10',
    accent: 'text-orange-200',
    button: 'bg-orange-600 hover:bg-orange-500',
  },
  direct: {
    border: 'border-violet-500/30',
    bg: 'bg-violet-950/10',
    accent: 'text-violet-200',
    button: 'bg-violet-600 hover:bg-violet-500',
  },
  local: {
    border: 'border-cyan-500/30',
    bg: 'bg-cyan-950/10',
    accent: 'text-cyan-200',
    button: 'bg-cyan-700 hover:bg-cyan-600',
  },
} as const;

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
      key: cfg.key as AiProviderKey,
      label: cfg.label,
      connected: api?.connected ?? false,
      connectMode: cfg.connectMode,
      credentialProvider: cfg.credentialProvider,
      defaultModel: cfg.defaultModel,
      billTip: cfg.billTip,
    };
  });
}

function ApiKeyCard({
  providerKey,
  row,
  guide,
  apiKeyInput,
  setApiKeyInput,
  connecting,
  onConnect,
  onDisconnect,
  onSetDefault,
  isDefault,
}: {
  providerKey: AiProviderKey;
  row: ReturnType<typeof mergeBrainProviders>[number];
  guide: (typeof AI_PROVIDER_GUIDES)[AiProviderKey];
  apiKeyInput: Record<string, string>;
  setApiKeyInput: (v: Record<string, string>) => void;
  connecting: string | null;
  onConnect: () => void;
  onDisconnect: () => void;
  onSetDefault: () => void;
  isDefault: boolean;
}) {
  const cred = row.credentialProvider ?? providerKey.toLowerCase();
  const styleKey = guide?.category ?? 'direct';
  const style = CATEGORY_STYLES[styleKey === 'private' ? 'local' : styleKey];

  return (
    <div className={`rounded-xl border p-4 ${style.border} ${style.bg}`}>
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="font-medium text-white">{row.label}</p>
          <p className="mt-1 text-xs text-zinc-500">{row.billTip}</p>
        </div>
        <div className="flex flex-wrap gap-1.5">
          {row.connected && (
            <span className="rounded-full bg-emerald-500/20 px-2 py-0.5 text-[10px] font-semibold text-emerald-300">
              Connected
            </span>
          )}
          {isDefault && (
            <span className="rounded-full bg-white/10 px-2 py-0.5 text-[10px] font-semibold text-white">
              Default brain
            </span>
          )}
        </div>
      </div>

      {guide && (
        <CollapsibleInfo title={`${row.label} setup`} hint={`${guide.steps.length} steps`} accent="zinc">
          <ol className="list-inside list-decimal space-y-1 text-[11px] leading-relaxed text-zinc-400">
            {guide.steps.map((step) => (
              <li key={step}>
                {step.includes('http') ? step : step}
                {step.includes('http') ? null : (
                  <>
                    {' '}
                    {guide.keyUrlLabel && step.toLowerCase().includes('paste') ? (
                      <a
                        href={guide.keyUrl}
                        className={`${style.accent} underline`}
                        target="_blank"
                        rel="noreferrer"
                      >
                        Get key →
                      </a>
                    ) : null}
                  </>
                )}
              </li>
            ))}
            {guide.keyUrl && !guide.steps.some((s) => s.includes('http')) && (
              <li>
                Get your key at{' '}
                <a href={guide.keyUrl} className={`${style.accent} underline`} target="_blank" rel="noreferrer">
                  {guide.keyUrlLabel}
                </a>
              </li>
            )}
          </ol>
        </CollapsibleInfo>
      )}

      <div className="mt-3 flex flex-col gap-2">
        <input
          type="password"
          value={apiKeyInput[cred] ?? ''}
          onChange={(e) => setApiKeyInput({ ...apiKeyInput, [cred]: e.target.value })}
          placeholder={guide?.keyPlaceholder ?? 'Paste API key'}
          className="rounded-lg border border-zinc-700 bg-black px-3 py-2 text-sm text-white"
        />
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            disabled={connecting === cred}
            onClick={onConnect}
            className={`rounded-lg px-3 py-2 text-sm font-medium text-white disabled:opacity-50 ${style.button}`}
          >
            {row.connected ? 'Update key' : 'Connect & activate'}
          </button>
          {row.connected && (
            <>
              {!isDefault && (
                <button
                  type="button"
                  onClick={onSetDefault}
                  className="rounded-lg border border-zinc-600 px-3 py-2 text-sm text-zinc-200 hover:border-zinc-400"
                >
                  Set as default
                </button>
              )}
              <button
                type="button"
                onClick={onDisconnect}
                className="rounded-lg border border-red-500/40 px-3 py-2 text-sm text-red-200 hover:border-red-400"
              >
                Disconnect
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  );
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
  onConnectProvider: (credentialProvider: string) => void;
  onConnectOllama: () => void;
  onConnectPhala: () => void;
  onDisconnectProvider: (credentialProvider: string) => void;
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
  onConnectProvider,
  onConnectOllama,
  onConnectPhala,
  onDisconnectProvider,
  onSaveSettings,
}: Props) {
  const brainProviders = mergeBrainProviders(settings);
  const providerMap = new Map(brainProviders.map((p) => [p.key, p]));
  const activeBrain = brainProviders.find((p) => p.key === settings.defaultProvider);
  const brainReady = settings.defaultBrainConnected ?? Boolean(activeBrain?.connected);
  const connectedCount = brainProviders.filter((p) => p.connected).length;

  const categories = listBrainProvidersByCategory();

  return (
    <div className="space-y-6">
      <CollapsibleInfo title="How to connect your AI brain" hint="3 steps" accent="violet">
        <ol className="list-inside list-decimal space-y-2 text-xs leading-relaxed text-zinc-400">
          <li>
            <strong className="text-zinc-200">Pick a provider below</strong> — marketplace (Surplus, OpenRouter),
            direct vendor (OpenAI, Anthropic, Gemini), or local Ollama.
          </li>
          <li>
            <strong className="text-zinc-200">Paste your API key</strong> → click{' '}
            <em className="text-emerald-300">Connect & activate</em>. Gray options in the brain picker mean the key
            is not connected yet.
          </li>
          <li>
            <strong className="text-zinc-200">Set Default brain</strong> — use the card button or the picker below.
            Copilot, Founder Brain, and project agents use this LLM.
          </li>
        </ol>
        <p className="mt-3 text-[11px] text-zinc-500">
          <strong className="text-zinc-400">Brain vs code:</strong> this section powers chat & drafts. Cursor /
          OpenHands below edit your GitHub repo.
        </p>
      </CollapsibleInfo>

      {/* Active brain status */}
      {brainReady && activeBrain ? (
        <div className="rounded-lg border border-emerald-500/30 bg-emerald-950/20 px-4 py-3 text-sm text-emerald-100">
          <strong className="text-white">Active brain:</strong> {activeBrain.label}
          {settings.preferredModel ? (
            <span className="text-emerald-200/80"> · model {settings.preferredModel}</span>
          ) : activeBrain.defaultModel ? (
            <span className="text-emerald-200/80"> · default {activeBrain.defaultModel}</span>
          ) : null}
          <span className="mt-1 block text-xs text-emerald-200/70">
            {connectedCount} provider{connectedCount === 1 ? '' : 's'} connected
          </span>
        </div>
      ) : (
        <div className="rounded-lg border border-amber-500/35 bg-amber-950/20 px-4 py-3 text-xs text-amber-100">
          No brain connected yet — connect any provider below, then set it as default. Having a key alone is not enough;
          you must click <strong>Connect & activate</strong>.
        </div>
      )}

      {/* Default brain picker — connected only clickable */}
      <div className="rounded-xl border border-zinc-800 bg-zinc-950/40 p-4">
        <p className="text-sm font-semibold text-white">Default brain</p>
        <p className="mt-1 text-xs text-zinc-500">
          Only connected providers can be selected. Connect OpenAI, Anthropic, or Gemini in the cards below first.
        </p>
        <div className="mt-3 grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
          {brainProviders.map((p) => {
            const selected = settings.defaultProvider === p.key;
            return (
              <button
                key={p.key}
                type="button"
                disabled={!p.connected}
                onClick={() =>
                  onSaveSettings({
                    defaultProvider: p.key,
                    preferredModel: p.defaultModel ?? undefined,
                  })
                }
                className={`rounded-lg border px-3 py-2.5 text-left text-xs transition ${
                  selected
                    ? 'border-emerald-400/60 bg-emerald-950/30 text-white'
                    : p.connected
                      ? 'border-zinc-700 bg-zinc-900/60 text-zinc-200 hover:border-zinc-500'
                      : 'cursor-not-allowed border-zinc-800 bg-zinc-950/40 text-zinc-600'
                }`}
              >
                <span className="font-medium">{p.label}</span>
                <span className="mt-0.5 block text-[10px] opacity-80">
                  {p.connected ? (selected ? 'Active' : 'Click to use') : 'Connect below first'}
                </span>
              </button>
            );
          })}
          <button
            type="button"
            onClick={() => onSaveSettings({ defaultProvider: 'RULE_BASED' })}
            className={`rounded-lg border px-3 py-2.5 text-left text-xs ${
              settings.defaultProvider === 'RULE_BASED'
                ? 'border-zinc-400 bg-zinc-800 text-white'
                : 'border-zinc-800 text-zinc-500 hover:border-zinc-600'
            }`}
          >
            <span className="font-medium">Project memory only</span>
            <span className="mt-0.5 block text-[10px]">No LLM — rules + GitHub context</span>
          </button>
        </div>

        <label className="mt-4 block text-sm">
          <span className="text-zinc-400">Preferred model (optional override)</span>
          <input
            defaultValue={settings.preferredModel ?? ''}
            onBlur={(e) => onSaveSettings({ preferredModel: e.target.value.trim() || undefined })}
            placeholder="e.g. claude-3-5-haiku-latest, gpt-4o-mini, claude-opus-4.8"
            className="mt-1 w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm text-white"
          />
        </label>
      </div>

      {/* Provider categories */}
      {categories.map((cat) => {
        if (cat.category === 'local') {
          const ollama = providerMap.get('OLLAMA_LOCAL');
          const phala = providerMap.get('PHALA');
          const nodeAi = settings.founderNodeAi;
          const phalaStatus = settings.phalaPrivateAi;
          const localStyle = CATEGORY_STYLES.local;

          return (
            <div key={cat.category} className="space-y-3">
              <h4 className="text-sm font-semibold text-zinc-200">{cat.label}</h4>
              <div className="grid gap-4 lg:grid-cols-2">
                {/* Ollama */}
                <div className={`rounded-xl border p-4 ${localStyle.border} ${localStyle.bg}`}>
                  <p className="font-medium text-white">Ollama (local via Founder Node)</p>
                  <p className="mt-1 text-xs text-zinc-500">
                    No API key — install Ollama on your PC, pair Founder Node, keep tray app running.
                  </p>
                  <CollapsibleInfo title="Ollama setup" hint="3 steps" accent="cyan">
                    <ol className="list-inside list-decimal space-y-1 text-[11px] text-zinc-400">
                      <li>
                        Install{' '}
                        <a href="https://ollama.com/download" className="text-cyan-300 underline" target="_blank" rel="noreferrer">
                          Ollama
                        </a>{' '}
                        on the same machine as Founder Node.
                      </li>
                      <li>
                        Run <code className="text-cyan-200/90">ollama pull llama3.2</code> and keep Ollama running.
                      </li>
                      <li>Wait for <strong className="text-emerald-300">Ollama ready</strong> in pairing status.</li>
                    </ol>
                  </CollapsibleInfo>
                  {nodeAi?.paired && nodeAi.online && nodeAi.ollamaReady && (
                    <p className="mt-2 text-xs text-emerald-300">
                      Ready{nodeAi.ollamaModel ? ` · ${nodeAi.ollamaModel}` : ''}
                    </p>
                  )}
                  {nodeAi?.paired && nodeAi.online && !nodeAi.ollamaReady && (
                    <p className="mt-2 text-xs text-amber-200">Node online — start Ollama on your desktop.</p>
                  )}
                  {!nodeAi?.paired && (
                    <p className="mt-2 text-xs text-amber-200">Complete Step 2 pairing first.</p>
                  )}
                  {ollama?.connected && settings.defaultProvider !== 'OLLAMA_LOCAL' && (
                    <button
                      type="button"
                      className="mt-3 rounded-lg bg-emerald-600 px-3 py-1.5 text-xs font-medium text-white"
                      onClick={() =>
                        onSaveSettings({
                          defaultProvider: 'OLLAMA_LOCAL',
                          preferredModel: nodeAi?.ollamaModel ?? 'llama3.2',
                        })
                      }
                    >
                      Set Ollama as default
                    </button>
                  )}
                  <details className="mt-3">
                    <summary className="cursor-pointer text-[11px] text-zinc-500 hover:text-zinc-300">
                      Advanced: direct Ollama URL (VPS only)
                    </summary>
                    <div className="mt-2 space-y-2">
                      <input
                        type="url"
                        value={ollamaUrl}
                        onChange={(e) => setOllamaUrl(e.target.value)}
                        placeholder="https://ollama.your-vps.com"
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
                          className={`rounded-lg px-3 py-2 text-sm font-medium text-white disabled:opacity-50 ${localStyle.button}`}
                        >
                          Connect URL
                        </button>
                      </div>
                    </div>
                  </details>
                </div>

                {/* Phala */}
                <div className="rounded-xl border border-fuchsia-500/30 bg-fuchsia-950/10 p-4">
                  <p className="font-medium text-white">Phala TEE (private cloud)</p>
                  <p className="mt-1 text-xs text-zinc-500">Confidential inference in hardware TEE — API key + optional URL.</p>
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
                    <div className="flex flex-wrap gap-2">
                      <input
                        value={phalaModel}
                        onChange={(e) => setPhalaModel(e.target.value)}
                        placeholder="phala/deepseek-chat-v3-0324"
                        className="min-w-0 flex-1 rounded-lg border border-zinc-700 bg-black px-3 py-2 text-sm"
                      />
                      <button
                        type="button"
                        disabled={connecting === 'phala'}
                        onClick={onConnectPhala}
                        className="rounded-lg bg-fuchsia-700 px-3 py-2 text-sm font-medium text-white disabled:opacity-50"
                      >
                        {phala?.connected ? 'Update' : 'Connect & activate'}
                      </button>
                      {phala?.connected && (
                        <button
                          type="button"
                          onClick={() => onDisconnectProvider('phala')}
                          className="rounded-lg border border-red-500/40 px-3 py-2 text-sm text-red-200"
                        >
                          Disconnect
                        </button>
                      )}
                    </div>
                    {phala?.connected && phalaStatus?.model && (
                      <p className="text-xs text-fuchsia-200/80">Connected · {phalaStatus.model}</p>
                    )}
                  </div>
                </div>
              </div>
            </div>
          );
        }

        const styleKey = cat.category === 'private' ? 'local' : cat.category;
        const style = CATEGORY_STYLES[styleKey as keyof typeof CATEGORY_STYLES];
        return (
          <div key={cat.category} className={`space-y-3 rounded-xl border p-4 ${style.border} ${style.bg}`}>
            <div>
              <h4 className="text-sm font-semibold text-zinc-200">{cat.label}</h4>
              <p className="text-[11px] text-zinc-500">
                {cat.category === 'marketplace'
                  ? 'One key, many models — routed by the gateway (often cheapest available).'
                  : 'Bill directly to OpenAI, Anthropic, Google, or DeepSeek.'}
              </p>
            </div>
            <div className="grid gap-4 lg:grid-cols-2">
              {cat.keys.map((key) => {
                const row = providerMap.get(key);
                if (!row?.credentialProvider) return null;
                const guide = AI_PROVIDER_GUIDES[key];
                return (
                  <ApiKeyCard
                    key={key}
                    providerKey={key}
                    row={row}
                    guide={guide}
                    apiKeyInput={apiKeyInput}
                    setApiKeyInput={setApiKeyInput}
                    connecting={connecting}
                    onConnect={() => onConnectProvider(row.credentialProvider!)}
                    onDisconnect={() => onDisconnectProvider(row.credentialProvider!)}
                    onSetDefault={() =>
                      onSaveSettings({
                        defaultProvider: key,
                        preferredModel: aiProviderConfig(key)?.defaultModel ?? undefined,
                      })
                    }
                    isDefault={settings.defaultProvider === key}
                  />
                );
              })}
            </div>
          </div>
        );
      })}

      <label className="block text-sm">
        <span className="text-zinc-400">Current goal focus</span>
        <input
          defaultValue={settings.currentGoalFocus ?? ''}
          onBlur={(e) => onSaveSettings({ currentGoalFocus: e.target.value.trim() || undefined })}
          placeholder="e.g. Ship referral system — syncs to your local vault"
          className="mt-1 w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-white"
        />
      </label>
    </div>
  );
}
