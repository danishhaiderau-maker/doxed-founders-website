'use client';

import { useCallback, useMemo, useState } from 'react';
import {
  buildPortfolioShareMessage,
  buildPortfolioShareUrl,
  buildPositionShareMessage,
  buildTwitterIntentUrl,
  pickShareImagePath,
  shareImageFilename,
} from '@dcf/utils';
import type { PositionShareInput } from '@dcf/utils';

type ShareFlexModalProps = {
  open: boolean;
  onClose: () => void;
  pnlOrRoi: number;
  tweetText: string;
  shareUrl?: string;
  title: string;
};

export function ShareFlexModal({
  open,
  onClose,
  pnlOrRoi,
  tweetText,
  shareUrl,
  title,
}: ShareFlexModalProps) {
  const imagePath = useMemo(() => pickShareImagePath(pnlOrRoi), [pnlOrRoi, open]);
  const win = pnlOrRoi >= 0;
  const twitterUrl = buildTwitterIntentUrl(tweetText, shareUrl);

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

  if (!open) return null;

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
            <p className="text-xs uppercase tracking-wider text-zinc-400">Flex on X</p>
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

        <div className="mt-4 overflow-hidden rounded-xl border border-white/10">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src={imagePath} alt={win ? 'Pump meme' : 'Dump meme'} className="w-full object-cover" />
        </div>

        <p className="mt-3 rounded-lg bg-black/30 p-3 text-xs leading-relaxed whitespace-pre-wrap text-zinc-300">
          {tweetText}
        </p>

        <ol className="mt-3 space-y-1 text-xs text-zinc-400">
          <li>1. Download the {win ? '🚀 pump' : '📉 dump'} image</li>
          <li>2. Open X and attach the image to your post</li>
          <li>3. Paste the text (pre-filled when you tap Post on X)</li>
        </ol>

        <div className="mt-4 flex flex-col gap-2 sm:flex-row">
          <button
            type="button"
            onClick={downloadImage}
            className={`flex-1 rounded-lg py-2.5 text-sm font-medium text-white ${
              win ? 'bg-emerald-600 hover:bg-emerald-500' : 'bg-red-600 hover:bg-red-500'
            }`}
          >
            Download image
          </button>
          <a
            href={twitterUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="flex-1 rounded-lg bg-sky-500 py-2.5 text-center text-sm font-medium text-white hover:bg-sky-400"
          >
            Post on X
          </a>
        </div>
      </div>
    </div>
  );
}

type UseShareFlexOptions = {
  pnlOrRoi: number;
  tweetText: string;
  shareUrl?: string;
  title: string;
};

export function useShareFlex({ pnlOrRoi, tweetText, shareUrl, title }: UseShareFlexOptions) {
  const [open, setOpen] = useState(false);
  const modal = (
    <ShareFlexModal
      open={open}
      onClose={() => setOpen(false)}
      pnlOrRoi={pnlOrRoi}
      tweetText={tweetText}
      shareUrl={shareUrl}
      title={title}
    />
  );
  return { openFlex: () => setOpen(true), modal };
}

export function buildPositionFlexShare(input: PositionShareInput & { userId: string; origin: string }) {
  return {
    pnlOrRoi: input.pnlPercent,
    tweetText: buildPositionShareMessage(input),
    shareUrl: buildPortfolioShareUrl(input.origin, input.userId),
    title: `$${input.ticker} · ${input.pnlPercent >= 0 ? 'Gain' : 'Loss'} flex`,
  };
}

export function buildPortfolioFlexShare(input: {
  displayName: string;
  roi: number;
  totalValue: number;
  userId: string;
  origin: string;
}) {
  return {
    pnlOrRoi: input.roi,
    tweetText: buildPortfolioShareMessage(input.displayName, input.roi, input.totalValue),
    shareUrl: buildPortfolioShareUrl(input.origin, input.userId),
    title: 'Portfolio flex',
  };
}
