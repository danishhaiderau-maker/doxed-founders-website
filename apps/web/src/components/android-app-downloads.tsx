'use client';

import { useEffect, useMemo, useState } from 'react';
import {
  ANDROID_APK_MIN_VERSION,
  ANDROID_APK_OPTIMAL,
  ANDROID_APK_RECOMMENDED,
  ANDROID_PLAY_PROTECT_NOTE,
} from '@/lib/android-app-requirements';

const REPO = 'danishhaiderau-maker/doxed-founders-website';
const SITE_APK = '/downloads/doxxedcrypto-android.apk';
const RELEASES_PAGE = `https://github.com/${REPO}/releases`;

type ReleaseAsset = {
  name: string;
  browser_download_url: string;
};

type Props = {
  /** Larger primary button (landing hero) */
  variant?: 'default' | 'hero' | 'landing-cta';
  showInstallGuide?: boolean;
};

function parseAndroidVersion(tag?: string): string | null {
  if (!tag) return null;
  const match = /^android-app-v(\d+\.\d+\.\d+)$/i.exec(tag);
  return match?.[1] ?? null;
}

function detectMobileOs(): 'android' | 'ios' | 'desktop' {
  if (typeof navigator === 'undefined') return 'desktop';
  const ua = navigator.userAgent.toLowerCase();
  if (/android/.test(ua)) return 'android';
  if (/iphone|ipad|ipod/.test(ua)) return 'ios';
  return 'desktop';
}

export function AndroidAppDownloads({ variant = 'default', showInstallGuide = false }: Props) {
  const [githubApkUrl, setGithubApkUrl] = useState<string | null>(null);
  const [releaseVersion, setReleaseVersion] = useState<string>('0.4.2');
  const [loading, setLoading] = useState(true);

  const mobileOs = useMemo(() => detectMobileOs(), []);

  useEffect(() => {
    fetch(`https://api.github.com/repos/${REPO}/releases?per_page=30`)
      .then((res) => (res.ok ? res.json() : null))
      .then((releases: Array<{ tag_name?: string; assets?: ReleaseAsset[] }> | null) => {
        const release = releases?.find((r) => r.tag_name?.startsWith('android-app-v'));
        const assets = release?.assets ?? [];
        const ver = parseAndroidVersion(release?.tag_name);
        if (ver) setReleaseVersion(ver);
        setGithubApkUrl(
          assets.find((a) => /\.apk$/i.test(a.name) && !/blockmap/i.test(a.name))
            ?.browser_download_url ?? null,
        );
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  const primaryHref = SITE_APK;
  const mirrorHref = githubApkUrl ?? `${RELEASES_PAGE}/latest`;

  const primaryClass =
    variant === 'hero'
      ? 'inline-flex w-full max-w-md items-center justify-center gap-2 rounded-xl bg-emerald-600 px-6 py-3.5 text-base font-semibold text-white shadow-lg shadow-emerald-950/40 hover:bg-emerald-500 sm:w-auto'
      : variant === 'landing-cta'
        ? 'inline-flex flex-col rounded-xl border border-emerald-500/50 bg-emerald-950/35 px-5 py-2.5 text-sm font-semibold text-emerald-50 shadow-lg shadow-emerald-950/30 hover:bg-emerald-900/40'
        : 'inline-flex w-full max-w-md items-center justify-center gap-2 rounded-lg bg-emerald-600 px-5 py-3 text-sm font-semibold text-white hover:bg-emerald-500 sm:w-auto';

  const requirementsBlock = (
    <div className="rounded-lg border border-zinc-700 bg-zinc-950/60 p-4 text-xs text-zinc-300">
      <p className="font-semibold text-white">Supported Android versions</p>
      <ul className="mt-2 list-disc space-y-1 pl-4 text-zinc-400">
        <li>
          <strong className="text-zinc-200">Minimum:</strong> {ANDROID_APK_MIN_VERSION}
        </li>
        <li>
          <strong className="text-zinc-200">Recommended:</strong> {ANDROID_APK_RECOMMENDED}
        </li>
        <li>
          <strong className="text-zinc-200">Best experience:</strong> {ANDROID_APK_OPTIMAL} (e.g. Pixel 8)
        </li>
      </ul>
    </div>
  );

  const playProtectBlock = (
    <div className="rounded-lg border border-amber-500/30 bg-amber-950/20 p-4 text-xs text-amber-100/90">
      <p className="font-semibold text-amber-100">Google Play Protect (Pixel / Samsung)</p>
      <p className="mt-2 leading-relaxed text-amber-100/85">{ANDROID_PLAY_PROTECT_NOTE}</p>
      <ol className="mt-3 list-decimal space-y-1.5 pl-4 text-amber-100/80">
        <li>After download, open the APK → if blocked, tap <strong className="text-white">More details</strong>.</li>
        <li>
          Tap <strong className="text-white">Install anyway</strong> (v0.4.2+ uses release signing, not debug).
        </li>
        <li>
          If enrolled in <strong className="text-white">Advanced Protection</strong>, Android may block all sideloads —
          use <a href="https://doxxedcrypto.digital/discover" className="underline">Chrome</a> instead until Play Store
          testing opens.
        </li>
      </ol>
    </div>
  );

  if (variant === 'landing-cta') {
    return (
      <a href={primaryHref} download className={primaryClass}>
        Download Android APK
        <span className="mt-0.5 block text-[10px] font-normal text-emerald-200/70">
          v{releaseVersion} · {ANDROID_APK_RECOMMENDED}
        </span>
      </a>
    );
  }

  return (
    <div className="space-y-4">
      {mobileOs === 'android' && (
        <p className="rounded-lg border border-emerald-500/30 bg-emerald-950/25 px-3 py-2 text-xs text-emerald-100">
          Android detected — tap below to install. Works on Pixel 8 and most phones on {ANDROID_APK_RECOMMENDED}.
        </p>
      )}
      {mobileOs === 'ios' && (
        <p className="rounded-lg border border-zinc-700 bg-zinc-900/50 px-3 py-2 text-xs text-zinc-400">
          iOS app coming later. Use Safari at{' '}
          <a href="https://doxxedcrypto.digital" className="text-cyan-300 underline">
            doxxedcrypto.digital
          </a>{' '}
          for full Founder OS.
        </p>
      )}

      <a href={primaryHref} download className={primaryClass}>
        Download Android app (.apk) — v{releaseVersion}
      </a>
      <p className="text-xs text-zinc-500">
        Supports {ANDROID_APK_MIN_VERSION} · recommended {ANDROID_APK_RECOMMENDED} · best on {ANDROID_APK_OPTIMAL}
      </p>

      {requirementsBlock}
      {playProtectBlock}

      <div className="flex flex-wrap gap-3">
        <a
          href={mirrorHref}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-2 rounded-lg border border-emerald-500/40 bg-emerald-950/30 px-5 py-2.5 text-sm font-medium text-emerald-100 hover:border-emerald-400/60"
        >
          Mirror on GitHub
        </a>
        <a
          href="https://doxxedcrypto.digital/discover?app=android"
          className="inline-flex items-center gap-2 rounded-lg border border-zinc-600 px-5 py-2.5 text-sm font-medium text-zinc-200 hover:border-zinc-500"
        >
          Open in browser
        </a>
      </div>

      <p className="text-xs text-zinc-500">
        {loading
          ? 'Checking GitHub release…'
          : `~4 MB WebView shell — release-signed APK; full platform loads online after install.`}
      </p>

      {showInstallGuide && (
        <div className="rounded-lg border border-emerald-500/25 bg-emerald-950/15 p-4">
          <p className="text-sm font-medium text-emerald-100">Install on Android</p>
          <ol className="mt-3 list-decimal space-y-2 pl-5 text-xs text-zinc-300">
            <li>Download the APK using the button above (same file on this site and GitHub releases).</li>
            <li>
              Open the file → allow install from this source if prompted (
              <strong className="text-white">Settings → Apps → Special access → Install unknown apps</strong>).
            </li>
            <li>
              If Play Protect appears, follow the amber box above — <strong className="text-white">Install anyway</strong>.
            </li>
            <li>Launch <strong className="text-white">Doxxed Crypto</strong> — Discover, trading, agents, Founder OS.</li>
            <li>
              For <strong className="text-white">private vault</strong>, pair Founder Node on PC and use{' '}
              <strong className="text-white">Code for Android</strong> in settings.
            </li>
          </ol>
          <p className="mt-3 text-[11px] text-zinc-500">
            Privacy: <a href="/privacy" className="text-cyan-300 underline">how encryption works</a> ·{' '}
            <a
              href="https://github.com/danishhaiderau-maker/doxed-founders-website/blob/master/PRIVACY.md"
              className="text-cyan-300 underline"
              target="_blank"
              rel="noreferrer"
            >
              full guide
            </a>
          </p>
        </div>
      )}
    </div>
  );
}
