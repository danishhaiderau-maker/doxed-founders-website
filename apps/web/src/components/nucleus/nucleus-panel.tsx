'use client';

import { useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import {
  NUCLEUS_GATEWAY_MODEL,
  buildNucleusContextPacket,
  buildNucleusGatewayMessages,
  extractCompletionText,
  extractSseCompletionText,
  formatNucleusPacketForPrompt,
  formatNucleusPacketVisible,
  projectLiveNucleusGraph,
  type NucleusContextPacket,
  type NucleusGraph,
} from '@dcf/utils';
import { fetchIdeNucleus } from '@/lib/api';
import { apiUrl } from '@/lib/api-base';
import { NucleusMap } from './nucleus-map';

type Turn = {
  id: string;
  role: 'user' | 'assistant' | 'context';
  text: string;
};

type Props = {
  accessToken: string;
};

let turnSeq = 0;
function nextTurnId(): string {
  turnSeq += 1;
  return `nucleus-turn-${turnSeq}`;
}

async function readAssistantText(res: Response): Promise<string> {
  const contentType = res.headers.get('content-type') ?? '';
  if (contentType.includes('text/event-stream')) {
    return extractSseCompletionText(await res.text());
  }
  const payload: unknown = await res.json().catch(() => null);
  return extractCompletionText(payload);
}

/**
 * Nucleus on the web. Graph load uses the session JWT against
 * `GET /api/ide/nucleus`. Chat uses that same JWT against
 * `POST /api/v1/chat/phone-completions` with model `founder-os-auto`
 * (existing gateway Auto tier). Nothing is written to browser storage.
 */
export function NucleusPanel({ accessToken }: Props) {
  const [graph, setGraph] = useState<NucleusGraph | null>(null);
  const [auth, setAuth] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [packet, setPacket] = useState<NucleusContextPacket | null>(null);
  const [draft, setDraft] = useState('Apply the intent at this delivery address.');
  const [turns, setTurns] = useState<Turn[]>([]);
  const [busy, setBusy] = useState(false);
  const [chatError, setChatError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const body = await fetchIdeNucleus(accessToken);
      setGraph(projectLiveNucleusGraph(body.graph));
      setAuth(body.auth ?? 'jwt');
    } catch (err) {
      setGraph(null);
      setError(err instanceof Error ? err.message : 'Could not load the Founder Graph.');
    } finally {
      setLoading(false);
    }
  }, [accessToken]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (!packet) return;
    document.getElementById('nucleus-chat')?.scrollIntoView({ block: 'nearest' });
  }, [packet]);

  const selectNode = useCallback(
    (nodeId: string) => {
      if (!graph) return;
      const next = buildNucleusContextPacket(projectLiveNucleusGraph(graph), nodeId);
      setSelectedId(nodeId);
      setPacket(next);
      setChatError(null);
      if (!next) return;
      setTurns((prev) => [
        ...prev,
        { id: nextTurnId(), role: 'context', text: formatNucleusPacketVisible(next) },
      ]);
    },
    [graph],
  );

  const send = useCallback(async () => {
    if (!packet || busy) return;
    const question = draft.trim();
    if (!question) return;
    const messages = buildNucleusGatewayMessages({ packet, userText: question });
    setBusy(true);
    setChatError(null);
    setTurns((prev) => [...prev, { id: nextTurnId(), role: 'user', text: question }]);
    try {
      const res = await fetch(apiUrl('/v1/chat/phone-completions'), {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${accessToken}`,
          'Content-Type': 'application/json',
          Accept: 'application/json, text/event-stream',
        },
        body: JSON.stringify({
          model: NUCLEUS_GATEWAY_MODEL,
          stream: false,
          founder_os_metadata: true,
          messages,
        }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        const message =
          body &&
          typeof body === 'object' &&
          'error' in body &&
          body.error &&
          typeof body.error === 'object' &&
          'message' in body.error &&
          typeof (body.error as { message?: unknown }).message === 'string'
            ? (body.error as { message: string }).message
            : `Gateway returned ${res.status}`;
        throw new Error(message);
      }
      const text = (await readAssistantText(res)).trim();
      setTurns((prev) => [
        ...prev,
        {
          id: nextTurnId(),
          role: 'assistant',
          text: text || 'The gateway returned an empty reply.',
        },
      ]);
    } catch (err) {
      setChatError(err instanceof Error ? err.message : 'Chat request failed.');
    } finally {
      setBusy(false);
    }
  }, [accessToken, busy, draft, packet]);

  return (
    <div className="grid gap-6 lg:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)]">
      <section className="rounded-2xl border border-zinc-800 bg-zinc-950/60 p-4">
        <div className="mb-3 flex items-center justify-between gap-3">
          <div>
            <h2 className="text-sm font-semibold text-white">Founder Graph</h2>
            <p className="text-xs text-zinc-500">
              {loading
                ? 'Loading…'
                : graph
                  ? `${graph.nodes?.length ?? 0} live nodes · auth ${auth ?? 'jwt'}`
                  : 'No graph'}
            </p>
          </div>
          <button
            type="button"
            onClick={() => void load()}
            className="rounded-lg border border-zinc-700 px-3 py-1.5 text-xs text-zinc-200 hover:border-zinc-500"
          >
            Refresh
          </button>
        </div>
        {error && (
          <p role="alert" className="mb-3 text-sm text-red-300">
            {error}
          </p>
        )}
        {!loading && graph && (graph.nodes?.length ?? 0) > 0 && (
          <NucleusMap graph={graph} selectedId={selectedId} onSelect={selectNode} />
        )}
        {!loading && graph && (graph.nodes?.length ?? 0) === 0 && (
          <p className="py-16 text-center text-sm text-zinc-500">
            No Founder Graph nodes yet. Pair a project and refresh.
          </p>
        )}
      </section>

      <section id="nucleus-chat" className="flex min-h-[28rem] flex-col rounded-2xl border border-zinc-800 bg-zinc-950/60 p-4">
        <div className="mb-3">
          <h2 className="text-sm font-semibold text-white">Chat</h2>
          <p className="text-xs text-zinc-500">
            Gateway model <span className="text-violet-300">{NUCLEUS_GATEWAY_MODEL}</span>
            {' · '}session JWT via /api/v1/chat/phone-completions
          </p>
        </div>

        {packet ? (
          <div className="mb-3 rounded-xl border border-violet-500/30 bg-violet-950/20 p-3">
            <p className="text-[11px] font-semibold uppercase tracking-wide text-violet-300">
              Node context
            </p>
            <pre className="mt-2 whitespace-pre-wrap font-sans text-xs leading-5 text-zinc-200">
              {formatNucleusPacketVisible(packet)}
            </pre>
            <details className="mt-2">
              <summary className="cursor-pointer text-[11px] text-zinc-500">
                Prompt block sent with {NUCLEUS_GATEWAY_MODEL}
              </summary>
              <pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap font-mono text-[11px] text-zinc-400">
                {formatNucleusPacketForPrompt(packet)}
              </pre>
            </details>
          </div>
        ) : (
          <p className="mb-3 text-sm text-zinc-500">
            Click a live node. Chat opens with its delivery address: file, symbol, line range, and intent.
          </p>
        )}

        <div className="flex-1 space-y-2 overflow-auto">
          {turns.map((turn) => (
            <div
              key={turn.id}
              className={
                turn.role === 'user'
                  ? 'rounded-lg bg-zinc-800/80 px-3 py-2 text-sm text-zinc-100'
                  : turn.role === 'assistant'
                    ? 'rounded-lg border border-zinc-800 px-3 py-2 text-sm text-zinc-200'
                    : 'rounded-lg border border-violet-500/20 px-3 py-2 text-xs text-violet-100'
              }
            >
              <p className="mb-1 text-[10px] uppercase tracking-wide text-zinc-500">
                {turn.role === 'context' ? 'injected context' : turn.role}
              </p>
              <pre className="whitespace-pre-wrap font-sans">{turn.text}</pre>
            </div>
          ))}
        </div>

        {chatError && (
          <p role="alert" className="mt-2 text-xs text-red-300">
            {chatError}
          </p>
        )}

        <form
          className="mt-3 flex gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            void send();
          }}
        >
          <label className="sr-only" htmlFor="nucleus-question">
            Question for the selected node
          </label>
          <input
            id="nucleus-question"
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            disabled={!packet || busy}
            className="min-w-0 flex-1 rounded-xl border border-zinc-700 bg-black/40 px-3 py-2 text-sm text-white outline-none focus:border-violet-500 disabled:opacity-50"
          />
          <button
            type="submit"
            disabled={!packet || busy}
            className="rounded-xl bg-violet-600 px-4 py-2 text-sm font-semibold text-white hover:bg-violet-500 disabled:opacity-50"
          >
            {busy ? 'Asking…' : 'Ask'}
          </button>
        </form>
        <p className="mt-3 text-[11px] text-zinc-600">
          In Founder IDE or Cursor, the same packet is injected by the Nucleus sidebar.{' '}
          <Link href="/founder-ide" className="text-zinc-400 underline-offset-2 hover:underline">
            Back to Founder IDE
          </Link>
        </p>
      </section>
    </div>
  );
}
