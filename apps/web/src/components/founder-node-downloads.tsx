'use client';

import { useEffect, useMemo, useState } from 'react';
import { FOUNDER_NODE_MIN_VERSION, FOUNDER_NODE_MIN_VERSION_LABEL } from '@/lib/founder-node-requirements';

const REPO = 'danishhaiderau-maker/doxed-founders-website';
export const FOUNDER_NODE_GITHUB_RELEASES = `https://github.com/${REPO}/releases/latest`;

/**
 * Founder IDE installer download — bundles Founder IDE (a VS Code-based
 * editor with built-in AI chat, routing, and memory injection through the
 * Founder OS AI Gateway) + Founder Node tray app in one download from GitHub
 * Releases.
 */
export const FOUNDER_IDE_RELEASES_URL = `https://github.com/${REPO}/releases`;

type ReleaseAsset = {
  name: string;
  browser_download_url: string;
};

type Props = {
  /** Show numbered install steps below download buttons */
  showInstallGuide?: boolean;
  /** Anchor id for deep-linking from workspace connect wizard */
  sectionId?: string;
};

export function FounderNodeInstallGuide() {
  return (
    <div className="text-xs text-zinc-300">
      <ol className="list-decimal space-y-2 pl-5">
        <li>
          Download the installer for your OS above. On <strong className="text-white">Windows</strong>, if
          sync fails the tray app will prompt you to{' '}
          <strong className="text-white">Allow Founder Node</strong> through the firewall (one UAC click).
        </li>
        <li>
          Launch from Start Menu / Applications / run the AppImage. Updates appear in the tray menu — no
          manual re-download on Windows.
        </li>
        <li>
          In <strong className="text-white">Pair your device</strong> below, select{' '}
          <strong className="text-white">Founder Vault (Founder Node)</strong> and click{' '}
          <strong className="text-white">Code for desktop</strong>.
        </li>
        <li>
          Tray icon → <strong className="text-white">Pair with Founder OS</strong> → paste the code. The pairing
          window can close — keep the tray app running. Pairing writes{' '}
          <code className="text-zinc-400">~/FounderVault/node-config.json</code>.
        </li>
        <li>
          Open <strong className="text-white">Founder IDE</strong> — it auto-loads the vault token and routes{' '}
          <strong className="text-white">@Founder OS</strong> chat through the AI Gateway (no GitHub/Google/Apple
          login inside the IDE). Or use tray <strong className="text-white">Connect Founder IDE</strong>.
        </li>
        <li>
          Complete <strong className="text-white">Sync, index & search</strong> —{' '}
          <strong className="text-white">Rebuild vector index</strong> once (first run up to ~2 minutes).
        </li>
        <li>
          Optional: install{' '}
          <a href="https://ollama.com" className="text-cyan-300 underline" target="_blank" rel="noreferrer">
            Ollama
          </a>{' '}
          for fully offline Copilot in the AI brain section.
        </li>
      </ol>
      <p className="mt-3 text-[11px] text-zinc-500">
        Vault: <code className="text-zinc-400">~/FounderVault/</code> — encrypted metadata sync only; plain-text
        notes stay local.
      </p>
    </div>
  );
}

function parseVersionFromTag(tag?: string): string | null {
  if (!tag) return null;
  const match = /^founder-node-v(\d+\.\d+\.\d+)$/i.exec(tag);
  return match?.[1] ?? null;
}

function detectOs(): 'windows' | 'mac' | 'linux' | 'unknown' {
  if (typeof navigator === 'undefined') return 'unknown';
  const ua = navigator.userAgent.toLowerCase();
  const platform = (navigator.platform ?? '').toLowerCase();
  if (/win/.test(platform) || ua.includes('windows')) return 'windows';
  if (/mac/.test(platform) || ua.includes('macintosh')) return 'mac';
  if (/linux/.test(platform) || ua.includes('linux')) return 'linux';
  return 'unknown';
}

export function FounderNodeDownloads({ showInstallGuide = false, sectionId = 'founder-node-download' }: Props) {
  const [winUrl, setWinUrl] = useState<string | null>(null);
  const [macUrl, setMacUrl] = useState<string | null>(null);
  const [linuxUrl, setLinuxUrl] = useState<string | null>(null);
  const [releaseVersion, setReleaseVersion] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const os = useMemo(() => detectOs(), []);

  useEffect(() => {
    fetch(`https://api.github.com/repos/${REPO}/releases?per_page=20`)
      .then((res) => (res.ok ? res.json() : null))
      .then((releases: Array<{ tag_name?: string; assets?: ReleaseAsset[] }> | null) => {
        const release = releases?.find((r) => r.tag_name?.startsWith('founder-node-v'));
        const assets = release?.assets ?? [];
        setReleaseVersion(parseVersionFromTag(release?.tag_name));
        setWinUrl(
          assets.find((a) => /\.exe$/i.test(a.name) && !/blockmap/i.test(a.name))
            ?.browser_download_url ?? null,
        );
        setMacUrl(assets.find((a) => /\.dmg$/i.test(a.name))?.browser_download_url ?? null);
        setLinuxUrl(
          assets.find((a) => /\.AppImage$/i.test(a.name))?.browser_download_url ??
            assets.find((a) => /\.deb$/i.test(a.name))?.browser_download_url ??
            null,
        );
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  const versionLabel = releaseVersion ?? FOUNDER_NODE_MIN_VERSION;
  const primaryLabel = os === 'unknown' ? null : 'Founder IDE — Windows beta';

  return (
    <div id={sectionId} className="scroll-mt-24 space-y-4">
      {/* Founder IDE — one application with embedded Founder Node. */}
      <div className="rounded-xl border border-emerald-500/30 bg-emerald-950/15 p-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="min-w-0">
            <p className="text-sm font-semibold text-emerald-100">Founder IDE</p>
            <p className="mt-0.5 text-xs text-zinc-400">
              One Windows application with Founder Node embedded for pairing, vault, local tools, and remote approval.
            </p>
            <p className="mt-1 text-[11px] text-zinc-500">
              The current desktop candidate is an unsigned internal beta. macOS and Linux Founder IDE packages are not yet release-proven.
            </p>
          </div>
          <a
            href={FOUNDER_IDE_RELEASES_URL}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-2 rounded-lg bg-emerald-500 px-4 py-2 text-sm font-semibold text-black hover:bg-emerald-400"
          >
            View Founder IDE beta
          </a>
        </div>
      </div>

      {/* Founder IDE beta — one application with embedded Founder Node. */}
      {primaryLabel && (
        <a
          href={FOUNDER_IDE_RELEASES_URL}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex w-full max-w-md items-center justify-center gap-2 rounded-lg bg-cyan-600 px-5 py-3 text-sm font-semibold text-white hover:bg-cyan-500 sm:w-auto"
        >
          {primaryLabel}
        </a>
      )}

      <p className="text-[11px] font-medium text-zinc-500">
        Founder Node is an embedded background capability, not a second founder-facing application.
      </p>

      <details className="rounded-lg border border-zinc-800 bg-zinc-950/40 p-3">
        <summary className="cursor-pointer text-xs font-semibold text-zinc-400">
          Legacy standalone Founder Node downloads
        </summary>
        <p className="mt-2 text-[11px] leading-5 text-zinc-500">
          Kept only for existing vault-only installations. New desktop founders should install Founder IDE.
        </p>
        <div className="mt-3 flex flex-wrap gap-3">
          <a
            href={winUrl ?? FOUNDER_NODE_GITHUB_RELEASES}
            target="_blank"
            rel="noopener noreferrer"
            className={`inline-flex items-center gap-2 rounded-lg px-5 py-2.5 text-sm font-medium ${
              os === 'windows' && primaryLabel
                ? 'border border-cyan-500/30 text-cyan-200/80'
                : 'bg-cyan-600 text-white hover:bg-cyan-500'
            }`}
          >
            Windows (.exe)
          </a>
          <a
            href={macUrl ?? FOUNDER_NODE_GITHUB_RELEASES}
            target="_blank"
            rel="noopener noreferrer"
            className={`inline-flex items-center gap-2 rounded-lg border px-5 py-2.5 text-sm font-medium ${
              os === 'mac'
                ? 'border-cyan-400/60 bg-cyan-950/40 text-cyan-50'
                : 'border-cyan-500/40 bg-cyan-950/30 text-cyan-100 hover:border-cyan-400/60'
            }`}
          >
            macOS (.dmg)
          </a>
          <a
            href={linuxUrl ?? FOUNDER_NODE_GITHUB_RELEASES}
            target="_blank"
            rel="noopener noreferrer"
            className={`inline-flex items-center gap-2 rounded-lg border px-5 py-2.5 text-sm font-medium ${
              os === 'linux'
                ? 'border-cyan-400/60 bg-cyan-950/40 text-cyan-50'
                : 'border-cyan-500/40 bg-cyan-950/30 text-cyan-100 hover:border-cyan-400/60'
            }`}
          >
            Linux / Ubuntu (.AppImage)
          </a>
        </div>

        <p className="mt-3 text-xs text-zinc-500">
          {loading
            ? 'Checking legacy release...'
            : winUrl || macUrl || linuxUrl
              ? `Legacy Node v${versionLabel}. Windows ${FOUNDER_NODE_MIN_VERSION_LABEL} or newer recommended.`
              : `Legacy installers on GitHub — ${FOUNDER_NODE_GITHUB_RELEASES}`}
        </p>
      </details>

      {showInstallGuide && (
        <div className="rounded-lg border border-cyan-500/25 bg-cyan-950/15 p-4">
          <p className="text-sm font-medium text-cyan-100">Installation (recommended order)</p>
          <div className="mt-3">
            <FounderNodeInstallGuide />
          </div>
        </div>
      )}
    </div>
  );
}
