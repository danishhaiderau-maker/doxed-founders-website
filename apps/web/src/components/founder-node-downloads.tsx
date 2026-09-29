/**
 * @deprecated The legacy install panel. Superseded by the /founder-ide page
 * (apps/web/src/app/founder-ide/page.tsx) and the founder-ide-pair component.
 * Kept for existing Settings/onboarding placements; it resolves the same
 * public Founder IDE release channel as /founder-ide.
 */
'use client';

import { useEffect, useMemo, useState } from 'react';
import { FOUNDER_IDE_RELEASES_REPO, FOUNDER_IDE_WINDOWS_DOWNLOAD_URL } from '@/lib/founder-ide-download';

export const FOUNDER_NODE_GITHUB_RELEASES = FOUNDER_IDE_WINDOWS_DOWNLOAD_URL;

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
  return /^v(\d+\.\d+\.\d+)$/i.exec(tag ?? '')?.[1] ?? null;
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
  const [releaseVersion, setReleaseVersion] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const os = useMemo(() => detectOs(), []);

  useEffect(() => {
    fetch(`https://api.github.com/repos/${FOUNDER_IDE_RELEASES_REPO}/releases/latest`)
      .then((res) => (res.ok ? res.json() : null))
      .then((release: { tag_name?: string; assets?: ReleaseAsset[] } | null) => {
        setReleaseVersion(parseVersionFromTag(release?.tag_name));
        setWinUrl(
          release?.assets?.find((a) => /\.exe$/i.test(a.name) && !/blockmap/i.test(a.name))
            ?.browser_download_url ?? null,
        );
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  const windowsLabel = releaseVersion
    ? `Download Founder IDE for Windows — v${releaseVersion}`
    : 'Download Founder IDE for Windows';

  return (
    <div id={sectionId} className="scroll-mt-24 space-y-4">
      <div className="rounded-xl border border-emerald-500/30 bg-emerald-950/15 p-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="min-w-0">
            <p className="text-sm font-semibold text-emerald-100">Founder IDE</p>
            <p className="mt-0.5 text-xs text-zinc-400">
              The Founder IDE desktop app, published with its installer and SHA256SUMS on GitHub releases.
            </p>
          </div>
          <a
            href={winUrl ?? FOUNDER_NODE_GITHUB_RELEASES}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-2 rounded-lg bg-emerald-500 px-4 py-2 text-sm font-semibold text-black hover:bg-emerald-400"
          >
            {windowsLabel}
          </a>
        </div>
      </div>

      {os !== 'windows' && os !== 'unknown' && (
        <p className="text-xs text-zinc-500">
          Founder IDE is currently published for Windows only. macOS and Linux installers are not available yet.
        </p>
      )}

      <p className="text-xs text-zinc-500">
        {loading
          ? 'Checking latest release…'
          : releaseVersion
            ? `Latest public release: v${releaseVersion}. Verify the installer against SHA256SUMS on the release page.`
            : `Installers on GitHub — ${FOUNDER_NODE_GITHUB_RELEASES}`}
      </p>

      <a
        href={FOUNDER_NODE_GITHUB_RELEASES}
        target="_blank"
        rel="noopener noreferrer"
        className="inline-block text-xs text-cyan-400/80 underline hover:text-cyan-300"
      >
        Or open the latest release on GitHub
      </a>

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
