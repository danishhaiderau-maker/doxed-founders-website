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

function releaseStream(stream: MediaStream | null) {
  if (!stream) return;
  for (const track of stream.getTracks()) track.stop();
}

/** Keeps mic open until user toggles off — restarts recognition with a fresh instance on browser auto-end. */
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
  const streamRef = useRef<MediaStream | null>(null);
  const audioCtxRef = useRef<AudioContext | null>(null);
  const rafRef = useRef<number | null>(null);

  const listening = phase === 'listening';
  const starting = phase === 'starting';

  const stopAudioMeter = useCallback(() => {
    if (rafRef.current != null) {
      cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
    }
    setAudioLevel(0);
    if (audioCtxRef.current) {
      void audioCtxRef.current.close().catch(() => undefined);
      audioCtxRef.current = null;
    }
    releaseStream(streamRef.current);
    streamRef.current = null;
  }, []);

  const startAudioMeter = useCallback(async () => {
    stopAudioMeter();
    if (!navigator.mediaDevices?.getUserMedia) {
      setVoiceError('Microphone API not available — use HTTPS and a modern browser.');
      return false;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true },
      });
      streamRef.current = stream;
      const ctx = new AudioContext();
      audioCtxRef.current = ctx;
      const source = ctx.createMediaStreamSource(stream);
      const analyser = ctx.createAnalyser();
      analyser.fftSize = 256;
      source.connect(analyser);
      const data = new Uint8Array(analyser.frequencyBinCount);

      const tick = () => {
        analyser.getByteFrequencyData(data);
        let sum = 0;
        for (let i = 0; i < data.length; i++) sum += data[i] ?? 0;
        const avg = sum / data.length / 255;
        setAudioLevel((prev) => prev * 0.55 + avg * 0.45);
        rafRef.current = requestAnimationFrame(tick);
      };
      rafRef.current = requestAnimationFrame(tick);
      return true;
    } catch {
      setVoiceError(
        'Could not open microphone — click the lock icon in the address bar and allow mic access.',
      );
      return false;
    }
  }, [stopAudioMeter]);

  useEffect(() => {
    const hasSpeech = Boolean(getSpeechRecognition());
    const hasMic =
      typeof navigator !== 'undefined' && Boolean(navigator.mediaDevices?.getUserMedia);
    setSupported(hasSpeech && hasMic);
    return () => {
      wantListeningRef.current = false;
      recRef.current?.abort();
      stopAudioMeter();
    };
  }, [stopAudioMeter]);

  const markListening = useCallback(() => {
    setPhase('listening');
    setVoiceError(null);
  }, []);

  const startRecognition = useCallback(() => {
    const Ctor = getSpeechRecognition();
    if (!Ctor) {
      setVoiceError('Speech-to-text is not supported — use Chrome or Edge on desktop.');
      wantListeningRef.current = false;
      setPhase('idle');
      stopAudioMeter();
      return;
    }

    recRef.current?.abort();
    const rec = new Ctor();
    rec.continuous = true;
    rec.interimResults = true;
    rec.lang = 'en-US';

    rec.onstart = () => markListening();
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
        onTranscript(transcriptBaseRef.current, true);
      } else if (interim) {
        onTranscript(`${transcriptBaseRef.current} ${interim}`.trim(), false);
      }
    };

    rec.onerror = (ev) => {
      const msg = voiceErrorMessage(ev.error);
      if (msg) setVoiceError(msg);
      if (
        ev.error === 'not-allowed' ||
        ev.error === 'service-not-allowed' ||
        ev.error === 'audio-capture'
      ) {
        wantListeningRef.current = false;
        setPhase('idle');
        stopAudioMeter();
      }
    };

    rec.onend = () => {
      if (wantListeningRef.current) {
        setPhase('starting');
        window.setTimeout(() => {
          if (wantListeningRef.current) startRecognition();
        }, 200);
      } else {
        setPhase('idle');
        stopAudioMeter();
      }
    };

    recRef.current = rec;
    try {
      rec.start();
    } catch {
      setVoiceError('Could not start voice recognition — wait a moment and tap the mic again.');
      setPhase('idle');
      wantListeningRef.current = false;
      stopAudioMeter();
    }
  }, [markListening, onTranscript, stopAudioMeter]);

  const start = useCallback(
    async (existingText = '') => {
      setVoiceError(null);
      transcriptBaseRef.current = existingText.trim();
      wantListeningRef.current = true;
      setPhase('starting');
      const micOk = await startAudioMeter();
      if (!micOk) {
        wantListeningRef.current = false;
        setPhase('idle');
        return;
      }
      startRecognition();
    },
    [startAudioMeter, startRecognition],
  );

  const stop = useCallback(() => {
    wantListeningRef.current = false;
    recRef.current?.stop();
    recRef.current?.abort();
    setPhase('idle');
    stopAudioMeter();
  }, [stopAudioMeter]);

  const toggle = useCallback(
    (existingText = '') => {
      if (phase !== 'idle') stop();
      else void start(existingText);
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
}
