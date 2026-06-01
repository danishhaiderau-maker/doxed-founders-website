'use client';

import { useEffect, useState } from 'react';

const REPO = 'danishhaiderau-maker/doxed-founders-website';
const RELEASES_PAGE = `https://github.com/${REPO}/releases/latest`;
const APP_VERSION = '0.4.0';

type ReleaseAsset = {
  name: string;
  browser_download_url: string;
};

export function FounderNodeDownloads() {
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
            : `Installers for v${APP_VERSION} will appear here after the first GitHub release. Until then, use the developer setup below.`}
      </p>
    </div>
  );
}
