'use client';

import Link from 'next/link';
import { WORKFORCE_TEMPLATES } from '@dcf/utils';
import { useEffect, useMemo, useState } from 'react';
import {
  BuilderSettings,
  fetchBuilderSettings,
  fetchBuilderWorkerStatus,
} from '@/lib/api';
import {
  listBuildWorkers,
  listChatProviders,
  shortProviderName,
  type ProviderRow,
} from '@/lib/copilot-ai-stack';

const CODING_AGENT_KEYS = new Set(['BUILDER', 'PRODUCT_MANAGER']);

const NORMAL_AGENTS = WORKFORCE_TEMPLATES.filter((t) => !CODING_AGENT_KEYS.has(t.key));
const CODING_AGENTS = WORKFORCE_TEMPLATES.filter((t) => CODING_AGENT_KEYS.has(t.key));

const GUEST_BRAIN_PROVIDERS = [
  'OpenRouter',
  'DeepSeek',
  'Claude',
  'OpenAI',
  'Gemini',
  'Ollama (local)',
  'Phala TEE',
] as const;

const GUEST_CODE_PROVIDERS = ['Cursor Cloud', 'OpenHands'] as const;

function isBrainProvider(p: ProviderRow) {
  return (
    p.key !== 'RULE_BASED' &&
    p.key !== 'CURSOR' &&
    p.key !== 'OPENHANDS' &&
    (p.connectMode === 'api_key' || p.connectMode === 'founder_node')
  );
}

function isCodeProvider(p: ProviderRow) {
  return p.key === 'CURSOR' || p.key === 'OPENHANDS';
}

function modelLabel(settings: BuilderSettings, providerKey: string): string | null {
  if (settings.defaultProvider === providerKey && settings.preferredModel?.trim()) {
    return settings.preferredModel.trim();
  }
  if (providerKey === 'OLLAMA_LOCAL') {
    const m =
      settings.founderNodeAi?.ollamaModel?.trim() ||
      settings.providers.find((p) => p.key === 'OLLAMA_LOCAL')?.defaultModel;
    if (m) return m;
  }
  if (providerKey === 'PHALA' && settings.phalaPrivateAi?.model) {
    return settings.phalaPrivateAi.model;
  }
  const row = settings.providers.find((p) => p.key === providerKey);
  return row?.defaultModel?.trim() || null;
}

function StackSection({
  title,
  subtitle,
  children,
}: {
  title: string;
  subtitle: string;
  children: React.ReactNode;
}) {
  return (
    <div className="rounded-lg border border-zinc-800/80 bg-black/25 p-2">
      <p className="text-[10px] font-semibold uppercase tracking-wide text-zinc-300">{title}</p>
      <p className="text-[9px] text-zinc-600">{subtitle}</p>
      <div className="mt-1.5 space-y-1">{children}</div>
    </div>
  );
}

function ConnectedRow({ name, detail }: { name: string; detail?: string }) {
  return (
    <div className="flex items-start justify-between gap-2 rounded border border-emerald-500/25 bg-emerald-950/20 px-2 py-1">
      <span className="text-[10px] font-medium text-emerald-100">{name}</span>
      {detail ? <span className="shrink-0 text-right text-[9px] text-emerald-300/80">{detail}</span> : null}
    </div>
  );
}

function AvailableRow({ name }: { name: string }) {
  return (
    <div className="flex items-center justify-between rounded border border-zinc-800 px-2 py-1">
      <span className="text-[10px] text-zinc-500">{name}</span>
      <span className="text-[9px] text-zinc-600">Not connected</span>
    </div>
  );
}

function AgentRow({
  label,
  ready,
  needs,
}: {
  label: string;
  ready: boolean;
  needs: string;
}) {
  return (
    <div
      className={`flex items-center justify-between rounded px-2 py-1 text-[10px] ${
        ready ? 'border border-violet-500/20 bg-violet-950/25 text-violet-100' : 'border border-zinc-800 text-zinc-500'
      }`}
    >
      <span>{label}</span>
      <span className={`text-[9px] ${ready ? 'text-emerald-400' : 'text-amber-400/90'}`}>
        {ready ? 'Ready' : needs}
      </span>
    </div>
  );
}

export function LandingAiStackPreview({ accessToken }: { accessToken?: string }) {
  const [settings, setSettings] = useState<BuilderSettings | null>(null);
  const [codeWorkers, setCodeWorkers] = useState<{ cursor: boolean; openHands: boolean } | null>(null);
  const [loading, setLoading] = useState(!!accessToken);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!accessToken) {
      setLoading(false);
      return;
    }
    let cancelled = false;
    (async () => {
      setLoading(true);
      setError(null);
      try {
        const [s, w] = await Promise.all([
          fetchBuilderSettings(accessToken),
          fetchBuilderWorkerStatus(accessToken),
        ]);
        if (!cancelled) {
          setSettings(s);
          setCodeWorkers(w.connections);
        }
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : 'Could not load AI stack');
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [accessToken]);

  const parsed = useMemo(() => {
    if (!settings) return null;
    const providers = settings.providers as ProviderRow[];
    const { connected: brainConnected, defaultChat } = listChatProviders(
      providers,
      settings.defaultProvider,
    );
    const brainDisconnected = providers.filter((p) => isBrainProvider(p) && !p.connected);
    const codeConnected = providers.filter((p) => isCodeProvider(p) && p.connected);
    const codeDisconnected = providers.filter((p) => isCodeProvider(p) && !p.connected);
    const buildWorkers = listBuildWorkers(codeWorkers ?? {});
    const defaultBrainName = defaultChat ? shortProviderName(defaultChat) : null;
    const defaultModel =
      defaultChat && modelLabel(settings, defaultChat.key)
        ? modelLabel(settings, defaultChat.key)
        : settings.preferredModel?.trim() || null;
    const llmReady = brainConnected.length > 0;
    const codeReady = buildWorkers.length > 0;
    return {
      brainConnected,
      brainDisconnected,
      codeConnected,
      codeDisconnected,
      buildWorkers,
      defaultBrainName,
      defaultModel,
      llmReady,
      codeReady,
    };
  }, [settings, codeWorkers]);

  if (!accessToken) {
    return (
      <div className="space-y-2">
        <StackSection
          title="Brain — project agents"
          subtitle="Community, marketing, research (your LLM)"
        >
          {NORMAL_AGENTS.map((a) => (
            <AgentRow key={a.key} label={a.label} ready={false} needs="Sign in + connect LLM" />
          ))}
        </StackSection>
        <StackSection title="Code — repo agents" subtitle="Builder & PM use Cursor / OpenHands">
          {CODING_AGENTS.map((a) => (
            <AgentRow key={a.key} label={a.label} ready={false} needs="Sign in + connect code worker" />
          ))}
        </StackSection>
        <p className="text-[10px] text-zinc-500">
          <Link href="/login?callbackUrl=/?#hub" className="text-sky-300 underline">
            Sign in
          </Link>{' '}
          to see which models and agents are connected to your account.
        </p>
        <div className="grid grid-cols-2 gap-2 text-[9px]">
          <div>
            <p className="mb-1 font-semibold text-zinc-500">Can connect (brain)</p>
            {GUEST_BRAIN_PROVIDERS.map((n) => (
              <AvailableRow key={n} name={n} />
            ))}
          </div>
          <div>
            <p className="mb-1 font-semibold text-zinc-500">Can connect (code)</p>
            {GUEST_CODE_PROVIDERS.map((n) => (
              <AvailableRow key={n} name={n} />
            ))}
          </div>
        </div>
      </div>
    );
  }

  if (loading) {
    return <p className="text-[11px] text-zinc-500">Loading your AI stack…</p>;
  }

  if (error || !parsed || !settings) {
    return (
      <p className="text-[11px] text-zinc-500">
        {error ?? 'Could not load stack.'}{' '}
        <Link href="/settings/builder" className="text-sky-300 underline">
          Open AI Stack
        </Link>
      </p>
    );
  }

  const {
    brainConnected,
    brainDisconnected,
    codeConnected,
    codeDisconnected,
    buildWorkers,
    defaultBrainName,
    defaultModel,
    llmReady,
    codeReady,
  } = parsed;

  return (
    <div className="space-y-2">
      {defaultBrainName && (
        <p className="text-[10px] text-sky-200/90">
          Default brain: <span className="font-semibold text-white">{defaultBrainName}</span>
          {defaultModel ? (
            <span className="text-zinc-400">
              {' '}
              · model <span className="text-zinc-300">{defaultModel}</span>
            </span>
          ) : null}
        </p>
      )}

      <StackSection
        title="Brain — project agents"
        subtitle="Use your LLM for replies, marketing, research"
      >
        {NORMAL_AGENTS.map((a) => (
          <AgentRow
            key={a.key}
            label={a.label}
            ready={llmReady}
            needs="Connect an LLM below"
          />
        ))}
        {brainConnected.length === 0 ? (
          <p className="text-[10px] text-amber-300/90">No LLM connected yet.</p>
        ) : (
          brainConnected.map((p) => {
            const model = modelLabel(settings, p.key);
            const isDefault = p.key === settings.defaultProvider;
            return (
              <ConnectedRow
                key={p.key}
                name={`${shortProviderName(p)}${isDefault ? ' (default)' : ''}`}
                detail={model ?? undefined}
              />
            );
          })
        )}
      </StackSection>

      <StackSection title="Code — repo agents" subtitle="Edits GitHub via Cursor or OpenHands">
        {CODING_AGENTS.map((a) => (
          <AgentRow
            key={a.key}
            label={a.label}
            ready={codeReady}
            needs="Connect Cursor or OpenHands"
          />
        ))}
        {codeConnected.length === 0 && buildWorkers.length === 0 ? (
          <p className="text-[10px] text-amber-300/90">No code worker connected.</p>
        ) : (
          (buildWorkers.length > 0
            ? buildWorkers.map((w) => <ConnectedRow key={w.key} name={w.label} />)
            : codeConnected.map((p) => (
                <ConnectedRow key={p.key} name={shortProviderName(p)} />
              )))
        )}
      </StackSection>

      {(brainDisconnected.length > 0 || codeDisconnected.length > 0) && (
        <StackSection title="Available to connect" subtitle="Add keys in AI Stack settings">
          {brainDisconnected.map((p) => (
            <AvailableRow key={p.key} name={shortProviderName(p)} />
          ))}
          {codeDisconnected.map((p) => (
            <AvailableRow key={p.key} name={shortProviderName(p)} />
          ))}
        </StackSection>
      )}
    </div>
  );
}
