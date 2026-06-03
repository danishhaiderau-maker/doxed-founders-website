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

/** Keeps mic open until user toggles off — restarts on browser auto-end. */
export function useVoiceInput(onTranscript: (text: string, isFinal: boolean) => void) {
  const [phase, setPhase] = useState<VoiceInputPhase>('idle');
  const [supported, setSupported] = useState(false);
  const recRef = useRef<InstanceType<NonNullable<ReturnType<typeof getSpeechRecognition>>> | null>(
    null,
  );
  const wantListeningRef = useRef(false);
  const transcriptBaseRef = useRef('');

  const listening = phase === 'listening';
  const starting = phase === 'starting';

  useEffect(() => {
    setSupported(Boolean(getSpeechRecognition()));
    return () => {
      wantListeningRef.current = false;
      recRef.current?.abort();
    };
  }, []);

  const markListening = useCallback(() => {
    setPhase('listening');
  }, []);

  const startRecognition = useCallback(() => {
    const Ctor = getSpeechRecognition();
    if (!Ctor) return;

    const rec = new Ctor();
    rec.continuous = true;
    rec.interimResults = true;
    rec.lang = 'en-US';

    rec.onstart = () => {
      markListening();
    };

    rec.onspeechstart = () => {
      markListening();
    };

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
      if (ev.error === 'not-allowed' || ev.error === 'service-not-allowed') {
        wantListeningRef.current = false;
        setPhase('idle');
      }
    };

    rec.onend = () => {
      if (wantListeningRef.current) {
        setPhase('starting');
        try {
          rec.start();
        } catch {
          setTimeout(() => {
            if (wantListeningRef.current) startRecognition();
          }, 300);
        }
      } else {
        setPhase('idle');
      }
    };

    recRef.current = rec;
    try {
      rec.start();
    } catch {
      setPhase('idle');
      wantListeningRef.current = false;
    }
  }, [markListening, onTranscript]);

  const start = useCallback(
    (existingText = '') => {
      transcriptBaseRef.current = existingText.trim();
      wantListeningRef.current = true;
      setPhase('starting');
      startRecognition();
    },
    [startRecognition],
  );

  const stop = useCallback(() => {
    wantListeningRef.current = false;
    recRef.current?.stop();
    setPhase('idle');
  }, []);

  const toggle = useCallback(
    (existingText = '') => {
      if (phase !== 'idle') stop();
      else start(existingText);
    },
    [phase, start, stop],
  );

  return { listening, starting, phase, supported, start, stop, toggle };
}
