'use client';

import { useEffect, useMemo, useState } from 'react';
import { signIn, useSession } from 'next-auth/react';
import { useSearchParams } from 'next/navigation';
import { Check, Laptop, ShieldCheck, X } from 'lucide-react';
import { SiteBrand } from '@/components/site-nav';
import {
  authorizeFounderNodeDevice,
  denyFounderNodeDevice,
  inspectFounderNodeDevice,
  type FounderNodeDevicePreview,
} from '@/lib/api';

type ResultState = 'idle' | 'authorizing' | 'authorized' | 'denied';

function normalizeUserCode(value: string | null): string {
  return (value ?? '').trim().toUpperCase().replace(/[^A-Z0-9-]/g, '').slice(0, 16);
}

export default function FounderIdAuthorizeClient() {
  const searchParams = useSearchParams();
  const { data: session, status } = useSession();
  const userCode = useMemo(
    () => normalizeUserCode(searchParams.get('user_code')),
    [searchParams],
  );
  const [result, setResult] = useState<ResultState>('idle');
  const [error, setError] = useState<string | null>(null);
  const [preview, setPreview] = useState<FounderNodeDevicePreview | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const accessToken = session?.accessToken ?? null;
  const callbackUrl =
    typeof window === 'undefined'
      ? `/founder-id/authorize?user_code=${encodeURIComponent(userCode)}`
      : `${window.location.pathname}${window.location.search}`;

  useEffect(() => {
    if (!accessToken || !userCode) {
      setPreview(null);
      return;
    }
    let cancelled = false;
    setPreviewLoading(true);
    setError(null);
    void inspectFounderNodeDevice(accessToken, userCode)
      .then((nextPreview) => {
        if (!cancelled) setPreview(nextPreview);
      })
      .catch((cause) => {
        if (!cancelled) {
          setPreview(null);
          setError(cause instanceof Error ? cause.message : 'Could not inspect this device.');
        }
      })
      .finally(() => {
        if (!cancelled) setPreviewLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [accessToken, userCode]);

  async function authorize() {
    if (!accessToken || !userCode) return;
    setResult('authorizing');
    setError(null);
    try {
      await authorizeFounderNodeDevice(accessToken, { userCode });
      setResult('authorized');
    } catch (cause) {
      setResult('idle');
      setError(cause instanceof Error ? cause.message : 'Could not connect Founder IDE.');
    }
  }

  async function deny() {
    if (!accessToken || !userCode) return;
    setResult('authorizing');
    setError(null);
    try {
      await denyFounderNodeDevice(accessToken, userCode);
      setResult('denied');
    } catch (cause) {
      setResult('idle');
      setError(cause instanceof Error ? cause.message : 'Could not deny this request.');
    }
  }

  return (
    <main className="flex min-h-screen items-center justify-center bg-[#08090b] px-5 py-10 text-white">
      <section className="w-full max-w-md">
        <SiteBrand className="mb-8 text-sm" />
        <div className="border-y border-zinc-800 py-7">
          {result === 'authorized' ? (
            <div className="text-center">
              <Check className="mx-auto h-10 w-10 text-emerald-400" aria-hidden />
              <h1 className="mt-4 text-xl font-semibold">Founder IDE connected</h1>
              <p className="mt-2 text-sm text-zinc-400">
                Return to Founder IDE. Sign-in will finish automatically.
              </p>
            </div>
          ) : result === 'denied' ? (
            <div className="text-center">
              <X className="mx-auto h-10 w-10 text-zinc-500" aria-hidden />
              <h1 className="mt-4 text-xl font-semibold">Connection denied</h1>
              <p className="mt-2 text-sm text-zinc-400">You can close this window.</p>
            </div>
          ) : (
            <>
              <div className="flex items-start gap-3">
                <Laptop className="mt-0.5 h-6 w-6 shrink-0 text-emerald-400" aria-hidden />
                <div>
                  <h1 className="text-xl font-semibold">Connect Founder IDE</h1>
                  <p className="mt-1 text-sm text-zinc-400">
                    Approve this laptop for your Founder OS account.
                  </p>
                </div>
              </div>

              <div className="mt-6 flex items-center justify-between border border-zinc-800 bg-zinc-950 px-4 py-3">
                <span className="text-xs text-zinc-500">Device code</span>
                <code className="text-sm font-semibold text-white">{userCode || 'Missing'}</code>
              </div>

              {previewLoading && (
                <p className="mt-4 text-sm text-zinc-500">Checking the device request...</p>
              )}

              {preview && (
                <div className="mt-4 border border-zinc-800 bg-zinc-950 px-4 py-4">
                  <p className="text-sm font-semibold text-white">{preview.deviceLabel}</p>
                  <dl className="mt-3 grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 text-xs">
                    <dt className="text-zinc-500">System</dt>
                    <dd className="text-right text-zinc-300">
                      {[preview.platform, preview.appVersion].filter(Boolean).join(' · ') || 'Not reported'}
                    </dd>
                    <dt className="text-zinc-500">Install fingerprint</dt>
                    <dd className="text-right font-mono text-zinc-300">
                      {preview.installFingerprint}
                    </dd>
                    <dt className="text-zinc-500">Request expires</dt>
                    <dd className="text-right text-zinc-300">
                      {new Date(preview.expiresAt).toLocaleString()}
                    </dd>
                  </dl>
                  <p className="mt-4 text-xs leading-5 text-zinc-500">
                    Approve only if this matches the Founder IDE in front of you.
                    The readable code cannot control the computer by itself.
                  </p>
                </div>
              )}

              {!userCode && (
                <p className="mt-4 text-sm text-red-300">
                  This link has no valid device code. Start sign-in again from Founder IDE.
                </p>
              )}

              {error && (
                <p className="mt-4 border border-red-500/40 bg-red-950/30 px-3 py-2 text-sm text-red-200">
                  {error}
                </p>
              )}

              {status === 'loading' ? (
                <p className="mt-6 text-sm text-zinc-500">Checking your account...</p>
              ) : !accessToken ? (
                <button
                  type="button"
                  disabled={!userCode}
                  onClick={() => void signIn('twitter', { callbackUrl })}
                  className="mt-6 w-full bg-white px-4 py-3 text-sm font-semibold text-black hover:bg-zinc-200 disabled:cursor-not-allowed disabled:opacity-40"
                >
                  Continue with X
                </button>
              ) : (
                <div className="mt-6 flex gap-3">
                  <button
                    type="button"
                    disabled={!userCode || !preview || previewLoading || result === 'authorizing'}
                    onClick={() => void authorize()}
                    className="flex flex-1 items-center justify-center gap-2 bg-emerald-400 px-4 py-3 text-sm font-semibold text-black hover:bg-emerald-300 disabled:cursor-not-allowed disabled:opacity-40"
                  >
                    <ShieldCheck className="h-4 w-4" aria-hidden />
                    {result === 'authorizing' ? 'Connecting...' : 'Approve'}
                  </button>
                  <button
                    type="button"
                    disabled={!userCode || !preview || previewLoading || result === 'authorizing'}
                    onClick={() => void deny()}
                    className="border border-zinc-700 px-4 py-3 text-sm font-medium text-zinc-300 hover:border-zinc-500 hover:text-white disabled:cursor-not-allowed disabled:opacity-40"
                  >
                    Deny
                  </button>
                </div>
              )}
            </>
          )}
        </div>
      </section>
    </main>
  );
}
