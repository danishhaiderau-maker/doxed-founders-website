'use client';

import { useEffect, useMemo, useState } from 'react';
import { appendPlatformXShareFooter } from '@dcf/utils';
import { useShareFooter } from '@/components/share-footer-provider';
import { fetchAiSectionRoutingPublic, paraphraseShareTweet } from '@/lib/api';

type Props = {
  open: boolean;
  onClose: () => void;
  /** Initial tweet text (already-built share message, without footer). */
  text: string;
  /** Optional project URL appended to the tweet via twitter intent `url` param. */
  url?: string;
  /** Optional project context passed to the paraphrase endpoint. */
  projectName?: string;
  ticker?: string;
  slug?: string;
  /** Auth token — required for the AI paraphrase button. */
  accessToken?: string;
};

/**
 * Preview modal for the project-page X share flow.
 *
 * - Shows the tweet text in an editable textarea.
 * - "✨ Paraphrase with AI" calls POST /share/paraphrase (DeepSeek) and
 *   replaces the textarea content with the rewritten tweet.
 * - "Share on X" opens https://twitter.com/intent/tweet?text=<encoded current
 *   textarea content> in a new tab (plus `url` when provided).
 * - "Cancel" closes the modal.
 * - Dark-mode aesthetic matching the rest of the app (violet accents, near-black
 *   bg, rounded modal with backdrop).
 */
export function SharePreviewModal({
  open,
  onClose,
  text,
  url,
  projectName,
  ticker,
  slug,
  accessToken,
}: Props) {
  const shareFooter = useShareFooter();
  const [draft, setDraft] = useState(text);
  const [paraphrasing, setParaphrasing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [needsLogin, setNeedsLogin] = useState(false);
  const [providerLabel, setProviderLabel] = useState<string | null>(null);

  // Sync the local draft whenever the modal is (re)opened with new text.
  useEffect(() => {
    if (open) {
      setDraft(text);
      setError(null);
      setNeedsLogin(false);
    }
  }, [open, text]);

  // Resolve which AI provider is routed for share_paraphrase so the button can
  // announce it (e.g. "Paraphrase with Gemini"). Public endpoint — no token.
  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    fetchAiSectionRoutingPublic('share_paraphrase')
      .then((r) => {
        if (cancelled) return;
        setProviderLabel(r?.ready ? (r.providerLabel ?? r.providerKey ?? null) : null);
      })
      .catch(() => {
        if (!cancelled) setProviderLabel(null);
      });
    return () => {
      cancelled = true;
    };
  }, [open]);

  // Close on Escape.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  const twitterHref = useMemo(() => {
    const fullText = appendPlatformXShareFooter(draft, shareFooter);
    const params = new URLSearchParams({ text: fullText });
    if (url) params.set('url', url);
    return `https://twitter.com/intent/tweet?${params.toString()}`;
  }, [draft, shareFooter, url]);

  if (!open) return null;

  const handleParaphrase = async () => {
    if (!accessToken) {
      setNeedsLogin(true);
      return;
    }
    const trimmed = draft.trim();
    if (!trimmed) {
      setError('Add some text first, then paraphrase.');
      return;
    }
    setParaphrasing(true);
    setError(null);
    try {
      const result = await paraphraseShareTweet(
        { text: trimmed, projectName, ticker, slug },
        accessToken,
      );
      if (result.text?.trim()) {
        setDraft(result.text.trim());
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'AI paraphrase failed. Try again.');
    } finally {
      setParaphrasing(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <button
        type="button"
        className="absolute inset-0 bg-black/70 backdrop-blur-sm"
        aria-label="Close"
        onClick={onClose}
      />

      <div className="relative w-full max-w-lg rounded-2xl border border-violet-500/40 bg-[#0B0B0B] p-5 shadow-2xl md:max-w-xl md:p-6">
        <div className="flex items-start justify-between gap-3">
          <div>
            <p className="text-xs uppercase tracking-wider text-violet-300">Share on X</p>
            <h3 className="mt-1 text-lg font-bold text-white">Preview your tweet</h3>
            <p className="mt-1 text-xs text-zinc-500">
              Edit before posting, or paraphrase with AI into a Twitter-ready founder-onboarding message.
            </p>
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

        <textarea
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          rows={8}
          className="mt-4 w-full resize-y rounded-xl border border-zinc-800 bg-zinc-950/60 p-3 text-sm leading-relaxed text-zinc-100 outline-none focus:border-violet-500/60 focus:ring-1 focus:ring-violet-500/40"
          spellCheck
        />

        {error && (
          <p className="mt-3 rounded-lg border border-red-500/40 bg-red-950/30 p-2 text-xs text-red-200">
            {error}
          </p>
        )}

        {needsLogin && (
          <p className="mt-3 rounded-lg border border-amber-500/40 bg-amber-950/30 p-2 text-xs text-amber-200">
            Sign in to use AI paraphrase. You can still edit and post manually.
          </p>
        )}

        <div className="mt-4 flex flex-col gap-2 sm:flex-row sm:justify-end">
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg border border-zinc-700 px-4 py-2 text-sm text-zinc-300 hover:border-zinc-600 hover:text-white"
          >
            Cancel
          </button>

          <button
            type="button"
            onClick={handleParaphrase}
            disabled={paraphrasing}
            className="rounded-lg border border-violet-500/50 bg-violet-950/40 px-4 py-2 text-sm font-medium text-violet-200 transition hover:border-violet-400/70 hover:bg-violet-900/40 disabled:opacity-50"
          >
            {paraphrasing ? 'Paraphrasing…' : `✨ Paraphrase with ${providerLabel ?? 'AI'} — make it Twitter-ready`}
          </button>

          <a
            href={twitterHref}
            target="_blank"
            rel="noopener noreferrer"
            className="rounded-lg bg-sky-500 px-4 py-2 text-center text-sm font-semibold text-white transition hover:bg-sky-400"
          >
            Share on X
          </a>
        </div>
      </div>
    </div>
  );
}
