'use client';

import { useMemo } from 'react';

const SITE_ORIGIN = 'https://doxxedcrypto.digital';
const TESTFLIGHT_URL = process.env.NEXT_PUBLIC_IOS_TESTFLIGHT_URL?.trim() || '';

function detectMobileOs(): 'android' | 'ios' | 'desktop' {
  if (typeof navigator === 'undefined') return 'desktop';
  const ua = navigator.userAgent.toLowerCase();
  if (/android/.test(ua)) return 'android';
  if (/iphone|ipad|ipod/.test(ua)) return 'ios';
  return 'desktop';
}

export function IosAppDownloads() {
  const mobileOs = useMemo(() => detectMobileOs(), []);
  const testFlightReady = Boolean(TESTFLIGHT_URL);

  return (
    <div id="ios" className="scroll-mt-24 space-y-4">
      {mobileOs === 'ios' && (
        <p className="rounded-lg border border-sky-500/30 bg-sky-950/25 px-3 py-2 text-xs text-sky-100">
          iPhone / iPad detected — use Safari below or join TestFlight when the beta opens.
        </p>
      )}

      <div className="flex flex-wrap gap-3">
        {testFlightReady ? (
          <a
            href={TESTFLIGHT_URL}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-2 rounded-lg bg-sky-600 px-5 py-2.5 text-sm font-semibold text-white hover:bg-sky-500"
          >
            Join TestFlight beta
          </a>
        ) : (
          <span className="inline-flex items-center gap-2 rounded-lg border border-sky-500/40 bg-sky-950/30 px-5 py-2.5 text-sm font-medium text-sky-100">
            TestFlight — opening soon
          </span>
        )}
        <a
          href={`${SITE_ORIGIN}/discover`}
          className="inline-flex items-center gap-2 rounded-lg border border-zinc-600 px-5 py-2.5 text-sm font-medium text-zinc-200 hover:border-zinc-500"
        >
          Open in Safari
        </a>
      </div>

      <div className="rounded-lg border border-zinc-700 bg-zinc-950/60 p-4 text-xs text-zinc-300">
        <p className="font-semibold text-white">iOS today</p>
        <ul className="mt-2 list-disc space-y-1.5 pl-4 text-zinc-400">
          <li>
            Full Founder OS works in <strong className="text-zinc-200">Safari</strong> — same account as desktop.
          </li>
          <li>
            Tap <strong className="text-zinc-200">Share → Add to Home Screen</strong> for an app-icon shortcut (PWA-style).
          </li>
          <li>
            Native Capacitor iOS build is queued; TestFlight will ship the same unified app as Android (Founder OS + mobile
            vault — not a separate Founder Node listing).
          </li>
          <li>
            Desktop vault + Ollama still requires <strong className="text-zinc-200">Founder Node v0.7.9+</strong> on your
            laptop.
          </li>
        </ul>
      </div>

      <p className="text-xs text-zinc-500">
        App Store listing follows TestFlight. Until then, Safari at doxxedcrypto.digital is fully supported.
      </p>
    </div>
  );
}
