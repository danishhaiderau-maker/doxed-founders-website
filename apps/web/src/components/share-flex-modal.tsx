'use client';

import Link from 'next/link';
import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  buildPortfolioShareUrl,
  buildProofOfConvictionMessage,
  buildProofOfConvictionThread,
  buildTwitterIntentUrl,
  formatTokenPrice,
  pickShareImagePath,
  shareImageFilename,
} from '@dcf/utils';
import type { PositionShareInput, ProofOfConvictionInput } from '@dcf/utils';
import { fetchXConnectionStatus, postProofOfConvictionToX } from '@/lib/api';

export type ShareConvictionConfig = {
  pnlOrRoi: number;
  tweetText: string;
  instantTweetText: string;
  threadPreview: string;
  shareUrl?: string;
  title: string;
  ticker?: string;
  projectId?: string;
  accessToken?: string;
  conviction?: {
    entryPrice?: number;
    currentPrice?: number;
    thesis?: string | null;
    catalyst?: string | null;
    targetPrice?: number | null;
    timeHorizon?: string | null;
    recordedAt?: string | null;
    daysHeld?: number;
  };
};

type ShareFlexModalProps = ShareConvictionConfig & {
  open: boolean;
  onClose: () => void;
};

function formatRecordedDate(iso: string | null | undefined): string {
  if (!iso) return '—';
  return new Date(iso).toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  });
}

export function ShareFlexModal({
  open,
  onClose,
  pnlOrRoi,
  tweetText,
  instantTweetText,
  threadPreview,
  shareUrl,
  title,
  ticker,
  projectId,
  accessToken,
  conviction,
}: ShareFlexModalProps) {
  const imagePath = useMemo(() => pickShareImagePath(pnlOrRoi), [pnlOrRoi, open]);
  const win = pnlOrRoi >= 0;
  const twitterUrl = buildTwitterIntentUrl(tweetText, shareUrl);
  const [copied, setCopied] = useState(false);
  const [flipped, setFlipped] = useState(false);
  const [posting, setPosting] = useState(false);
  const [postError, setPostError] = useState<string | null>(null);
  const [postedUrl, setPostedUrl] = useState<string | null>(null);
  const [xStatus, setXStatus] = useState<{
    canPostInstantly: boolean;
    twitterHandle: string | null;
    message: string;
  } | null>(null);

  useEffect(() => {
    if (!open) {
      setFlipped(false);
      setPostError(null);
      setPostedUrl(null);
      return;
    }
    if (!accessToken) {
      setXStatus(null);
      return;
    }
    fetchXConnectionStatus(accessToken)
      .then(setXStatus)
      .catch(() =>
        setXStatus({
          canPostInstantly: false,
          twitterHandle: null,
          message: 'Sign in with X to post in one tap.',
        }),
      );
  }, [open, accessToken]);

  const copyImageToClipboard = useCallback(async () => {
    try {
      const res = await fetch(imagePath);
      const blob = await res.blob();
      if (typeof ClipboardItem !== 'undefined' && navigator.clipboard?.write) {
        await navigator.clipboard.write([new ClipboardItem({ [blob.type]: blob })]);
        setCopied(true);
        return true;
      }
    } catch {
      /* fallback to download */
    }
    return false;
  }, [imagePath]);

  const downloadImage = useCallback(async () => {
    try {
      const res = await fetch(imagePath);
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = shareImageFilename(pnlOrRoi);
      a.click();
      URL.revokeObjectURL(url);
    } catch {
      window.open(imagePath, '_blank');
    }
  }, [imagePath, pnlOrRoi]);

  const openComposer = useCallback(async () => {
    const copiedOk = await copyImageToClipboard();
    if (!copiedOk) {
      await downloadImage();
    }
    window.open(twitterUrl, '_blank', 'noopener,noreferrer');
  }, [copyImageToClipboard, downloadImage, twitterUrl]);

  const postInstantly = useCallback(async () => {
    if (!accessToken || !projectId) return;
    setPosting(true);
    setPostError(null);
    try {
      const result = await postProofOfConvictionToX(
        { projectId, text: instantTweetText, pnlPercent: pnlOrRoi },
        accessToken,
      );
      setPostedUrl(result.tweetUrl);
    } catch (err) {
      setPostError(err instanceof Error ? err.message : 'Could not post to X');
    } finally {
      setPosting(false);
    }
  }, [accessToken, projectId, instantTweetText, pnlOrRoi]);

  if (!open) return null;

  const canInstant = Boolean(accessToken && projectId && xStatus?.canPostInstantly);
  const sign = win ? '+' : '';

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <button
        type="button"
        className="absolute inset-0 bg-black/70 backdrop-blur-sm"
        aria-label="Close"
        onClick={onClose}
      />
      <div
        className={`relative max-h-[90vh] w-full max-w-md overflow-y-auto rounded-2xl border p-5 shadow-2xl ${
          win ? 'border-emerald-500/40 bg-[#0a120f]' : 'border-red-500/40 bg-[#120a0a]'
        }`}
      >
        <div className="flex items-start justify-between gap-3">
          <div>
            <p className="text-xs uppercase tracking-wider text-zinc-400">Proof of Conviction</p>
            <h3 className="mt-1 text-lg font-bold text-white">{title}</h3>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="text-zinc-400 hover:text-white"
            aria-label="Close"
          >
            ×
          </button>
        </div>

        {conviction && ticker && (
          <div className="mt-4 perspective-[1000px]">
            <button
              type="button"
              onClick={() => setFlipped((f) => !f)}
              className="relative h-44 w-full [transform-style:preserve-3d] transition-transform duration-500"
              style={{ transform: flipped ? 'rotateY(180deg)' : undefined }}
            >
              <div
                className={`absolute inset-0 flex flex-col items-center justify-center rounded-xl border p-4 [backface-visibility:hidden] ${
                  win
                    ? 'border-emerald-500/30 bg-gradient-to-br from-emerald-950/80 to-black'
                    : 'border-red-500/30 bg-gradient-to-br from-red-950/80 to-black'
                }`}
              >
                <p className="text-xs uppercase tracking-widest text-zinc-500">Thesis played out</p>
                <p className="mt-2 text-3xl font-black text-white">${ticker}</p>
                <p
                  className={`mt-1 text-2xl font-bold ${win ? 'text-emerald-400' : 'text-red-400'}`}
                >
                  {sign}
                  {Math.abs(Math.round(pnlOrRoi))}%
                </p>
                <p className="mt-3 text-[10px] text-zinc-500">Tap to flip card</p>
              </div>
              <div
                className="absolute inset-0 flex flex-col justify-center rounded-xl border border-white/10 bg-zinc-950/90 p-4 text-left [backface-visibility:hidden] [transform:rotateY(180deg)]"
              >
                <div className="grid grid-cols-2 gap-x-4 gap-y-2 text-xs">
                  <div>
                    <p className="text-zinc-500">Entry</p>
                    <p className="font-semibold text-white">
                      {conviction.entryPrice != null
                        ? formatTokenPrice(conviction.entryPrice)
                        : '—'}
                    </p>
                  </div>
                  <div>
                    <p className="text-zinc-500">Current</p>
                    <p className="font-semibold text-white">
                      {conviction.currentPrice != null
                        ? formatTokenPrice(conviction.currentPrice)
                        : '—'}
                    </p>
                  </div>
                  <div>
                    <p className="text-zinc-500">Held</p>
                    <p className="font-semibold text-white">
                      {conviction.daysHeld != null ? `${conviction.daysHeld} days` : '—'}
                    </p>
                  </div>
                  <div>
                    <p className="text-zinc-500">Recorded</p>
                    <p className="font-semibold text-white">
                      {formatRecordedDate(conviction.recordedAt)}
                    </p>
                  </div>
                </div>
                {conviction.thesis?.trim() && (
                  <p className="mt-3 line-clamp-2 text-xs italic text-zinc-300">
                    &ldquo;{conviction.thesis.trim()}&rdquo;
                  </p>
                )}
                {conviction.catalyst?.trim() && (
                  <p className="mt-2 line-clamp-2 text-xs text-zinc-400">
                    Catalyst: {conviction.catalyst.trim()}
                  </p>
                )}
                {conviction.targetPrice != null && conviction.targetPrice > 0 && (
                  <p className="mt-1 text-[10px] text-zinc-500">
                    Target: {formatTokenPrice(conviction.targetPrice)}
                  </p>
                )}
                {conviction.timeHorizon?.trim() && (
                  <p className="mt-1 text-[10px] text-zinc-500">
                    Horizon: {conviction.timeHorizon.trim()}
                  </p>
                )}
              </div>
            </button>
          </div>
        )}

        <div className="mt-4 overflow-hidden rounded-xl border border-white/10">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src={imagePath} alt={win ? 'Pump meme' : 'Dump meme'} className="w-full object-cover" />
        </div>

        <div className="mt-3">
          <p className="text-[10px] font-medium uppercase tracking-wider text-zinc-500">
            Auto-written thread
          </p>
          <p className="mt-1 max-h-40 overflow-y-auto rounded-lg bg-black/30 p-3 text-xs leading-relaxed whitespace-pre-wrap text-zinc-300">
            {threadPreview}
          </p>
        </div>

        {accessToken && xStatus && (
          <div
            className={`mt-3 rounded-lg border px-3 py-2 text-xs ${
              canInstant
                ? 'border-emerald-500/30 bg-emerald-950/20 text-emerald-100'
                : 'border-amber-500/30 bg-amber-950/20 text-amber-100'
            }`}
          >
            {canInstant ? (
              <>
                Connected as @{xStatus.twitterHandle?.replace(/^@/, '')} — post with image in one tap.
              </>
            ) : (
              <>
                {xStatus.message}{' '}
                <Link href="/login" className="font-medium underline hover:text-white">
                  Sign in with X
                </Link>{' '}
                to enable Post Instantly.
              </>
            )}
          </div>
        )}

        {!accessToken && (
          <div className="mt-3 rounded-lg border border-sky-500/30 bg-sky-950/20 px-3 py-2 text-xs text-sky-100">
            <strong>Tip:</strong> Sign up with X for 1-click Proof of Conviction — no download, no paste.{' '}
            <Link href="/register" className="font-medium underline hover:text-white">
              Create account with X
            </Link>
          </div>
        )}

        {postError && (
          <p className="mt-3 rounded-lg border border-red-500/40 bg-red-950/30 p-2 text-xs text-red-200">
            {postError}
          </p>
        )}

        {postedUrl && (
          <p className="mt-3 rounded-lg border border-emerald-500/40 bg-emerald-950/30 p-2 text-xs text-emerald-200">
            Posted!{' '}
            <a href={postedUrl} target="_blank" rel="noopener noreferrer" className="underline">
              View on X
            </a>
          </p>
        )}

        <div className="mt-4 flex flex-col gap-2">
          {canInstant && !postedUrl && (
            <button
              type="button"
              onClick={postInstantly}
              disabled={posting}
              className={`rounded-lg py-2.5 text-sm font-semibold text-white disabled:opacity-50 ${
                win ? 'bg-emerald-600 hover:bg-emerald-500' : 'bg-red-600 hover:bg-red-500'
              }`}
            >
              {posting ? 'Posting…' : 'Post Instantly'}
            </button>
          )}
          {!postedUrl && (
            <button
              type="button"
              onClick={openComposer}
              className="rounded-lg bg-sky-500 py-2.5 text-sm font-medium text-white hover:bg-sky-400"
            >
              {copied ? 'Image copied — open X' : canInstant ? 'Share via composer' : 'Open X composer'}
            </button>
          )}
          <button
            type="button"
            onClick={downloadImage}
            className="rounded-lg border border-white/10 py-2 text-xs text-zinc-400 hover:text-white"
          >
            Download image
          </button>
        </div>
      </div>
    </div>
  );
}

export function useShareFlex(config: ShareConvictionConfig) {
  const [open, setOpen] = useState(false);
  const modal = (
    <ShareFlexModal {...config} open={open} onClose={() => setOpen(false)} />
  );
  return { openFlex: () => setOpen(true), modal };
}

function daysBetween(iso: string | null | undefined): number | undefined {
  if (!iso) return undefined;
  const start = new Date(iso).getTime();
  const now = Date.now();
  return Math.max(0, Math.floor((now - start) / (1000 * 60 * 60 * 24)));
}

function buildProofInput(
  input: PositionShareInput & { userId: string; origin: string; projectId?: string },
): ProofOfConvictionInput {
  const entryPrice =
    input.entryPrice ??
    (input.currentPrice != null
      ? input.currentPrice / (1 + input.pnlPercent / 100)
      : 0);
  const currentPrice =
    input.currentPrice ?? entryPrice * (1 + input.pnlPercent / 100);

  return {
    ticker: input.ticker,
    entryPrice,
    currentPrice,
    returnPct: input.pnlPercent,
    thesis: input.thesis,
    catalyst: input.catalyst,
    targetPrice: input.targetPrice,
    timeHorizon: input.timeHorizon,
    recordedAt: input.recordedAt ?? input.positionOpenedAt,
    proofUrl: buildPortfolioShareUrl(input.origin, input.userId),
  };
}

export function buildPositionFlexShare(
  input: PositionShareInput & {
    userId: string;
    origin: string;
    projectId?: string;
    accessToken?: string;
  },
): ShareConvictionConfig {
  const proof = buildProofInput(input);
  const entryPrice = input.entryPrice;
  const currentPrice =
    input.currentPrice ??
    (entryPrice != null ? entryPrice * (1 + input.pnlPercent / 100) : undefined);

  return {
    pnlOrRoi: input.pnlPercent,
    tweetText: buildProofOfConvictionThread(proof),
    instantTweetText: buildProofOfConvictionMessage(proof),
    threadPreview: buildProofOfConvictionThread(proof),
    shareUrl: buildPortfolioShareUrl(input.origin, input.userId),
    title: `$${input.ticker} · thesis ${input.pnlPercent >= 0 ? 'playing out' : 'update'}`,
    ticker: input.ticker,
    projectId: input.projectId,
    accessToken: input.accessToken,
    conviction: {
      entryPrice,
      currentPrice,
      thesis: input.thesis,
      catalyst: input.catalyst,
      targetPrice: input.targetPrice,
      timeHorizon: input.timeHorizon,
      recordedAt: input.recordedAt,
      daysHeld: input.daysHeld ?? daysBetween(input.recordedAt ?? input.positionOpenedAt),
    },
  };
}

export function buildPortfolioFlexShare(input: {
  displayName: string;
  roi: number;
  totalValue: number;
  userId: string;
  origin: string;
  accessToken?: string;
}): ShareConvictionConfig {
  const proofUrl = buildPortfolioShareUrl(input.origin, input.userId);
  const sign = input.roi >= 0 ? '+' : '';
  const threadPreview = [
    '🚨 Proof of Conviction · Portfolio',
    '',
    `${input.displayName}`,
    `Overall ROI: ${sign}${input.roi.toFixed(1)}%`,
    `Total value: $${input.totalValue.toLocaleString()}`,
    '',
    'Tip: Share from an individual position for full thesis + entry story.',
    '',
    'Proof:',
    proofUrl,
    '',
    '#ProofOfConviction',
  ].join('\n');
  const tweetText = threadPreview;
  return {
    pnlOrRoi: input.roi,
    tweetText,
    instantTweetText: `🚨 Portfolio · ${sign}${input.roi.toFixed(1)}% paper ROI\n${proofUrl}\n#ProofOfConviction`,
    threadPreview,
    shareUrl: proofUrl,
    title: 'Portfolio conviction',
    accessToken: input.accessToken,
  };
}
