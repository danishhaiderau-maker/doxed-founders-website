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

export type VoiceInputPhase = 'idle' | 'starting' | 'listening';

const MAX_RECOGNITION_RESTARTS = 8;
const RESTART_DELAY_MS = 350;

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
      return 'Voice needs internet (Chrome sends audio for transcription). Check connection and retry.';
    case 'aborted':
      return '';
    default:
      return `Voice error (${code}) — try Chrome or Edge, or type your message.`;
  }
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
  const pulseRafRef = useRef<number | null>(null);
  const onTranscriptRef = useRef(onTranscript);

  useEffect(() => {
    onTranscriptRef.current = onTranscript;
  }, [onTranscript]);

  const listening = phase === 'listening';
  const starting = phase === 'starting';

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

  useEffect(() => {
    setSupported(Boolean(getSpeechRecognition()));
    return () => {
      wantListeningRef.current = false;
      recRef.current?.abort();
      stopPulse();
    };
  }, [stopPulse]);

  const markListening = useCallback(() => {
    setPhase('listening');
    setVoiceError(null);
    startPulse();
  }, [startPulse]);

  const stopRecognition = useCallback(() => {
    wantListeningRef.current = false;
    restartCountRef.current = 0;
    recRef.current?.stop();
    recRef.current?.abort();
    recRef.current = null;
    setPhase('idle');
    stopPulse();
  }, [stopPulse]);

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
      setVoiceError('Voice session ended — tap the mic again to continue.');
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
      restartCountRef.current = 0;
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
        transcriptBaseRef.current = `${transcriptBaseRef.current} ${finalChunk}`.trim();
        onTranscriptRef.current(transcriptBaseRef.current, true);
      } else if (interim) {
        onTranscriptRef.current(`${transcriptBaseRef.current} ${interim}`.trim(), false);
      }
    };

    rec.onerror = (ev) => {
      const msg = voiceErrorMessage(ev.error);
      if (msg) setVoiceError(msg);
      if (
        ev.error === 'not-allowed' ||
        ev.error === 'service-not-allowed' ||
        ev.error === 'audio-capture' ||
        ev.error === 'network'
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
      rec.start();
    } catch {
      setVoiceError('Could not start voice recognition — wait a moment and tap the mic again.');
      wantListeningRef.current = false;
      setPhase('idle');
      stopPulse();
    }
  }, [markListening, stopPulse, stopRecognition]);

  const start = useCallback(
    (existingText = '') => {
      setVoiceError(null);
      transcriptBaseRef.current = existingText.trim();
      wantListeningRef.current = true;
      restartCountRef.current = 0;
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

  return {
    listening,
    starting,
    phase,
    supported,
    audioLevel,
    voiceError,
    clearVoiceError,
    start,
    stop,
    toggle,
  };
};
