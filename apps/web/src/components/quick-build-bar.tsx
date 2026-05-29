'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { copilotHandsFree } from '@/lib/api';

type QuickBuildBarProps = {
  accessToken: string;
  founderActive?: boolean;
  onCaptured?: () => void;
  onMessage?: (msg: string) => void;
};

type SpeechRecognitionCtor = new () => {
  continuous: boolean;
  interimResults: boolean;
  lang: string;
  onresult: ((ev: { results: { [index: number]: { [index: number]: { transcript: string } } } }) => void) | null;
  onerror: ((ev: { error: string }) => void) | null;
  onend: (() => void) | null;
  start: () => void;
  stop: () => void;
};

function getSpeechRecognition(): SpeechRecognitionCtor | null {
  if (typeof window === 'undefined') return null;
  const w = window as Window & {
    SpeechRecognition?: SpeechRecognitionCtor;
    webkitSpeechRecognition?: SpeechRecognitionCtor;
  };
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null;
}

export function QuickBuildBar({
  accessToken,
  founderActive = true,
  onCaptured,
  onMessage,
}: QuickBuildBarProps) {
  const [open, setOpen] = useState(false);
  const [prompt, setPrompt] = useState('');
  const [listening, setListening] = useState(false);
  const [busy, setBusy] = useState(false);
  const recognitionRef = useRef<InstanceType<NonNullable<ReturnType<typeof getSpeechRecognition>>> | null>(null);

  const stopVoice = useCallback(() => {
    recognitionRef.current?.stop();
    setListening(false);
  }, []);

  useEffect(() => () => stopVoice(), [stopVoice]);

  async function handleSubmit() {
    if (!founderActive) {
      onMessage?.('Activate your founder profile first');
      return;
    }
    if (!prompt.trim() || busy) return;
    setBusy(true);
    try {
      const text = listening ? `[voice] ${prompt.trim()}` : prompt.trim();
      const result = await copilotHandsFree(text, accessToken);
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

  function toggleVoice() {
    if (!founderActive) {
      onMessage?.('Activate your founder profile first');
      return;
    }
    const Ctor = getSpeechRecognition();
    if (!Ctor) {
      onMessage?.('Voice input is not supported in this browser — type your idea instead');
      return;
    }
    if (listening) {
      stopVoice();
      return;
    }
    const rec = new Ctor();
    rec.continuous = false;
    rec.interimResults = true;
    rec.lang = 'en-US';
    rec.onresult = (ev) => {
      const results = ev.results as unknown as { length: number; [index: number]: { [index: number]: { transcript: string } } };
      const text = Array.from({ length: results.length })
        .map((_, i) => results[i]?.[0]?.transcript ?? '')
        .join(' ')
        .trim();
      if (text) setPrompt(text);
    };
    rec.onerror = () => setListening(false);
    rec.onend = () => setListening(false);
    recognitionRef.current = rec;
    setListening(true);
    setOpen(true);
    rec.start();
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
        className={`fixed inset-x-0 bottom-0 z-50 border-t border-emerald-500/30 bg-zinc-950/95 p-4 backdrop-blur md:bottom-4 md:inset-x-auto md:right-6 md:max-w-md md:rounded-2xl md:border ${
          open ? 'block' : 'hidden md:block'
        }`}
      >
        <div className="mx-auto flex max-w-6xl flex-col gap-2 md:mx-0">
          <div className="flex items-center justify-between gap-2">
            <p className="text-xs font-semibold uppercase tracking-wider text-emerald-400">Quick Build</p>
            <button
              type="button"
              onClick={() => setOpen(false)}
              className="text-xs text-zinc-500 hover:text-white md:hidden"
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
              onClick={toggleVoice}
              className={`shrink-0 rounded-xl border px-3 py-2 text-sm ${
                listening
                  ? 'border-red-500/50 bg-red-950/40 text-red-200 animate-pulse'
                  : 'border-zinc-600 text-zinc-300 hover:border-violet-500/50'
              }`}
              title="Founder voice mode"
            >
              {listening ? '●' : '🎤'}
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
        </div>
      </div>
    </>
  );
}
