'use client';

import type { ReactNode } from 'react';
import type { MemoryStorageModeKey } from '@dcf/utils';
import type { BuilderSettings } from '@/lib/api';
import {
  FounderNodeDownloads,
  FounderNodeInstallGuide,
} from '@/components/founder-node-downloads';
import { MemoryStoragePanel } from '@/components/memory-storage-panel';
import { FounderNodeV2Panel } from '@/components/settings/founder-node-v2-panel';
import { AttestationDashboardPanel } from '@/components/settings/attestation-dashboard-panel';
import { PhalaCvmSealPanel } from '@/components/settings/phala-cvm-seal-panel';
import { PhalaCvmVaultPanel } from '@/components/settings/phala-cvm-vault-panel';
import { SealedSecretsPanel } from '@/components/settings/sealed-secrets-panel';
import { FounderNodeAiSection } from '@/components/settings/founder-node-ai-section';
import { PlatformSetupGuide } from '@/components/settings/platform-setup-guide';
import { CollapsibleInfo } from '@/components/ui/collapsible-info';
import { FOUNDER_NODE_MIN_VERSION_LABEL } from '@/lib/founder-node-requirements';

function StatusCard({ label, value, ok }: { label: string; value: string; ok?: boolean }) {
  return (
    <div className="rounded-xl border border-white/5 bg-white/[0.03] px-4 py-3">
      <p className="text-[10px] font-semibold uppercase tracking-wider text-zinc-500">{label}</p>
      <p className={`mt-1 text-sm font-semibold ${ok ? 'text-emerald-300' : 'text-zinc-300'}`}>{value}</p>
    </div>
  );
}

function HelpSection({ title, hint, children, id }: { title: string; hint?: string; children: ReactNode; id?: string }) {
  return (
    <div id={id} className="scroll-mt-24">
      <CollapsibleInfo title={title} hint={hint} accent="cyan">
        {children}
      </CollapsibleInfo>
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
  /**
   * When false, Step 3 (AI providers) is hidden and the remaining steps are
   * renumbered 1–4. Used by the tabbed Settings page, which surfaces AI
   * providers in their own hub.
   */
  showAiSection?: boolean;
};

export function FounderNodeHubPanel({
  accessToken,
  settings,
  onRefresh,
  memoryMode,
  onMemoryModeChange,
  aiSection,
  showAiSection = true,
}: Props) {
  const v2 = settings.founderNodeV2;
  const paired = v2?.paired ?? false;
  const online = v2?.online ?? false;
  const versionLabel = v2?.appVersion ? `v${v2.appVersion}` : FOUNDER_NODE_MIN_VERSION_LABEL;

  return (
    <section className="rounded-2xl border border-cyan-500/40 bg-gradient-to-b from-cyan-950/20 to-[#0a0a0f] p-6 shadow-lg shadow-cyan-950/20">
      {/* Hero + status */}
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h2 className="text-xl font-semibold text-white">Founder Node</h2>
          <p className="mt-1 max-w-2xl text-sm text-zinc-400">
            Download, pair once, connect your AI brain — vault stays on your machine.
          </p>
        </div>
        <div className="flex flex-wrap gap-2 text-[10px] font-semibold">
          <span
            className={`rounded-full px-2.5 py-1 ${paired ? 'bg-emerald-500/20 text-emerald-200' : 'bg-zinc-700/60 text-zinc-400'}`}
          >
            {paired ? 'Paired' : 'Not paired'}
          </span>
          <span
            className={`rounded-full px-2.5 py-1 ${online ? 'bg-emerald-500/20 text-emerald-200' : 'bg-zinc-700/60 text-zinc-400'}`}
          >
            {online ? 'Online' : 'Offline'}
          </span>
          <span className="rounded-full bg-zinc-700/60 px-2.5 py-1 text-zinc-300">{versionLabel}</span>
        </div>
      </div>

      {/* Primary CTAs — download */}
      <div className="mt-6 rounded-xl border border-emerald-500/25 bg-emerald-950/10 p-5">
        <p className="text-sm font-semibold text-emerald-100">Download Founder Node</p>
        <p className="mt-0.5 text-xs text-zinc-500">Windows installer with auto-update — {FOUNDER_NODE_MIN_VERSION_LABEL} recommended.</p>
        <div className="mt-4">
          <FounderNodeDownloads />
        </div>
      </div>

      {/* Quick status cards */}
      <div className="mt-4 grid gap-3 sm:grid-cols-3">
        <StatusCard label="Paired" value={paired ? 'Yes' : 'No'} ok={paired} />
        <StatusCard label="Node status" value={online ? 'Online' : 'Offline'} ok={online} />
        <StatusCard label="App version" value={versionLabel} />
      </div>

      {/* Pairing — actions always visible */}
      <div className="mt-5 rounded-xl border border-cyan-500/20 bg-zinc-950/40 p-5">
        <h3 className="font-semibold text-white">Pair your device</h3>
        <p className="mt-0.5 text-xs text-zinc-500">
          Choose vault mode, then generate a pairing code for desktop or Android.
        </p>
        <div className="mt-4">
          <MemoryStoragePanel
            embedded
            accessToken={accessToken}
            currentMode={memoryMode}
            onModeChange={onMemoryModeChange}
            phalaPrivateAi={settings.phalaPrivateAi}
          />
        </div>
      </div>

      {/* AI brain — active status + connect buttons stay visible */}
      {showAiSection && (
        <div id="connect-ai" className="mt-5 scroll-mt-24 rounded-xl border border-violet-500/20 bg-zinc-950/40 p-5">
          <h3 className="font-semibold text-white">Connect AI brain</h3>
          <p className="mt-0.5 text-xs text-zinc-500">
            Surplus, OpenRouter, OpenAI, Anthropic, Gemini, Ollama — connect and set your default brain.
          </p>
          <div className="mt-4">
            <FounderNodeAiSection {...aiSection} settings={settings} />
          </div>
        </div>
      )}

      {/* Collapsible help sections */}
      <div className="mt-6 space-y-2">
        <p className="text-[10px] font-semibold uppercase tracking-[0.2em] text-zinc-500">Need help?</p>

        <HelpSection title="📥 Download & install" hint="6 steps">
          <FounderNodeInstallGuide />
        </HelpSection>

        <PlatformSetupGuide />

        <HelpSection title="🤖 Code agent (Cursor)" hint="Remote IDE control">
          <p className="text-xs text-zinc-400">
            <strong className="text-zinc-200">Cursor</strong> or <strong className="text-zinc-200">OpenHands</strong>{' '}
            is separate from the brain — it edits your GitHub repo. In Copilot use{' '}
            <strong className="text-zinc-200">Run in Cursor</strong>; output streams in Development Workspace.
          </p>
        </HelpSection>

        <HelpSection title="📢 Ship in public" hint="Build feed & autopilot">
          <p className="text-xs text-zinc-400">
            Publish build updates, enable Autopilot in Remote builder agents for hands-free sync + deploy. Say
            &quot;take full control&quot; in Copilot when stack tokens are connected.
          </p>
        </HelpSection>

        <HelpSection title="Sync, index & search" hint="Vault search & agents">
          <FounderNodeV2Panel embedded accessToken={accessToken} settings={settings} onRefresh={onRefresh} />
        </HelpSection>

        <HelpSection title="Privacy attestation" hint="TEE receipts & sealed keys" id="founder-attestation">
          <div className="space-y-4">
            <SealedSecretsPanel settings={settings} />
            <PhalaCvmSealPanel
              embedded
              accessToken={accessToken}
              cvmUnwrapReadyFromSettings={settings.secretsStatus?.cvmUnwrapReady}
              activeUnwrapPathLabel={settings.secretsStatus?.activeUnwrapPathLabel}
            />
            <PhalaCvmVaultPanel embedded accessToken={accessToken} />
            <AttestationDashboardPanel embedded accessToken={accessToken} />
          </div>
        </HelpSection>
      </div>
    </section>
  );
}
