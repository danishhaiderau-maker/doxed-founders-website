'use client';

import { useEffect, useState } from 'react';

const REPO = 'danishhaiderau-maker/doxed-founders-website';
const RELEASES_PAGE = `https://github.com/${REPO}/releases/latest`;
const APP_VERSION = '0.4.0';

type ReleaseAsset = {
  name: string;
  browser_download_url: string;
};

type Props = {
  /** Show numbered install steps below download buttons */
  showInstallGuide?: boolean;
};

export function FounderNodeDownloads({ showInstallGuide = false }: Props) {
  const [winUrl, setWinUrl] = useState<string | null>(null);
  const [macUrl, setMacUrl] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetch(`https://api.github.com/repos/${REPO}/releases/latest`)
      .then((res) => (res.ok ? res.json() : null))
      .then((release: { assets?: ReleaseAsset[] } | null) => {
        const assets = release?.assets ?? [];
        setWinUrl(assets.find((a) => /\.exe$/i.test(a.name))?.browser_download_url ?? null);
        setMacUrl(assets.find((a) => /\.dmg$/i.test(a.name))?.browser_download_url ?? null);
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  const winHref = winUrl ?? RELEASES_PAGE;
  const macHref = macUrl ?? RELEASES_PAGE;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-3">
        <a
          href={winHref}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-2 rounded-lg bg-cyan-600 px-5 py-2.5 text-sm font-medium text-white hover:bg-cyan-500"
        >
          Download for Windows (.exe)
        </a>
        <a
          href={macHref}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-2 rounded-lg border border-cyan-500/40 bg-cyan-950/30 px-5 py-2.5 text-sm font-medium text-cyan-100 hover:border-cyan-400"
        >
          Download for macOS (.dmg)
        </a>
      </div>
      <p className="text-xs text-zinc-500">
        {loading
          ? 'Checking latest release…'
          : winUrl || macUrl
            ? `Version ${APP_VERSION} — one-click install, no Node.js required.`
            : `Installers for v${APP_VERSION} on GitHub releases. Until then use ${RELEASES_PAGE}.`}
      </p>

      {showInstallGuide && (
        <div className="rounded-lg border border-cyan-500/25 bg-cyan-950/15 p-4">
          <p className="text-sm font-medium text-cyan-100">Installation (recommended order)</p>
          <ol className="mt-3 list-decimal space-y-2 pl-5 text-xs text-zinc-300">
            <li>
              Download <strong className="text-white">Founder-Node-{APP_VERSION}-win-x64.exe</strong> and run it —
              allow through Windows Firewall if prompted.
            </li>
            <li>Launch from the system tray (bottom-right). The app runs in the background.</li>
            <li>
              In <strong className="text-white">Step 2 below</strong>, select <strong className="text-white">Founder Vault (Founder Node)</strong> and
              click <strong className="text-white">Generate pairing code</strong>.
            </li>
            <li>
              Right-click the tray icon → <strong className="text-white">Pair with Founder OS</strong> → paste the code.
            </li>
            <li>
              Complete <strong className="text-white">Step 4</strong> — click <strong className="text-white">Rebuild vector index</strong> once
              (first run can take up to 2 minutes).
            </li>
            <li>
              Optional: install{' '}
              <a href="https://ollama.com" className="text-cyan-300 underline" target="_blank" rel="noreferrer">
                Ollama
              </a>{' '}
              locally for fully offline Copilot in Step 3.
            </li>
          </ol>
          <p className="mt-3 text-[11px] text-zinc-500">
            Vault files live at <code className="text-zinc-400">~/FounderVault/</code> on your machine — never uploaded in plain text.
          </p>
        </div>
      )}
    </div>
  );
}
