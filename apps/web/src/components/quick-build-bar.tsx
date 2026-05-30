'use client';

import { useCallback, useState } from 'react';
import { copilotHandsFree } from '@/lib/api';
import { useVoiceInput } from '@/hooks/use-voice-input';

type QuickBuildBarProps = {
  accessToken: string;
  founderActive?: boolean;
  onCaptured?: () => void;
  onMessage?: (msg: string) => void;
};

export function QuickBuildBar({
  accessToken,
  founderActive = true,
  onCaptured,
  onMessage,
}: QuickBuildBarProps) {
  const [open, setOpen] = useState(false);
  const [prompt, setPrompt] = useState('');
  const [busy, setBusy] = useState(false);

  const onTranscript = useCallback((text: string) => {
    setPrompt(text);
    setOpen(true);
  }, []);

  const { listening, supported, toggle, stop } = useVoiceInput(onTranscript);

  async function handleSubmit() {
    if (!founderActive) {
      onMessage?.('Activate your founder profile first');
      return;
    }
    if (!prompt.trim() || busy) return;
    stop();
    setBusy(true);
    try {
      const result = await copilotHandsFree(prompt.trim(), accessToken);
      onMessage?.(result.answer);
      setPrompt('');
      setOpen(false);
      onCaptured?.();
    } catch (err) {
      onMessage?.(err instanceof Error ? err.message : 'Quick build failed');
    } finally {
      setBusy(false);
    }
  }

  function handleVoiceToggle() {
    if (!founderActive) {
      onMessage?.('Activate your founder profile first');
      return;
    }
    if (!supported) {
      onMessage?.('Voice input is not supported in this browser — type your idea instead');
      return;
    }
    setOpen(true);
    toggle(prompt);
  }

  return (
    <>
      {!open && (
        <button
          type="button"
          onClick={() => setOpen(true)}
          className="fixed bottom-6 right-6 z-40 flex h-14 w-14 items-center justify-center rounded-full bg-emerald-600 text-2xl font-bold text-white shadow-lg shadow-emerald-900/50 hover:bg-emerald-500 md:hidden"
          aria-label="Quick Build"
        >
          +
        </button>
      )}

      <div
        className={`fixed inset-x-0 bottom-0 z-50 border-t border-emerald-500/30 bg-zinc-950/95 p-4 backdrop-blur lg:inset-x-auto lg:right-8 lg:bottom-8 lg:max-w-xl lg:rounded-2xl lg:border ${
          open ? 'block' : 'hidden lg:block'
        }`}
      >
        <div className="flex flex-col gap-2">
          <div className="flex items-center justify-between gap-2">
            <p className="text-xs font-semibold uppercase tracking-wider text-emerald-400">Quick Build</p>
            <button
              type="button"
              onClick={() => {
                stop();
                setOpen(false);
              }}
              className="text-xs text-zinc-500 hover:text-white lg:hidden"
            >
              Close
            </button>
          </div>
          <p className="text-[11px] text-zinc-500">
            Tell AI what to build — creates idea, spec, tasks & GitHub issues in your queue.
          </p>
          <div className="flex gap-2">
            <input
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && handleSubmit()}
              placeholder="Add dark mode… Add Backpack wallet support…"
              className="min-w-0 flex-1 rounded-xl border border-zinc-700 bg-black px-3 py-2.5 text-sm text-white placeholder:text-zinc-600"
            />
            <button
              type="button"
              onClick={handleVoiceToggle}
              className={`shrink-0 rounded-xl border px-3 py-2 text-sm ${
                listening
                  ? 'border-red-500/50 bg-red-950/40 text-red-200'
                  : 'border-zinc-600 text-zinc-300 hover:border-violet-500/50'
              }`}
              title={listening ? 'Stop recording' : 'Voice input — stays open while you talk'}
            >
              {listening ? '⏹' : '🎤'}
            </button>
            <button
              type="button"
              disabled={busy || !prompt.trim()}
              onClick={handleSubmit}
              className="shrink-0 rounded-xl bg-emerald-600 px-4 py-2 text-sm font-semibold text-white disabled:opacity-40"
            >
              {busy ? '…' : 'Queue'}
            </button>
          </div>
          {listening && (
            <p className="text-[10px] text-red-300/90">Listening… tap ⏹ when finished</p>
          )}
        </div>
      </div>
    </>
  );
}
