'use client';

import { useCallback, useEffect, useRef, useState } from 'react';

type SpeechRecognitionCtor = new () => {
  continuous: boolean;
  interimResults: boolean;
  lang: string;
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
  const [listening, setListening] = useState(false);
  const [supported, setSupported] = useState(false);
  const recRef = useRef<InstanceType<NonNullable<ReturnType<typeof getSpeechRecognition>>> | null>(
    null,
  );
  const wantListeningRef = useRef(false);
  const transcriptBaseRef = useRef('');

  useEffect(() => {
    setSupported(Boolean(getSpeechRecognition()));
    return () => {
      wantListeningRef.current = false;
      recRef.current?.abort();
    };
  }, []);

  const startRecognition = useCallback(() => {
    const Ctor = getSpeechRecognition();
    if (!Ctor) return;

    const rec = new Ctor();
    rec.continuous = true;
    rec.interimResults = true;
    rec.lang = 'en-US';

    rec.onresult = (ev) => {
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
        setListening(false);
      }
    };

    rec.onend = () => {
      if (wantListeningRef.current) {
        try {
          rec.start();
        } catch {
          setTimeout(() => {
            if (wantListeningRef.current) startRecognition();
          }, 300);
        }
      } else {
        setListening(false);
      }
    };

    recRef.current = rec;
    try {
      rec.start();
      setListening(true);
    } catch {
      setListening(false);
      wantListeningRef.current = false;
    }
  }, [onTranscript]);

  const start = useCallback(
    (existingText = '') => {
      transcriptBaseRef.current = existingText.trim();
      wantListeningRef.current = true;
      startRecognition();
    },
    [startRecognition],
  );

  const stop = useCallback(() => {
    wantListeningRef.current = false;
    recRef.current?.stop();
    setListening(false);
  }, []);

  const toggle = useCallback(
    (existingText = '') => {
      if (listening) stop();
      else start(existingText);
    },
    [listening, start, stop],
  );

  return { listening, supported, start, stop, toggle };
}
