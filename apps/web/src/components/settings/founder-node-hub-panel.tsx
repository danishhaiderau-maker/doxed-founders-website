'use client';

import type { ReactNode } from 'react';
import type { MemoryStorageModeKey } from '@dcf/utils';
import type { BuilderSettings } from '@/lib/api';
import { FounderNodeDownloads } from '@/components/founder-node-downloads';
import { MemoryStoragePanel } from '@/components/memory-storage-panel';
import { FounderNodeV2Panel } from '@/components/settings/founder-node-v2-panel';
import { AttestationDashboardPanel } from '@/components/settings/attestation-dashboard-panel';
import { FounderNodeAiSection } from '@/components/settings/founder-node-ai-section';
import { PlatformSetupGuide } from '@/components/settings/platform-setup-guide';

function HubStep({
  step,
  title,
  summary,
  id,
  children,
}: {
  step: number;
  title: string;
  summary: string;
  id?: string;
  children: ReactNode;
}) {
  return (
    <div id={id} className="rounded-xl border border-cyan-500/20 bg-zinc-950/40 p-5 scroll-mt-24">
      <div className="flex items-start gap-3">
        <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-cyan-500/25 text-sm font-bold text-cyan-100">
          {step}
        </span>
        <div className="min-w-0 flex-1">
          <h3 className="font-semibold text-white">{title}</h3>
          <p className="mt-0.5 text-xs text-zinc-500">{summary}</p>
          <div className="mt-4">{children}</div>
        </div>
      </div>
    </div>
  );
}

type AiSectionProps = React.ComponentProps<typeof FounderNodeAiSection>;

type Props = {
  accessToken: string;
  settings: BuilderSettings;
  onRefresh: () => void;
  memoryMode: MemoryStorageModeKey;
  onMemoryModeChange: (mode: MemoryStorageModeKey) => void;
  aiSection: AiSectionProps;
};

export function FounderNodeHubPanel({
  accessToken,
  settings,
  onRefresh,
  memoryMode,
  onMemoryModeChange,
  aiSection,
}: Props) {
  const v2 = settings.founderNodeV2;
  const paired = v2?.paired ?? false;
  const online = v2?.online ?? false;

  return (
    <section className="rounded-2xl border border-cyan-500/40 bg-gradient-to-b from-cyan-950/20 to-zinc-950/30 p-6 shadow-lg shadow-cyan-950/20">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h2 className="text-xl font-semibold text-white">Founder Node</h2>
          <p className="mt-1 max-w-2xl text-sm text-zinc-400">
            One place for your local vault, private AI, and attestation — download the desktop app, pair once,
            connect your models, and verify memory integrity. No need to jump between pages.
          </p>
        </div>
        <div className="flex flex-wrap gap-2 text-[10px] font-semibold">
          <span className={`rounded-full px-2.5 py-1 ${paired ? 'bg-emerald-500/20 text-emerald-200' : 'bg-zinc-700/60 text-zinc-400'}`}>
            {paired ? 'Paired' : 'Not paired'}
          </span>
          <span className={`rounded-full px-2.5 py-1 ${online ? 'bg-emerald-500/20 text-emerald-200' : 'bg-zinc-700/60 text-zinc-400'}`}>
            {online ? 'Online' : 'Offline'}
          </span>
          {v2?.appVersion && (
            <span className="rounded-full bg-zinc-700/60 px-2.5 py-1 text-zinc-300">v{v2.appVersion}</span>
          )}
        </div>
      </div>

      <div className="mt-6 space-y-5">
        <PlatformSetupGuide />

        <HubStep
          step={1}
          title="Download & install"
          summary="Windows installer with auto-update — runs in your system tray. v0.5.0+ recommended."
        >
          <FounderNodeDownloads showInstallGuide />
        </HubStep>

        <HubStep
          step={2}
          title="Vault memory & pairing"
          summary="Choose Founder Vault mode, generate a pairing code, and connect the tray app."
        >
          <MemoryStoragePanel
            embedded
            accessToken={accessToken}
            currentMode={memoryMode}
            onModeChange={onMemoryModeChange}
            phalaPrivateAi={settings.phalaPrivateAi}
          />
        </HubStep>

        <HubStep
          step={3}
          title="AI on your stack"
          summary="Your connected LLM is the brain for Copilot Ask and all project agents — Cursor is only for coding."
        >
          <FounderNodeAiSection {...aiSection} settings={settings} />
        </HubStep>

        <HubStep
          step={4}
          title="Sync, index & search"
          summary="Rebuild your local vector index and search vault files on your machine."
        >
          <FounderNodeV2Panel embedded accessToken={accessToken} settings={settings} onRefresh={onRefresh} />
        </HubStep>

        <HubStep
          step={5}
          title="Privacy attestation"
          summary="Cryptographic proof of vault integrity and Phala TEE inference receipts."
          id="founder-attestation"
        >
          <AttestationDashboardPanel embedded accessToken={accessToken} />
        </HubStep>
      </div>
    </section>
  );
}
