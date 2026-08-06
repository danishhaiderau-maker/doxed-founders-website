'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import {
  fetchFounderNodeStatus,
  fetchIdeBridgeSessions,
  fetchIdeBridgeWorkspaces,
  dispatchToIdeSession,
  fetchIdeDispatchStatus,
  type BridgeSession,
  type BridgeWorkspace,
  type FounderNodeStatusRow,
} from '@/lib/api';
import { useVoiceInput } from '@/hooks/use-voice-input';
import { VoiceWaveform } from '@/components/voice-waveform';

type ChatMsg = {
  id: string;
  role: 'user' | 'assistant';
  text: string;
  at: string;
  pending?: boolean;
  status?: string;
};

/**
 * Options shown in the AI dropdown. "Auto", "Free", "Founder AI" stay in-chat
 * (no navigation). "Local" and "BYOK" navigate to dedicated routes.
 *
 * HARD COST RULE: GLM is never selectable here. GLM is cost-prohibitive and
 * is reserved exclusively for the Second Brain critical-review surface.
 * General chat uses DeepSeek (text) + Gemini (vision) only.
 */
type AiOption = {
  key: string;
  label: string;
  kind: 'chat' | 'nav';
  href?: string;
  blurb: string;
};

const AI_OPTIONS: AiOption[] = [
  { key: 'auto', label: 'Auto', kind: 'chat', blurb: 'Founder IDE picks the best model for the task' },
  { key: 'free', label: 'Free', kind: 'chat', blurb: 'Daily free cloud quota + unlimited local' },
  { key: 'founder-ai', label: 'Founder AI', kind: 'chat', blurb: 'Founder Brain routed model (DeepSeek text + Gemini vision)' },
  { key: 'local', label: 'Local', kind: 'nav', href: '/founder-ide/local', blurb: 'Pair a local llama.cpp / Ollama model' },
  { key: 'byok', label: 'BYOK', kind: 'nav', href: '/founder-ide/byok', blurb: 'Connect your own cloud API keys' },
];

type Props = {
  accessToken: string;
  nodeId: string;
};

const DISPATCH_POLL_INTERVAL_MS = 4000;
const DISPATCH_POLL_TIMEOUT_MS = 120_000;
const REFRESH_INTERVAL_MS = 15_000;
/** Debounce: minimum ms between two mic toggle clicks. Prevents flicker from
 *  rapid toggling that re-initializes SpeechRecognition. */
const MIC_DEBOUNCE_MS = 600;

export function FounderIdeChat({ accessToken, nodeId }: Props) {
  const router = useRouter();
  const [nodes, setNodes] = useState<FounderNodeStatusRow[]>([]);
  const [workspaces, setWorkspaces] = useState<BridgeWorkspace[]>([]);
  const [sessions, setSessions] = useState<BridgeSession[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMsg[]>([]);
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const [aiOpen, setAiOpen] = useState(false);
  const [aiChoice, setAiChoice] = useState<AiOption>(AI_OPTIONS[0]);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [micDenied, setMicDenied] = useState(false);

  const aiDropdownRef = useRef<HTMLDivElement>(null);
  const dispatchPollRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const dispatchStartedAtRef = useRef(0);
  /** Guard against re-entrant mic clicks while SpeechRecognition is spinning
   *  up — the previous toggle handler would otherwise race against the new
   *  one and the underlying Web Speech API repeatedly re-initializes, which
   *  surfaces as the "waiting for internet connection" flicker. */
  const micToggleInFlightRef = useRef(false);
  const micLastToggleAtRef = useRef(0);

  // ── Voice input ──────────────────────────────────────────────────────────
  const onTranscript = useCallback((text: string, isFinal: boolean) => {
    setInput(text);
    if (isFinal) {
      // keep cursor at end so the user can append typed text
    }
  }, []);

  const voice = useVoiceInput(onTranscript);

  // Detect a prior mic denial so we can show a friendly "open settings" prompt.
  useEffect(() => {
    if (typeof navigator === 'undefined' || !navigator.permissions) return;
    let cancelled = false;
    navigator.permissions
      .query({ name: 'microphone' as PermissionName })
      .then((status) => {
        if (cancelled) return;
        if (status.state === 'denied') setMicDenied(true);
        status.onchange = () => {
          setMicDenied(status.state === 'denied');
        };
      })
      .catch(() => {
        // Permissions API unsupported (Firefox/Safari) — fall back to onerror.
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // ── Load status + workspaces + sessions ──────────────────────────────────
  const load = useCallback(async () => {
    try {
      const [status, ws, ss] = await Promise.all([
        fetchFounderNodeStatus(accessToken),
        fetchIdeBridgeWorkspaces(accessToken).catch(() => []),
        fetchIdeBridgeSessions(accessToken).catch(() => []),
      ]);
      setNodes(status.nodes ?? []);
      setWorkspaces(ws);
      setSessions(ss);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load workspace state');
    } finally {
      setLoading(false);
    }
  }, [accessToken]);

  useEffect(() => {
    void load();
    const id = setInterval(() => void load(), REFRESH_INTERVAL_MS);
    return () => clearInterval(id);
  }, [load]);

  // ── Close AI dropdown on outside click ───────────────────────────────────
  useEffect(() => {
    if (!aiOpen) return;
    const handler = (e: MouseEvent) => {
      if (aiDropdownRef.current && !aiDropdownRef.current.contains(e.target as Node)) {
        setAiOpen(false);
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [aiOpen]);

  const stopDispatchPoll = useCallback(() => {
    if (dispatchPollRef.current) {
      clearTimeout(dispatchPollRef.current);
      dispatchPollRef.current = null;
    }
  }, []);

  useEffect(() => () => stopDispatchPoll(), [stopDispatchPoll]);

  // ── Derived state ────────────────────────────────────────────────────────
  const onlineNode = useMemo(
    () => nodes.find((n) => n.status === 'online') ?? nodes[0] ?? null,
    [nodes],
  );
  const isOnline = Boolean(onlineNode?.status === 'online');

  // Group sessions by workspace so the user sees "projects" with chats inside.
  const projects = useMemo(() => {
    const groups = new Map<string, { workspace: BridgeWorkspace | null; sessions: BridgeSession[] }>();
    for (const s of sessions) {
      const ws = workspaces.find(
        (w) =>
          w.id === s.workspaceId ||
          w.id === s.workspaceStorageId ||
          (w.repository && s.repository && w.repository === s.repository),
      );
      const key = ws?.id ?? s.workspaceId ?? s.id;
      const entry = groups.get(key) ?? { workspace: ws ?? null, sessions: [] };
      entry.sessions.push(s);
      groups.set(key, entry);
    }
    // If we have workspaces with no sessions yet, still surface them as projects.
    for (const w of workspaces) {
      if (!groups.has(w.id)) groups.set(w.id, { workspace: w, sessions: [] });
    }
    return Array.from(groups.values());
  }, [sessions, workspaces]);

  const selectedSession = useMemo(
    () => sessions.find((s) => s.id === selectedSessionId) ?? null,
    [sessions, selectedSessionId],
  );

  // ── Dispatch poll ────────────────────────────────────────────────────────
  const pollDispatch = useCallback(
    (dispatchId: string) => {
      stopDispatchPoll();
      dispatchStartedAtRef.current = Date.now();
      const tick = async () => {
        if (Date.now() - dispatchStartedAtRef.current > DISPATCH_POLL_TIMEOUT_MS) {
          setNotice('Delivery timed out — is Founder IDE online?');
          stopDispatchPoll();
          return;
        }
        try {
          const s = await fetchIdeDispatchStatus(accessToken, dispatchId);
          setMessages((prev) =>
            prev.map((m) =>
              m.id === `dispatch:${dispatchId}`
                ? { ...m, status: s.status, pending: s.status === 'PENDING' || s.status === 'DISPATCHING' }
                : m,
            ),
          );
          if (s.delivered) {
            setMessages((prev) =>
              prev.map((m) =>
                m.id === `dispatch:${dispatchId}`
                  ? { ...m, pending: false, status: 'Delivered to Founder IDE' }
                  : m,
              ),
            );
            setNotice('Delivered to Founder IDE.');
            stopDispatchPoll();
            return;
          }
          if (s.failed) {
            setMessages((prev) =>
              prev.map((m) =>
                m.id === `dispatch:${dispatchId}`
                  ? { ...m, pending: false, status: `Failed: ${s.result ?? 'unknown'}` }
                  : m,
              ),
            );
            setNotice(`Delivery failed: ${s.result ?? 'unknown error'}`);
            stopDispatchPoll();
            return;
          }
          dispatchPollRef.current = setTimeout(tick, DISPATCH_POLL_INTERVAL_MS);
        } catch {
          dispatchPollRef.current = setTimeout(tick, DISPATCH_POLL_INTERVAL_MS);
        }
      };
      void tick();
    },
    [accessToken, stopDispatchPoll],
  );

  // ── Send ─────────────────────────────────────────────────────────────────
  const handleSend = useCallback(async () => {
    const text = input.trim();
    if (!text) return;

    // Always a clear, actionable error — never a silently-disabled send.
    if (!isOnline) {
      setError('No Founder Node connected — open the app on your computer and pair it.');
      return;
    }
    if (!selectedSession) {
      setError(
        projects.length === 0
          ? 'No project is open in your Founder IDE yet — open a workspace on your laptop, then try again.'
          : 'Pick a project on the left to dispatch to your Founder IDE.',
      );
      return;
    }
    if (voice.phase !== 'idle') voice.stop();
    setBusy(true);
    setError(null);
    setNotice(null);

    const userMsg: ChatMsg = {
      id: `u:${Date.now()}`,
      role: 'user',
      text,
      at: new Date().toISOString(),
    };
    const dispatchId = `dispatch:${Date.now()}`;
    const pendingMsg: ChatMsg = {
      id: dispatchId,
      role: 'assistant',
      text: `Dispatching to ${selectedSession.title}…`,
      at: new Date().toISOString(),
      pending: true,
      status: 'PENDING',
    };
    setMessages((prev) => [...prev, userMsg, pendingMsg]);
    setInput('');

    try {
      // Retarget dispatch from Cursor → Founder IDE by forcing ideProvider.
      const created = await dispatchToIdeSession(
        accessToken,
        selectedSession.id,
        text,
        'founder-ide',
      );
      pollDispatch(created.id);
    } catch (e) {
      setMessages((prev) =>
        prev.map((m) =>
          m.id === dispatchId
            ? { ...m, pending: false, status: `error: ${e instanceof Error ? e.message : 'failed'}` }
            : m,
        ),
      );
      setNotice(`Dispatch failed: ${e instanceof Error ? e.message : 'unknown error'}`);
    } finally {
      setBusy(false);
    }
  }, [accessToken, input, isOnline, pollDispatch, projects.length, selectedSession, voice]);

  // ── Mic toggle (debounced) ───────────────────────────────────────────────
  const handleMicToggle = useCallback(() => {
    // Debounce: ignore rapid double-clicks that re-init SpeechRecognition.
    const now = Date.now();
    if (now - micLastToggleAtRef.current < MIC_DEBOUNCE_MS) return;
    if (micToggleInFlightRef.current) return;
    micLastToggleAtRef.current = now;
    micToggleInFlightRef.current = true;
    // Release the gate on the next tick — by then the SpeechRecognition
    // start() call has either fired or thrown synchronously.
    window.setTimeout(() => {
      micToggleInFlightRef.current = false;
    }, MIC_DEBOUNCE_MS);

    voice.clearVoiceError();
    setError(null);

    // If we are already listening, just stop — single, deterministic path.
    if (voice.phase !== 'idle') {
      voice.stop();
      return;
    }

    // If we already know the user denied the mic, surface a friendly prompt
    // instead of silently re-requesting.
    if (micDenied) {
      setNotice('Microphone blocked. Click here to allow in browser settings.');
      return;
    }

    if (!voice.supported) {
      setError('Voice needs Chrome or Edge on desktop with microphone permission (HTTPS).');
      return;
    }

    // Probe the permission first so a denial flips micDenied and shows the
    // friendly prompt. The actual SpeechRecognition call still happens after.
    if (navigator.mediaDevices?.getUserMedia) {
      navigator.mediaDevices
        .getUserMedia({ audio: true })
        .then((stream) => {
          // Chrome Web Speech opens its own mic handle — release this probe.
          stream.getTracks().forEach((t) => t.stop());
          voice.start(input);
        })
        .catch((e: DOMException) => {
          if (e.name === 'NotAllowedError' || e.name === 'SecurityError') {
            setMicDenied(true);
            setNotice('Microphone blocked. Click here to allow in browser settings.');
          } else {
            setError(`Microphone error: ${e.message}`);
          }
        });
    } else {
      voice.start(input);
    }
  }, [input, micDenied, voice]);

  function handleAiSelect(opt: AiOption) {
    setAiChoice(opt);
    setAiOpen(false);
    if (opt.kind === 'nav' && opt.href) {
      router.push(opt.href);
    }
  }

  // ───────────────────────────────────────────────────────────────────────
  return (
    <div className='overflow-hidden rounded-2xl border border-zinc-800 bg-zinc-950/40'>
      {/* Connected header */}
      <div className='flex items-center justify-between gap-4 border-b border-zinc-800 bg-zinc-950/60 px-5 py-3.5'>
        <div className='flex items-center gap-2'>
          <span
            className={
              'h-2 w-2 rounded-full ' +
              (isOnline ? 'bg-emerald-400' : 'bg-amber-400') +
              (isOnline ? '' : ' animate-pulse')
            }
          />
          <span className='text-sm font-semibold text-white'>
            {isOnline ? 'Connected' : 'Offline'}
          </span>
          <code className='rounded-md bg-zinc-900 px-2 py-0.5 font-mono text-xs text-emerald-300'>
            {onlineNode?.nodeId?.slice(0, 12) ?? nodeId.slice(0, 12)}
          </code>
          <span className='hidden text-xs text-zinc-500 sm:inline'>
            {onlineNode?.label || 'Founder IDE'}
            {onlineNode?.platform ? ` · ${onlineNode.platform}` : ''}
          </span>
        </div>
        {/* Plan info lives on /pricing now — this is just a thin CTA. */}
        <Link
          href='/pricing'
          className='shrink-0 rounded-xl border border-zinc-700 px-3 py-1.5 text-xs font-semibold text-zinc-200 transition hover:border-violet-500/50 hover:text-white'
        >
          Plans
        </Link>
      </div>

      {!isOnline && (
        <div className='border-b border-amber-500/20 bg-amber-950/15 px-5 py-2.5 text-xs text-amber-200'>
          No Founder Node connected. Open the app on your computer and pair it, then this chat will deliver prompts
          to your IDE.
        </div>
      )}

      <div className='grid grid-cols-1 md:grid-cols-[280px_1fr]'>
        {/* Projects sidebar */}
        <aside className='border-b border-zinc-800 bg-zinc-950/40 p-4 md:border-b-0 md:border-r'>
          <h3 className='mb-2 text-[10px] font-semibold uppercase tracking-[0.18em] text-zinc-500'>
            Open projects
          </h3>
          {loading && <p className='text-xs text-zinc-600'>Loading…</p>}
          {!loading && projects.length === 0 && (
            <div className='rounded-xl border border-zinc-800 bg-zinc-950/40 px-3 py-4 text-xs text-zinc-500'>
              No open projects yet. Open a workspace in Founder IDE on your laptop and it will appear here
              automatically.
            </div>
          )}
          <div className='space-y-1'>
            {projects.map((group) => {
              const label = group.workspace?.title ?? group.sessions[0]?.title ?? 'Untitled project';
              const branch = group.workspace?.branch ?? group.sessions[0]?.branch;
              const id = group.workspace?.id ?? group.sessions[0]?.id;
              return (
                <button
                  key={id}
                  type='button'
                  onClick={() => setSelectedSessionId(group.sessions[0]?.id ?? null)}
                  className={
                    'block w-full rounded-xl px-3 py-2 text-left transition ' +
                    (selectedSessionId && group.sessions.some((s) => s.id === selectedSessionId)
                      ? 'bg-violet-500/10 ring-1 ring-violet-400/30'
                      : 'hover:bg-white/5')
                  }
                >
                  <div className='flex items-center gap-2'>
                    <span
                      className={
                        'h-1.5 w-1.5 shrink-0 rounded-full ' +
                        (group.workspace?.hasActiveAgent ? 'bg-emerald-400' : 'bg-zinc-600')
                      }
                    />
                    <span className='truncate text-sm font-medium text-zinc-100'>{label}</span>
                  </div>
                  <div className='mt-0.5 truncate pl-3.5 text-[0.65rem] text-zinc-500'>
                    {branch ?? group.workspace?.repository ?? `${group.sessions.length} chat(s)`}
                  </div>
                </button>
              );
            })}
          </div>
        </aside>

        {/* Chat panel */}
        <section className='flex min-h-[420px] flex-col'>
          {/* Active session label */}
          <div className='flex items-center justify-between gap-4 border-b border-zinc-800 px-5 py-3'>
            <div className='min-w-0'>
              <p className='truncate text-sm font-semibold text-white'>
                {selectedSession?.title ?? (projects.length ? 'Select a project' : 'No project selected')}
              </p>
              {selectedSession?.repository && (
                <p className='truncate text-[0.65rem] text-zinc-500'>{selectedSession.repository}</p>
              )}
            </div>
            {/* AI dropdown */}
            <div ref={aiDropdownRef} className='relative'>
              <button
                type='button'
                onClick={() => setAiOpen((v) => !v)}
                className='inline-flex items-center gap-1.5 rounded-xl border border-zinc-700 bg-zinc-900 px-3 py-1.5 text-xs font-semibold text-zinc-200 transition hover:border-violet-500/50'
                aria-haspopup='listbox'
                aria-expanded={aiOpen}
                aria-label='Choose AI provider'
              >
                <span className='text-violet-300'>AI:</span>
                <span>{aiChoice.label}</span>
                <svg
                  width='10'
                  height='10'
                  viewBox='0 0 10 10'
                  className={'transition ' + (aiOpen ? 'rotate-180' : '')}
                  aria-hidden='true'
                >
                  <path
                    d='M2 3.5L5 6.5L8 3.5'
                    stroke='currentColor'
                    strokeWidth='1.4'
                    fill='none'
                    strokeLinecap='round'
                    strokeLinejoin='round'
                  />
                </svg>
              </button>
              {aiOpen && (
                <div
                  role='listbox'
                  className='absolute right-0 top-full z-50 mt-1 w-72 overflow-hidden rounded-xl border border-zinc-700 bg-[#0B0B0B] shadow-2xl'
                >
                  <div className='border-b border-zinc-800 px-3 py-2 text-[10px] font-semibold uppercase tracking-[0.18em] text-zinc-500'>
                    Route this chat to…
                  </div>
                  <div className='max-h-80 overflow-y-auto py-1'>
                    {AI_OPTIONS.map((opt) => (
                      <button
                        key={opt.key}
                        type='button'
                        role='option'
                        aria-selected={aiChoice.key === opt.key}
                        onClick={() => handleAiSelect(opt)}
                        className={
                          'flex w-full items-start gap-2 px-3 py-2 text-left transition hover:bg-zinc-900 ' +
                          (aiChoice.key === opt.key ? 'bg-violet-500/10' : '')
                        }
                      >
                        <span className='mt-0.5 text-violet-300'>{opt.kind === 'nav' ? '→' : '•'}</span>
                        <span className='min-w-0 flex-1'>
                          <span className='block text-sm font-medium text-zinc-100'>
                            {opt.label}
                            {opt.kind === 'nav' && (
                              <span className='ml-1.5 rounded bg-zinc-800 px-1.5 py-0.5 text-[9px] uppercase tracking-wide text-zinc-400'>
                                open
                              </span>
                            )}
                          </span>
                          <span className='block truncate text-[11px] text-zinc-500'>{opt.blurb}</span>
                        </span>
                      </button>
                    ))}
                  </div>
                  <div className='border-t border-zinc-800 px-3 py-2 text-[10px] text-zinc-600'>
                    Auto / Free / Founder AI stay in this chat. Local &amp; BYOK open setup pages.
                  </div>
                </div>
              )}
            </div>
          </div>

          {/* Messages */}
          <div className='flex-1 space-y-3 overflow-y-auto px-5 py-4'>
            {messages.length === 0 && (
              <div className='flex h-full flex-col items-center justify-center gap-2 px-6 text-center text-sm text-zinc-600'>
                <p>Send a message — it lands in your Founder IDE chat box and activates the agent.</p>
                <p className='text-xs text-zinc-700'>
                  This is remote control: drive work from your phone without sitting at the computer.
                </p>
              </div>
            )}
            {messages.map((m) => (
              <div key={m.id} className={'flex ' + (m.role === 'user' ? 'justify-end' : 'justify-start')}>
                <div
                  className={
                    'max-w-[80%] rounded-2xl border px-3.5 py-2 text-sm ' +
                    (m.role === 'user'
                      ? 'border-violet-500/30 bg-violet-500/10 text-violet-50'
                      : m.status?.startsWith('Failed') || m.status?.startsWith('error')
                        ? 'border-red-500/30 bg-red-950/20 text-red-200'
                        : 'border-zinc-700/70 bg-zinc-900/70 text-zinc-100')
                  }
                >
                  <p className='whitespace-pre-wrap break-words'>{m.text}</p>
                  {m.status && (
                    <p className='mt-1 text-[10px] text-zinc-500'>
                      {m.pending && (
                        <span className='mr-1 inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-amber-400 align-middle' />
                      )}
                      {m.status}
                    </p>
                  )}
                </div>
              </div>
            ))}
          </div>

          {/* Composer */}
          <div className='border-t border-zinc-800 px-4 py-3 sm:px-5'>
            {error && <div className='mb-2 text-xs text-rose-400'>{error}</div>}
            {notice && (
              <div className='mb-2 text-xs text-amber-300/90'>
                {notice}{' '}
                {micDenied && (
                  <a
                    href='https://support.google.com/chrome/answer/2693767'
                    target='_blank'
                    rel='noreferrer'
                    className='ml-1 underline hover:text-amber-200'
                  >
                    Open mic settings ↗
                  </a>
                )}
              </div>
            )}
            {voice.voiceError && <div className='mb-2 text-xs text-rose-400'>{voice.voiceError}</div>}
            <div className='flex items-end gap-2'>
              <button
                type='button'
                onClick={handleMicToggle}
                className={
                  'flex h-10 w-10 shrink-0 items-center justify-center rounded-xl text-sm transition ' +
                  (voice.listening
                    ? 'bg-red-600 text-white'
                    : voice.waitingNetwork
                      ? 'bg-sky-700/90 text-white'
                      : voice.starting
                        ? 'bg-amber-600/90 text-white'
                        : micDenied
                          ? 'border border-amber-500/50 bg-amber-950/30 text-amber-300'
                          : 'border border-zinc-700 bg-zinc-900 text-zinc-400 hover:text-zinc-100')
                }
                title={
                  micDenied
                    ? 'Microphone blocked — click to learn how to re-enable'
                    : voice.listening
                      ? 'Stop recording'
                      : 'Voice input (speech to text)'
                }
                aria-label={voice.listening ? 'Stop recording' : 'Voice input'}
              >
                {voice.listening ? '⏹' : '🎤'}
              </button>
              <textarea
                value={input}
                onChange={(e) => {
                  if (voice.phase !== 'idle') voice.stop();
                  setInput(e.target.value);
                }}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault();
                    void handleSend();
                  }
                }}
                rows={1}
                placeholder='Message your Founder IDE — dispatched to the open project'
                className='min-h-[40px] flex-1 resize-none rounded-xl border border-zinc-700 bg-zinc-900 px-3.5 py-2 text-sm text-zinc-100 placeholder:text-zinc-600 focus:border-violet-500/60 focus:outline-none focus:ring-1 focus:ring-violet-500/40'
              />
              <button
                type='button'
                onClick={() => void handleSend()}
                disabled={busy || !input.trim()}
                className='min-h-[40px] rounded-xl bg-violet-600 px-5 py-2 text-sm font-semibold text-white transition hover:bg-violet-500 disabled:opacity-40 disabled:hover:bg-violet-600'
              >
                {busy ? 'Sending…' : 'Send'}
              </button>
            </div>
            {(voice.listening || voice.starting || voice.waitingNetwork) && (
              <div className='mt-1.5 flex items-center gap-2 text-[0.65rem] text-zinc-500'>
                <VoiceWaveform phase={voice.phase} level={voice.audioLevel} />
                <span>
                  {voice.listening
                    ? 'Listening — speak now'
                    : voice.waitingNetwork
                      ? 'Waiting for network — your typed text is safe.'
                      : 'Starting microphone…'}
                </span>
                <button
                  type='button'
                  onClick={() => voice.stop()}
                  className='ml-auto text-zinc-400 underline hover:text-zinc-200'
                >
                  Stop
                </button>
              </div>
            )}
          </div>
        </section>
      </div>
    </div>
  );
}
