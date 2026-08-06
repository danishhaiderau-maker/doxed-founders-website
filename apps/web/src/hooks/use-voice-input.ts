'use client';

import { useCallback, useEffect, useRef, useState } from 'react';

type SpeechRecognitionCtor = new () => {
  continuous: boolean;
  interimResults: boolean;
  lang: string;
  onstart: (() => void) | null;
  onspeechstart: (() => void) | null;
  onresult: ((ev: SpeechRecognitionEventLike) => void) | null;
  onerror: ((ev: { error: string }) => void) | null;
  onend: (() => void) | null;
  start: () => void;
  stop: () => void;
  abort: () => void;
};

type SpeechRecognitionEventLike = {
  resultIndex: number;
  results: {
    length: number;
    [index: number]: {
      isFinal: boolean;
      [index: number]: { transcript: string };
    };
  };
};

export type VoiceInputPhase = 'idle' | 'starting' | 'listening' | 'waiting_network';

const MAX_RECOGNITION_RESTARTS = 24;
const RESTART_DELAY_MS = 350;
/** Initial backoff floor for silent retries (errors 1 and 2). */
const NETWORK_RETRY_SILENT_MS = 1200;
/** Backoff floor once we surface "waiting for internet" (errors 3+). */
const NETWORK_RETRY_VISIBLE_MS = 5000;
/** Absolute minimum spacing between two recognition restart attempts. */
const MIN_RETRY_GAP_MS = 800;
/** Suppress the "waiting for internet" UI until this many consecutive
 *  network errors have fired without a successful onstart/onspeechstart/onresult. */
const SILENT_NETWORK_ERROR_THRESHOLD = 3;
const MAX_NETWORK_RETRIES = 6;
/** Guard against runaway interim/final duplication in the textarea. */
export const MAX_VOICE_TRANSCRIPT_LENGTH = 12_000;

/** Collapse cumulative STT duplication (same phrase repeated with growing prefixes). */
export function cleanTranscriptText(text: string): string {
  let t = text.trim().replace(/\s+/g, ' ');
  if (!t) return t;
  if (t.length > MAX_VOICE_TRANSCRIPT_LENGTH) {
    t = t.slice(0, MAX_VOICE_TRANSCRIPT_LENGTH).trim();
  }
  const words = t.split(' ');
  if (words.length < 8) return t;

  // Drop consecutive duplicate word runs (e.g. "uh uh uh" → "uh").
  const dedupedWords: string[] = [];
  for (const w of words) {
    if (dedupedWords.length === 0 || dedupedWords[dedupedWords.length - 1] !== w) {
      dedupedWords.push(w);
    }
  }
  t = dedupedWords.join(' ');

  // If the string is prefix + space + prefix + … keep the longest trailing segment.
  for (let len = Math.min(Math.floor(t.length / 2), 240); len >= 12; len--) {
    const prefix = t.slice(0, len).trimEnd();
    if (!prefix) continue;
    const rest = t.slice(len).trimStart();
    if (rest.startsWith(prefix)) {
      let candidate = t;
      while (candidate.length > prefix.length) {
        const tail = candidate.slice(prefix.length).trimStart();
        if (tail.startsWith(prefix)) candidate = tail;
        else break;
      }
      return candidate.trim() || t;
    }
  }
  return t;
}

function getSpeechRecognition(): SpeechRecognitionCtor | null {
  if (typeof window === 'undefined') return null;
  const w = window as Window & {
    SpeechRecognition?: SpeechRecognitionCtor;
    webkitSpeechRecognition?: SpeechRecognitionCtor;
  };
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null;
}

function voiceErrorMessage(code: string): string {
  switch (code) {
    case 'not-allowed':
    case 'service-not-allowed':
      return 'Microphone blocked — allow mic for this site in browser settings, then try again.';
    case 'no-speech':
      return 'No speech detected — speak clearly or move closer to the mic.';
    case 'audio-capture':
      return 'No microphone found — plug in a mic or check Windows sound settings.';
    case 'network':
      return 'Reconnecting voice… your words are kept; keep talking.';
    case 'aborted':
      return '';
    default:
      return `Voice error (${code}) — try Chrome or Edge, or type your message.`;
  }
}

/** navigator.onLine is the only authoritative signal of "the internet is
 *  actually down" we have. Chrome's Web Speech API fires spurious `network`
 *  errors on DNS refresh / idle socket / server throttle even when the user's
 *  connection is healthy — so we never surface a "waiting for internet"
 *  message while the browser itself reports online. */
function browserIsOnline(): boolean {
  if (typeof navigator === 'undefined') return true;
  return navigator.onLine === true;
}

/**
 * Chrome Web Speech API already opens the mic — do not also call getUserMedia (dual capture crashes tabs).
 */
export function useVoiceInput(onTranscript: (text: string, isFinal: boolean) => void) {
  const [phase, setPhase] = useState<VoiceInputPhase>('idle');
  const [supported, setSupported] = useState(false);
  const [audioLevel, setAudioLevel] = useState(0);
  const [voiceError, setVoiceError] = useState<string | null>(null);
  const recRef = useRef<InstanceType<NonNullable<ReturnType<typeof getSpeechRecognition>>> | null>(
    null,
  );
  const wantListeningRef = useRef(false);
  const transcriptBaseRef = useRef('');
  const restartCountRef = useRef(0);
  const networkRetryRef = useRef(0);
  /** Consecutive network errors with no successful onstart/onspeechstart/onresult
   *  in between. Reset to 0 the moment recognition comes back healthy. Drives
   *  the silent-retry threshold so a single flaky `network` error doesn't
   *  disrupt the UI. */
  const consecutiveNetworkErrorsRef = useRef(0);
  /** When true, `onend` is the tail of a network-error retry path (silent OR
   *  visible) and must NOT trigger the generic session-restart path — that
   *  would race with our own scheduled retry and double-restart recognition. */
  const waitingNetworkRef = useRef(false);
  const networkRetryTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  /** Timestamp (ms) of the last recognition start attempt — used to enforce
   *  MIN_RETRY_GAP_MS so retries can't cluster. */
  const lastRetryAtRef = useRef(0);
  const pulseRafRef = useRef<number | null>(null);
  const onTranscriptRef = useRef(onTranscript);

  useEffect(() => {
    onTranscriptRef.current = onTranscript;
  }, [onTranscript]);

  const listening = phase === 'listening';
  const starting = phase === 'starting';
  const waitingNetwork = phase === 'waiting_network';

  const stopPulse = useCallback(() => {
    if (pulseRafRef.current != null) {
      cancelAnimationFrame(pulseRafRef.current);
      pulseRafRef.current = null;
    }
    setAudioLevel(0);
  }, []);

  const startPulse = useCallback(() => {
    stopPulse();
    let t0 = performance.now();
    const tick = (now: number) => {
      if (!wantListeningRef.current) {
        stopPulse();
        return;
      }
      const wave = 0.35 + 0.25 * Math.sin((now - t0) / 180);
      setAudioLevel((prev) => prev * 0.6 + wave * 0.4);
      pulseRafRef.current = requestAnimationFrame(tick);
    };
    pulseRafRef.current = requestAnimationFrame(tick);
  }, [stopPulse]);

  const clearNetworkRetryTimer = useCallback(() => {
    if (networkRetryTimerRef.current != null) {
      clearTimeout(networkRetryTimerRef.current);
      networkRetryTimerRef.current = null;
    }
  }, []);

  useEffect(() => {
    setSupported(Boolean(getSpeechRecognition()));
    return () => {
      wantListeningRef.current = false;
      clearNetworkRetryTimer();
      recRef.current?.abort();
      stopPulse();
    };
  }, [clearNetworkRetryTimer, stopPulse]);

  const markListening = useCallback(() => {
    // Any sign of life from the recognition engine means the previous network
    // errors were spurious — clear the streak so the next one starts fresh.
    consecutiveNetworkErrorsRef.current = 0;
    networkRetryRef.current = 0;
    restartCountRef.current = 0;
    waitingNetworkRef.current = false;
    setPhase('listening');
    setVoiceError(null);
    startPulse();
  }, [startPulse]);

  const stopRecognition = useCallback(() => {
    wantListeningRef.current = false;
    restartCountRef.current = 0;
    networkRetryRef.current = 0;
    consecutiveNetworkErrorsRef.current = 0;
    waitingNetworkRef.current = false;
    lastRetryAtRef.current = 0;
    clearNetworkRetryTimer();
    recRef.current?.stop();
    recRef.current?.abort();
    recRef.current = null;
    setPhase('idle');
    stopPulse();
  }, [clearNetworkRetryTimer, stopPulse]);

  const scheduleNetworkRetry = useCallback(
    (startRecognitionFn: () => void) => {
      if (!wantListeningRef.current) return;

      consecutiveNetworkErrorsRef.current += 1;
      const streak = consecutiveNetworkErrorsRef.current;

      // Hard cap: too many consecutive failures — give up gracefully. The user
      // can still type & send; voice resumes on the next explicit mic tap.
      if (streak > MAX_NETWORK_RETRIES) {
        setVoiceError('Voice paused — tap the mic to retry.');
        wantListeningRef.current = false;
        waitingNetworkRef.current = false;
        setPhase('idle');
        stopPulse();
        return;
      }

      // Determine which tier we're in. If the browser reports online, we treat
      // ALL network errors as silent — Chrome's Web Speech API is just flaky.
      const online = browserIsOnline();
      const surfaceUi = !online && streak >= SILENT_NETWORK_ERROR_THRESHOLD;

      // Suppress the generic onend-restart path for BOTH tiers so it doesn't
      // race with our scheduled retry (would double-start recognition).
      waitingNetworkRef.current = true;

      if (surfaceUi) {
        // Tier 2: visible "waiting for internet". Slower backoff (5s base).
        setPhase('waiting_network');
        setVoiceError(voiceErrorMessage('network'));
        startPulse();
      } else {
        // Tier 1 (or online): silent retry. Do NOT flip phase away from
        // 'listening'/'starting' — UI shows no disruption, user keeps talking.
        setVoiceError(null);
      }

      networkRetryRef.current = streak;
      const baseDelay = surfaceUi ? NETWORK_RETRY_VISIBLE_MS : NETWORK_RETRY_SILENT_MS;
      const elapsedSinceLast = Date.now() - lastRetryAtRef.current;
      const delay = Math.max(baseDelay, MIN_RETRY_GAP_MS - elapsedSinceLast);

      clearNetworkRetryTimer();
      networkRetryTimerRef.current = setTimeout(() => {
        if (!wantListeningRef.current) return;
        const now = Date.now();
        const since = now - lastRetryAtRef.current;
        if (since < MIN_RETRY_GAP_MS) {
          // Reschedule for the remaining gap rather than firing immediately.
          const wait = MIN_RETRY_GAP_MS - since;
          networkRetryTimerRef.current = setTimeout(() => {
            if (!wantListeningRef.current) return;
            lastRetryAtRef.current = Date.now();
            startRecognitionFn();
          }, wait);
          return;
        }
        lastRetryAtRef.current = now;
        startRecognitionFn();
      }, delay);
    },
    [clearNetworkRetryTimer, startPulse, stopPulse],
  );

  const startRecognition = useCallback(() => {
    const Ctor = getSpeechRecognition();
    if (!Ctor) {
      setVoiceError('Speech-to-text is not supported — use Chrome or Edge on desktop.');
      wantListeningRef.current = false;
      setPhase('idle');
      stopPulse();
      return;
    }

    if (restartCountRef.current >= MAX_RECOGNITION_RESTARTS) {
      setVoiceError('Voice session paused — tap the mic again to continue.');
      wantListeningRef.current = false;
      setPhase('idle');
      stopPulse();
      return;
    }

    recRef.current?.abort();
    const rec = new Ctor();
    rec.continuous = true;
    rec.interimResults = true;
    rec.lang = 'en-US';

    rec.onstart = () => {
      // markListening resets consecutiveNetworkErrorsRef, networkRetryRef, and
      // waitingNetworkRef — a single source of truth for "recognition is healthy".
      markListening();
    };
    rec.onspeechstart = () => markListening();

    rec.onresult = (ev) => {
      markListening();
      let interim = '';
      let finalChunk = '';
      for (let i = ev.resultIndex; i < ev.results.length; i++) {
        const part = ev.results[i]?.[0]?.transcript ?? '';
        if (ev.results[i]?.isFinal) finalChunk += part;
        else interim += part;
      }
      if (finalChunk) {
        transcriptBaseRef.current = cleanTranscriptText(
          `${transcriptBaseRef.current} ${finalChunk}`.trim(),
        );
        onTranscriptRef.current(transcriptBaseRef.current, true);
      } else if (interim) {
        const preview = cleanTranscriptText(`${transcriptBaseRef.current} ${interim}`.trim());
        onTranscriptRef.current(preview, false);
      }
    };

    rec.onerror = (ev) => {
      if (ev.error === 'network' && wantListeningRef.current) {
        scheduleNetworkRetry(startRecognition);
        return;
      }
      const msg = voiceErrorMessage(ev.error);
      if (msg) setVoiceError(msg);
      if (
        ev.error === 'not-allowed' ||
        ev.error === 'service-not-allowed' ||
        ev.error === 'audio-capture'
      ) {
        stopRecognition();
      }
    };

    rec.onend = () => {
      if (!wantListeningRef.current) {
        setPhase('idle');
        stopPulse();
        return;
      }
      if (waitingNetworkRef.current) return;
      restartCountRef.current += 1;
      if (restartCountRef.current >= MAX_RECOGNITION_RESTARTS) {
        stopRecognition();
        return;
      }
      setPhase('starting');
      window.setTimeout(() => {
        if (wantListeningRef.current) startRecognition();
      }, RESTART_DELAY_MS);
    };

    recRef.current = rec;
    try {
      lastRetryAtRef.current = Date.now();
      rec.start();
    } catch {
      setVoiceError('Could not start voice recognition — wait a moment and tap the mic again.');
      wantListeningRef.current = false;
      setPhase('idle');
      stopPulse();
    }
  }, [markListening, scheduleNetworkRetry, stopPulse, stopRecognition]);

  const start = useCallback(
    (existingText = '') => {
      setVoiceError(null);
      transcriptBaseRef.current = cleanTranscriptText(existingText);
      wantListeningRef.current = true;
      restartCountRef.current = 0;
      networkRetryRef.current = 0;
      consecutiveNetworkErrorsRef.current = 0;
      waitingNetworkRef.current = false;
      setPhase('starting');
      startRecognition();
    },
    [startRecognition],
  );

  const stop = useCallback(() => {
    stopRecognition();
  }, [stopRecognition]);

  const toggle = useCallback(
    (existingText = '') => {
      if (phase !== 'idle') stop();
      else start(existingText);
    },
    [phase, start, stop],
  );

  const clearVoiceError = useCallback(() => setVoiceError(null), []);

  /** Clear accumulated transcript so a send cannot be overwritten by stale STT. */
  const resetTranscript = useCallback(() => {
    transcriptBaseRef.current = '';
  }, []);

  return {
    listening,
    starting,
    waitingNetwork,
    phase,
    supported,
    audioLevel,
    voiceError,
    clearVoiceError,
    resetTranscript,
    start,
    stop,
    toggle,
  };
};
